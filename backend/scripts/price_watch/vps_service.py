import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


CONTRACT = "expense-price-watch/v1"
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_PRODUCTS = 5000
DEFAULT_HOME = Path(__file__).resolve().parent
DEFAULT_DATABASE = DEFAULT_HOME / "state" / "price_watch.sqlite3"
DEFAULT_PROFILE = DEFAULT_HOME / "state" / "walmart-browser-profile"
DEFAULT_AUTH_PROFILE = Path(
    "/home/ledger/apps/expense-collector/profiles/walmart_ca"
)
MY_ITEMS_URL = "https://www.walmart.ca/en/my-items"
DEFAULT_SCHEDULE_STATUS = DEFAULT_HOME / "state" / "schedule-status.json"
DEFAULT_MANUAL_TRIGGER = DEFAULT_HOME / "state" / "manual-trigger.json"
STORE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
PRODUCT_KEY_PATTERN = re.compile(r"^walmart-ca:(?:\d{6,}|url:[a-f0-9]{24})$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BLOCK_PATTERNS = (
    "verify your identity",
    "real shoppers, not robots",
    "press and hold",
    "access denied",
)


class ServiceError(RuntimeError):
    pass


class CollectionBlocked(ServiceError):
    pass


class WrongStore(ServiceError):
    pass


class LoginRequired(ServiceError):
    pass


class HomeExitOffline(ServiceError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError):
        return default


def parse_timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def canonicalize_product_url(value):
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"}:
        return ""
    if (parsed.hostname or "").lower() not in {"walmart.ca", "www.walmart.ca"}:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "").rstrip("/")
    if "/ip/" not in path.lower():
        return ""
    return urlunsplit(("https", "www.walmart.ca", path, "", ""))


def optional_price(value, field):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ServiceError(f"{field} must be a non-negative number or null.")
    value = round(float(value), 2)
    if value < 0 or value > 1_000_000:
        raise ServiceError(f"{field} is outside the accepted range.")
    return value


def validate_text(value, field, maximum, required=False):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not value:
        raise ServiceError(f"{field} is required.")
    if len(value) > maximum:
        raise ServiceError(f"{field} is too long.")
    return value


def validate_product(raw):
    if not isinstance(raw, dict):
        raise ServiceError("Each product must be an object.")
    key = validate_text(raw.get("product_key"), "product_key", 80, required=True)
    if not PRODUCT_KEY_PATTERN.fullmatch(key):
        raise ServiceError("product_key is invalid.")
    url = canonicalize_product_url(raw.get("url"))
    if not url:
        raise ServiceError("Product URL is not a canonical walmart.ca item URL.")
    expected_numeric_id = re.search(r"/(\d{6,})$", urlsplit(url).path)
    if expected_numeric_id and key != f"walmart-ca:{expected_numeric_id.group(1)}":
        raise ServiceError("product_key does not match the product URL.")
    name = validate_text(raw.get("name"), "name", 300, required=True)
    first_date = validate_text(
        raw.get("first_purchased_on"), "first_purchased_on", 10
    )
    last_date = validate_text(
        raw.get("last_purchased_on"), "last_purchased_on", 10
    )
    for field, value in (
        ("first_purchased_on", first_date),
        ("last_purchased_on", last_date),
    ):
        if value and not DATE_PATTERN.fullmatch(value):
            raise ServiceError(f"{field} must use YYYY-MM-DD.")
    if first_date and last_date and first_date > last_date:
        raise ServiceError("first_purchased_on cannot follow last_purchased_on.")
    currency = validate_text(raw.get("currency") or "CAD", "currency", 3)
    if currency != "CAD":
        raise ServiceError("Only CAD products are accepted.")
    return {
        "product_key": key,
        "name": name,
        "url": url,
        "first_purchased_on": first_date,
        "last_purchased_on": last_date,
        "last_purchase_price": optional_price(
            raw.get("last_purchase_price"), "last_purchase_price"
        ),
        "currency": currency,
    }


def validate_home_proxy(value):
    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise ServiceError("Home-exit proxy URL is invalid.") from exc
    if (
        parsed.scheme != "socks5"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1024 <= port <= 65535
    ):
        raise ServiceError(
            "Home-exit proxy must be a loopback-only socks5 URL."
        )
    return f"socks5://127.0.0.1:{port}"


def probe_socks_proxy(proxy, destination="www.walmart.ca", port=443, timeout=8):
    proxy = validate_home_proxy(proxy)
    parsed = urlsplit(proxy)
    try:
        with socket.create_connection(
            (parsed.hostname, parsed.port), timeout=timeout
        ) as connection:
            connection.settimeout(timeout)
            connection.sendall(b"\x05\x01\x00")
            if connection.recv(2) != b"\x05\x00":
                raise HomeExitOffline(
                    "The home-exit SOCKS tunnel rejected authentication."
                )
            encoded_host = destination.encode("idna")
            if len(encoded_host) > 255:
                raise ServiceError("Proxy probe destination is invalid.")
            connection.sendall(
                b"\x05\x01\x00\x03"
                + bytes([len(encoded_host)])
                + encoded_host
                + int(port).to_bytes(2, "big")
            )
            reply = connection.recv(4)
            if len(reply) != 4 or reply[0] != 5 or reply[1] != 0:
                raise HomeExitOffline(
                    "The home-exit tunnel could not reach Walmart."
                )
            address_type = reply[3]
            if address_type == 1:
                remaining = 6
            elif address_type == 4:
                remaining = 18
            elif address_type == 3:
                length = connection.recv(1)
                if len(length) != 1:
                    raise HomeExitOffline("The SOCKS proxy reply was incomplete.")
                remaining = length[0] + 2
            else:
                raise HomeExitOffline("The SOCKS proxy reply was invalid.")
            while remaining:
                chunk = connection.recv(remaining)
                if not chunk:
                    raise HomeExitOffline("The SOCKS proxy reply was incomplete.")
                remaining -= len(chunk)
    except HomeExitOffline:
        raise
    except (OSError, TimeoutError) as exc:
        raise HomeExitOffline(
            "The home-exit tunnel is offline or unreachable."
        ) from exc
    return {
        "contract": CONTRACT,
        "status": "ready",
        "route": "home_exit",
    }


def connect_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            store_id TEXT NOT NULL,
            product_key TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            first_purchased_on TEXT,
            last_purchased_on TEXT,
            last_purchase_price REAL,
            currency TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (store_id, product_key)
        );
        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY,
            store_id TEXT NOT NULL,
            received_at TEXT NOT NULL,
            product_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_checks (
            store_id TEXT NOT NULL,
            product_key TEXT NOT NULL,
            status TEXT NOT NULL,
            regular_price REAL,
            current_price REAL,
            on_sale INTEGER NOT NULL DEFAULT 0,
            checked_at TEXT NOT NULL,
            PRIMARY KEY (store_id, product_key),
            FOREIGN KEY (store_id, product_key)
                REFERENCES products(store_id, product_key)
                ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS collection_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            route TEXT NOT NULL DEFAULT 'direct',
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            checked_count INTEGER NOT NULL DEFAULT 0,
            sale_count INTEGER NOT NULL DEFAULT 0,
            message TEXT
        );
        CREATE TABLE IF NOT EXISTS collection_progress (
            store_id TEXT NOT NULL,
            route TEXT NOT NULL,
            cycle_started_at TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            PRIMARY KEY (store_id, route)
        );
        CREATE TABLE IF NOT EXISTS my_items_deals (
            store_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            regular_price REAL NOT NULL,
            current_price REAL NOT NULL,
            checked_at TEXT NOT NULL,
            PRIMARY KEY (store_id, source_name)
        );
        CREATE TABLE IF NOT EXISTS my_items_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            route TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            item_count INTEGER NOT NULL DEFAULT 0,
            sale_count INTEGER NOT NULL DEFAULT 0,
            message TEXT
        );
        """
    )
    run_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(collection_runs)")
    }
    if "route" not in run_columns:
        conn.execute(
            "ALTER TABLE collection_runs "
            "ADD COLUMN route TEXT NOT NULL DEFAULT 'direct'"
        )
    return conn


def read_stdin_json():
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ServiceError("Input exceeded the service size limit.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError("Input must be one valid UTF-8 JSON object.") from exc
    if not isinstance(payload, dict):
        raise ServiceError("Input must be a JSON object.")
    return payload


def ingest_watchlist(database, payload):
    if payload.get("contract") != CONTRACT:
        raise ServiceError("Unsupported watchlist contract.")
    batch_id = validate_text(payload.get("batch_id"), "batch_id", 64, required=True)
    if not SHA256_PATTERN.fullmatch(batch_id):
        raise ServiceError("batch_id must be a lowercase SHA-256 digest.")
    store_id = validate_text(payload.get("store_id"), "store_id", 64, required=True)
    if not STORE_ID_PATTERN.fullmatch(store_id):
        raise ServiceError("store_id is invalid.")
    raw_products = payload.get("products")
    if not isinstance(raw_products, list) or not 1 <= len(raw_products) <= MAX_PRODUCTS:
        raise ServiceError("products must contain between 1 and 5000 records.")
    products = [validate_product(raw) for raw in raw_products]
    if len({product["product_key"] for product in products}) != len(products):
        raise ServiceError("products contains duplicate product_key values.")
    now = utc_now()
    with closing(connect_database(database)) as conn, conn:
        replay = conn.execute(
            "SELECT product_count FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if replay:
            return {
                "contract": CONTRACT,
                "accepted": replay["product_count"],
                "created": 0,
                "updated": 0,
                "unchanged": replay["product_count"],
                "replayed": True,
            }
        created = updated = unchanged = 0
        for product in products:
            existing = conn.execute(
                """
                SELECT name, url, first_purchased_on, last_purchased_on,
                       last_purchase_price, currency
                FROM products
                WHERE store_id = ? AND product_key = ?
                """,
                (store_id, product["product_key"]),
            ).fetchone()
            comparable = (
                product["name"],
                product["url"],
                product["first_purchased_on"],
                product["last_purchased_on"],
                product["last_purchase_price"],
                product["currency"],
            )
            if existing and tuple(existing) == comparable:
                unchanged += 1
                continue
            if existing:
                updated += 1
                conn.execute(
                    """
                    UPDATE products
                    SET name = ?, url = ?, first_purchased_on = ?,
                        last_purchased_on = ?, last_purchase_price = ?,
                        currency = ?, updated_at = ?
                    WHERE store_id = ? AND product_key = ?
                    """,
                    comparable + (now, store_id, product["product_key"]),
                )
            else:
                created += 1
                conn.execute(
                    """
                    INSERT INTO products (
                        store_id, product_key, name, url, first_purchased_on,
                        last_purchased_on, last_purchase_price, currency,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        store_id,
                        product["product_key"],
                        *comparable,
                        now,
                        now,
                    ),
                )
        conn.execute(
            """
            INSERT INTO batches (batch_id, store_id, received_at, product_count)
            VALUES (?, ?, ?, ?)
            """,
            (batch_id, store_id, now, len(products)),
        )
    return {
        "contract": CONTRACT,
        "accepted": len(products),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "replayed": False,
    }


def parse_price_token(value):
    token = re.sub(r"\s+", " ", str(value or "")).strip()
    cents = re.fullmatch(r"([0-9]{1,3})\s*¢", token)
    if cents:
        return round(int(cents.group(1)) / 100, 2)
    dollars = re.fullmatch(
        r"\$\s*([0-9]{1,6}(?:,[0-9]{3})*)(?:[.\s]([0-9]{2}))?",
        token,
    )
    if not dollars:
        return None
    whole = int(dollars.group(1).replace(",", ""))
    fraction = int(dollars.group(2) or "0") / 100
    return round(whole + fraction, 2)


def labeled_price(text, labels):
    token_pattern = (
        r"(\$\s*[0-9]{1,6}(?:,[0-9]{3})*"
        r"(?:[.\s][0-9]{2})?|[0-9]{1,3}\s*¢)"
    )
    match = re.search(
        rf"(?:{labels})\s*(?:CAD\s*)?{token_pattern}",
        text,
        re.IGNORECASE,
    )
    return parse_price_token(match.group(1)) if match else None


def extract_json_ld_price(page):
    prices = []
    try:
        scripts = page.css('script[type="application/ld+json"]::text').getall()
    except Exception:
        return None

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            offer = value.get("offers")
            if isinstance(offer, dict):
                price = offer.get("price")
                if isinstance(price, (int, float, str)):
                    try:
                        numeric = round(float(price), 2)
                    except (TypeError, ValueError):
                        numeric = None
                    if numeric is not None and 0 <= numeric <= 1_000_000:
                        prices.append(numeric)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)

    for script in scripts:
        try:
            visit(json.loads(str(script)))
        except json.JSONDecodeError:
            continue
    return prices[0] if prices else None


def normalized_store_name(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


def store_name_present(text, expected_store_name):
    expected = normalized_store_name(expected_store_name)
    return bool(expected and expected in normalized_store_name(text))


def extract_price_facts(page, expected_store_name):
    status = int(getattr(page, "status", 0) or 0)
    final_url = str(getattr(page, "url", "") or "")
    text = str(page.get_all_text(separator="\n", strip=True))
    lowered = text.lower()
    if (
        status in {403, 429, 456}
        or "/blocked" in final_url.lower()
        or any(pattern in lowered for pattern in BLOCK_PATTERNS)
    ):
        raise CollectionBlocked("Walmart returned a verification or blocked page.")
    if status and not 200 <= status < 400:
        raise ServiceError(f"Walmart returned HTTP {status}.")
    if not store_name_present(text, expected_store_name):
        raise WrongStore("The product page did not confirm the configured store.")
    segment = re.split(
        r"\b(?:similar items|customers also|you may also like|more items to consider)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    compact = re.sub(r"\s+", " ", segment)
    current = labeled_price(
        compact,
        r"current price is|now(?: price)?|sale price",
    )
    regular = labeled_price(
        compact,
        r"was(?: price)?|regular price|original price|list price|previous price",
    )
    if current is None:
        current = extract_json_ld_price(page)
    unavailable = bool(
        re.search(
            r"\b(?:currently out of stock|out of stock|not available at this store)\b",
            compact,
            re.IGNORECASE,
        )
    )
    if current is None and not unavailable:
        raise ServiceError("The product page did not expose a primary price.")
    on_sale = bool(
        current is not None and regular is not None and current < regular
    )
    return {
        "status": "unavailable" if unavailable and current is None else "ok",
        "regular_price": regular,
        "current_price": current,
        "on_sale": on_sale,
    }


def parse_my_items_text(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    money_source = (
        r"\$\s*[0-9]{1,6}(?:,[0-9]{3})*(?:[.\s][0-9]{2})?"
        r"|[0-9]{1,3}\s*¢"
    )
    was = re.search(rf"\bwas\s+({money_source})", text, re.IGNORECASE)
    if not was:
        return None
    before_was = text[:was.start()].strip()
    current_matches = list(
        re.finditer(money_source, before_was, re.IGNORECASE)
    )
    if not current_matches:
        return None
    current_match = current_matches[-1]
    current = parse_price_token(current_match.group(0))
    regular = parse_price_token(was.group(1))
    source_name = re.sub(
        r"\b(?:current price is|now)\s*$",
        "",
        before_was[:current_match.start()],
        flags=re.IGNORECASE,
    )
    source_name = re.sub(r"[,\s·-]+$", "", source_name).strip()
    if (
        not source_name
        or current is None
        or regular is None
        or current >= regular
    ):
        return None
    return {
        "source_name": source_name[:300],
        "current_price": current,
        "regular_price": regular,
    }


def selector_text(selector):
    try:
        return str(selector.get_all_text(separator=" ", strip=True))
    except Exception:
        try:
            return str(selector.text or "")
        except Exception:
            return ""


def extract_my_items_facts(page, expected_store_name):
    status = int(getattr(page, "status", 0) or 0)
    final_url = str(getattr(page, "url", "") or "")
    text = str(page.get_all_text(separator="\n", strip=True))
    lowered = text.lower()
    if (
        status in {403, 429, 456}
        or "/blocked" in final_url.lower()
        or any(pattern in lowered for pattern in BLOCK_PATTERNS)
    ):
        raise CollectionBlocked("Walmart returned a verification or blocked page.")
    if status and not 200 <= status < 400:
        raise ServiceError(f"Walmart returned HTTP {status}.")
    if "/en/my-items" not in final_url.lower():
        raise LoginRequired("The Walmart My Items session requires login.")
    if not store_name_present(text, expected_store_name):
        raise WrongStore("The My Items page did not confirm the configured store.")
    anchors = page.css(
        "#maincontent > main > section:nth-child(2) a"
    )
    labels = [
        re.sub(r"\s+", " ", selector_text(anchor)).strip()
        for anchor in anchors
    ]
    item_labels = [
        label for label in labels
        if label and re.search(r"(?:\$|¢)", label)
    ]
    if not item_labels:
        raise ServiceError("The My Items page exposed no purchased-item prices.")
    deals = {}
    for label in item_labels:
        parsed = parse_my_items_text(label)
        if parsed:
            deals[parsed["source_name"].casefold()] = parsed
    return {
        "item_count": len(item_labels),
        "deals": [
            deals[key]
            for key in sorted(deals)
        ],
    }


def settle_my_items_page(page, maximum_scrolls=12):
    page.wait_for_timeout(8_000)
    previous = -1
    stable = 0
    for _ in range(maximum_scrolls):
        state = page.evaluate(
            """
            () => {
              const root =
                document.querySelector(
                  "#maincontent > main > section:nth-child(2)"
                ) ||
                document.querySelector("#maincontent") ||
                document.body;
              const count = [...root.querySelectorAll("a")].filter(
                (anchor) => /(?:\\$|¢)/.test(
                  anchor.innerText || anchor.textContent || ""
                )
              ).length;
              window.scrollTo(0, document.body.scrollHeight);
              return {
                count,
                atBottom:
                  window.innerHeight + window.scrollY >=
                  document.body.scrollHeight - 8,
              };
            }
            """
        )
        count = int((state or {}).get("count", 0))
        stable = stable + 1 if count == previous else 0
        previous = count
        if stable >= 2 and (state or {}).get("atBottom"):
            break
        page.wait_for_timeout(800)
    page.evaluate("() => window.scrollTo(0, 0)")


def latest_my_items_run(conn, store_id, route=None):
    query = """
        SELECT status, completed_at, message, route, item_count, sale_count
        FROM my_items_runs
        WHERE store_id = ?
    """
    parameters = [store_id]
    if route:
        query += " AND route = ?"
        parameters.append(route)
    query += " ORDER BY id DESC LIMIT 1"
    return conn.execute(query, parameters).fetchone()


def run_my_items_collection(
    database,
    store_id,
    store_name,
    profile=DEFAULT_AUTH_PROFILE,
    route="direct",
    proxy="",
    cooldown_seconds=21600,
    force=False,
    fetch_page=None,
    proxy_probe=probe_socks_proxy,
):
    if not STORE_ID_PATTERN.fullmatch(store_id):
        raise ServiceError("store_id is invalid.")
    store_name = validate_text(store_name, "store_name", 200, required=True)
    if route not in {"direct", "home_exit"}:
        raise ServiceError("Collection route is invalid.")
    if route == "home_exit":
        proxy = validate_home_proxy(proxy)
    elif proxy:
        raise ServiceError("A proxy is allowed only for the home_exit route.")
    started_at = utc_now()
    with closing(connect_database(database)) as conn:
        previous = latest_my_items_run(conn, store_id, route)
        if previous and previous["status"] == "blocked" and not force:
            previous_at = parse_timestamp(previous["completed_at"])
            next_retry = (
                previous_at + timedelta(seconds=cooldown_seconds)
                if previous_at
                else None
            )
            if next_retry and datetime.now(timezone.utc) < next_retry:
                return {
                    "contract": CONTRACT,
                    "status": "cooldown",
                    "checked": 0,
                    "deals": 0,
                    "route": route,
                    "next_retry_at": next_retry.isoformat(),
                }
        with conn:
            run_id = conn.execute(
                """
                INSERT INTO my_items_runs (
                    store_id, route, status, started_at
                ) VALUES (?, ?, 'running', ?)
                """,
                (store_id, route, started_at),
            ).lastrowid
        session = None
        run_status = "completed"
        message = ""
        facts = {"item_count": 0, "deals": []}
        try:
            if route == "home_exit":
                proxy_probe(proxy)
            profile = Path(profile)
            if (
                fetch_page is None
                and not (profile / ".authenticated.json").is_file()
            ):
                raise LoginRequired(
                    "The dedicated Walmart VPS session requires login."
                )
            if fetch_page is None:
                try:
                    from scrapling.fetchers import DynamicSession
                except ImportError as exc:
                    raise ServiceError(
                        "Scrapling fetchers are not installed in this runtime."
                    ) from exc
                session_options = {
                    "headless": True,
                    "user_data_dir": str(profile.resolve()),
                    "locale": "en-CA",
                    "timezone_id": "America/Toronto",
                    "max_pages": 1,
                    "timeout": 60_000,
                    "wait": 2_000,
                    "network_idle": False,
                    "extra_flags": ["--disable-dev-shm-usage"],
                }
                if proxy:
                    session_options["proxy"] = proxy
                session = DynamicSession(**session_options)
                session.start()
                fetch_page = session.fetch
            page = fetch_page(
                MY_ITEMS_URL,
                timeout=60_000,
                wait=2_000,
                network_idle=False,
                page_action=settle_my_items_page,
            )
            facts = extract_my_items_facts(page, store_name)
            checked_at = utc_now()
            with conn:
                conn.execute(
                    "DELETE FROM my_items_deals WHERE store_id = ?",
                    (store_id,),
                )
                conn.executemany(
                    """
                    INSERT INTO my_items_deals (
                        store_id, source_name, regular_price,
                        current_price, checked_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            store_id,
                            row["source_name"],
                            row["regular_price"],
                            row["current_price"],
                            checked_at,
                        )
                        for row in facts["deals"]
                    ],
                )
        except CollectionBlocked as exc:
            run_status = "blocked"
            message = str(exc)
        except LoginRequired as exc:
            run_status = "login_required"
            message = str(exc)
        except WrongStore as exc:
            run_status = "wrong_store"
            message = str(exc)
        except HomeExitOffline as exc:
            run_status = "home_exit_offline"
            message = str(exc)
        except Exception as exc:
            run_status = "failed"
            message = f"My Items collection failed: {type(exc).__name__}."
        finally:
            if session is not None:
                session.close()
            completed_at = utc_now()
            with conn:
                conn.execute(
                    """
                    UPDATE my_items_runs
                    SET status = ?, completed_at = ?, item_count = ?,
                        sale_count = ?, message = ?
                    WHERE id = ?
                    """,
                    (
                        run_status,
                        completed_at,
                        facts["item_count"] if run_status == "completed" else 0,
                        len(facts["deals"]) if run_status == "completed" else 0,
                        validate_text(message, "message", 300),
                        run_id,
                    ),
                )
        return {
            "contract": CONTRACT,
            "status": run_status,
            "checked": (
                facts["item_count"] if run_status == "completed" else 0
            ),
            "deals": (
                len(facts["deals"]) if run_status == "completed" else 0
            ),
            "route": route,
            "completed_at": completed_at,
        }


def dismiss_obstructing_dialogs(page):
    for _ in range(3):
        overlays = page.locator('[class*="OverlayScrim_scrim"]')
        before = overlays.count()
        if not before:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        if page.locator('[class*="OverlayScrim_scrim"]').count() < before:
            continue
        clicked = False
        for selector in (
            'button[aria-label="close button"]',
            'button[aria-label="Close dialogue"]',
        ):
            close = page.locator(selector).last
            if close.is_visible():
                close.click(timeout=3_000, force=True)
                clicked = True
                page.wait_for_timeout(500)
                break
        if not clicked:
            return


def find_matching_store_radio(page, expected_store_name):
    radios = page.locator('input[name="pickup-store"]')
    expected = normalized_store_name(expected_store_name)
    for index in range(radios.count()):
        radio = radios.nth(index)
        label = radio.locator("xpath=ancestor::label[1]")
        try:
            label_text = label.inner_text()
        except Exception:
            label_text = ""
        if expected and expected in normalized_store_name(label_text):
            return radio
    return None


def ensure_store_selection(page, expected_store_name, store_query):
    page.wait_for_timeout(750)
    dismiss_obstructing_dialogs(page)
    body_text = page.locator("body").inner_text()
    if store_name_present(body_text, expected_store_name):
        return
    change_store = page.locator('button[aria-label$=", change store"]').first
    if not change_store.is_visible():
        raise WrongStore("The current store control was not available.")
    change_store.click(timeout=8_000)
    search = page.locator('input[name="zipCodeOrCityState"]').first
    search.wait_for(state="visible", timeout=8_000)
    search.fill(store_query)
    search.press("Enter")
    page.wait_for_timeout(3_500)
    target = find_matching_store_radio(page, expected_store_name)
    if target is None:
        search_button = page.locator('button[aria-label="Search"]').last
        if search_button.is_visible():
            search_button.click()
            page.wait_for_timeout(3_500)
            target = find_matching_store_radio(page, expected_store_name)
        if target is None:
            raise WrongStore("The configured store was not found in the selector.")
    target.check()
    save = page.get_by_role("button", name="Save", exact=True)
    if not save.is_visible():
        raise WrongStore("The store selector did not expose its Save action.")
    save.click()
    page.wait_for_timeout(2_000)
    close = page.locator(
        'button[aria-label="Close, how do you want your items?"]'
    ).first
    if close.is_visible():
        close.click()
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2_500)
    body_text = page.locator("body").inner_text()
    if not store_name_present(body_text, expected_store_name):
        raise WrongStore("The configured store was not retained after reload.")


def latest_run(conn, store_id, route=None):
    if route:
        return conn.execute(
            """
            SELECT status, completed_at, message, route
            FROM collection_runs
            WHERE store_id = ? AND route = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (store_id, route),
        ).fetchone()
    return conn.execute(
        """
        SELECT status, completed_at, message, route
        FROM collection_runs
        WHERE store_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (store_id,),
    ).fetchone()


def begin_or_resume_cycle(conn, store_id, route, *, force_new_cycle=False):
    progress = conn.execute(
        """
        SELECT cycle_started_at, status
        FROM collection_progress
        WHERE store_id = ? AND route = ?
        """,
        (store_id, route),
    ).fetchone()
    resumable = {
        "running",
        "partial",
        "blocked",
        "wrong_store",
        "home_exit_offline",
        "failed",
    }
    resumed = bool(
        progress
        and progress["status"] in resumable
        and not force_new_cycle
    )
    cycle_started_at = (
        progress["cycle_started_at"]
        if resumed
        else datetime.now(timezone.utc).isoformat()
    )
    now = utc_now()
    conn.execute(
        """
        INSERT INTO collection_progress (
            store_id, route, cycle_started_at, status, updated_at, completed_at
        ) VALUES (?, ?, ?, 'running', ?, NULL)
        ON CONFLICT(store_id, route) DO UPDATE SET
            cycle_started_at = excluded.cycle_started_at,
            status = 'running',
            updated_at = excluded.updated_at,
            completed_at = NULL
        """,
        (store_id, route, cycle_started_at, now),
    )
    return cycle_started_at, resumed


def remaining_cycle_count(conn, store_id, cycle_started_at):
    return conn.execute(
        """
        SELECT COUNT(*)
        FROM products p
        LEFT JOIN price_checks c
          ON c.store_id = p.store_id
         AND c.product_key = p.product_key
        WHERE p.store_id = ?
          AND (c.checked_at IS NULL OR c.checked_at < ?)
        """,
        (store_id, cycle_started_at),
    ).fetchone()[0]


def run_collection(
    database,
    store_id,
    store_name,
    store_query,
    profile,
    route="direct",
    proxy="",
    limit=5000,
    delay_seconds=75,
    cooldown_seconds=21600,
    force=False,
    force_new_cycle=False,
    fetch_page=None,
    proxy_probe=probe_socks_proxy,
):
    if not STORE_ID_PATTERN.fullmatch(store_id):
        raise ServiceError("store_id is invalid.")
    store_name = validate_text(store_name, "store_name", 200, required=True)
    store_query = validate_text(store_query, "store_query", 100, required=True)
    if route not in {"direct", "home_exit"}:
        raise ServiceError("Collection route is invalid.")
    if route == "home_exit":
        proxy = validate_home_proxy(proxy)
    elif proxy:
        raise ServiceError("A proxy is allowed only for the home_exit route.")
    started_at = utc_now()
    with closing(connect_database(database)) as conn:
        with conn:
            conn.execute(
                """
                UPDATE collection_runs
                SET status = 'failed', completed_at = ?,
                    message = 'Previous collector process ended unexpectedly.'
                WHERE store_id = ? AND status = 'running'
                """,
                (started_at, store_id),
            )
        previous = latest_run(conn, store_id, route)
        if previous and previous["status"] == "blocked" and not force:
            previous_at = parse_timestamp(previous["completed_at"])
            next_retry = (
                previous_at + timedelta(seconds=cooldown_seconds)
                if previous_at
                else None
            )
            if next_retry and datetime.now(timezone.utc) < next_retry:
                return {
                    "contract": CONTRACT,
                    "status": "cooldown",
                    "checked": 0,
                    "deals": 0,
                    "next_retry_at": next_retry.replace(microsecond=0).isoformat(),
                }
        with conn:
            cycle_started_at, resumed = begin_or_resume_cycle(
                conn,
                store_id,
                route,
                force_new_cycle=force_new_cycle,
            )
        products = conn.execute(
            """
            SELECT p.product_key, p.url
            FROM products p
            LEFT JOIN price_checks c
              ON c.store_id = p.store_id
             AND c.product_key = p.product_key
            WHERE p.store_id = ?
              AND (c.checked_at IS NULL OR c.checked_at < ?)
            ORDER BY
                CASE WHEN c.checked_at IS NULL THEN 0 ELSE 1 END,
                c.checked_at,
                p.product_key
            LIMIT ?
            """,
            (
                store_id,
                cycle_started_at,
                max(1, min(int(limit), MAX_PRODUCTS)),
            ),
        ).fetchall()
        if not products:
            total_products = conn.execute(
                "SELECT COUNT(*) FROM products WHERE store_id = ?",
                (store_id,),
            ).fetchone()[0]
            if not total_products:
                raise ServiceError("No products are registered for this store.")
            completed_at = utc_now()
            with conn:
                conn.execute(
                    """
                    UPDATE collection_progress
                    SET status = 'completed', updated_at = ?, completed_at = ?
                    WHERE store_id = ? AND route = ?
                    """,
                    (completed_at, completed_at, store_id, route),
                )
            return {
                "contract": CONTRACT,
                "status": "completed",
                "checked": 0,
                "deals": 0,
                "remaining": 0,
                "route": route,
                "cycle_started_at": cycle_started_at,
                "resumed": resumed,
                "completed_at": completed_at,
            }
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO collection_runs (
                    store_id, route, status, started_at
                )
                VALUES (?, ?, 'running', ?)
                """,
                (store_id, route, started_at),
            )
            run_id = cursor.lastrowid

        checked = sales = 0
        remaining = remaining_cycle_count(conn, store_id, cycle_started_at)
        run_status = "completed"
        message = ""
        session = None
        try:
            if route == "home_exit":
                proxy_probe(proxy)
            if fetch_page is None:
                try:
                    from scrapling.fetchers import DynamicSession
                except ImportError as exc:
                    raise ServiceError(
                        "Scrapling fetchers are not installed in this runtime."
                    ) from exc
                Path(profile).mkdir(parents=True, exist_ok=True)
                session_options = {
                    "headless": True,
                    "user_data_dir": str(Path(profile).resolve()),
                    "locale": "en-CA",
                    "timezone_id": "America/Toronto",
                    "max_pages": 1,
                    "timeout": 45_000,
                    "wait": 1_500,
                    "network_idle": False,
                    "extra_flags": ["--disable-dev-shm-usage"],
                }
                if proxy:
                    session_options["proxy"] = proxy
                session = DynamicSession(
                    **session_options
                )
                session.start()
                fetch_page = session.fetch
            for index, product in enumerate(products):
                try:
                    page = fetch_page(
                        product["url"],
                        timeout=45_000,
                        wait=1_500,
                        network_idle=False,
                        page_action=lambda browser_page: ensure_store_selection(
                            browser_page, store_name, store_query
                        ),
                    )
                    facts = extract_price_facts(page, store_name)
                except CollectionBlocked as exc:
                    run_status = "blocked"
                    message = str(exc)
                    break
                except WrongStore as exc:
                    run_status = "wrong_store"
                    message = str(exc)
                    break
                except Exception as exc:
                    run_status = "partial" if checked else "failed"
                    message = f"Collection stopped after a product error: {type(exc).__name__}."
                    break
                checked_at = utc_now()
                with conn:
                    conn.execute(
                        """
                        INSERT INTO price_checks (
                            store_id, product_key, status, regular_price,
                            current_price, on_sale, checked_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(store_id, product_key) DO UPDATE SET
                            status = excluded.status,
                            regular_price = excluded.regular_price,
                            current_price = excluded.current_price,
                            on_sale = excluded.on_sale,
                            checked_at = excluded.checked_at
                        """,
                        (
                            store_id,
                            product["product_key"],
                            facts["status"],
                            facts["regular_price"],
                            facts["current_price"],
                            int(facts["on_sale"]),
                            checked_at,
                        ),
                    )
                checked += 1
                sales += int(facts["on_sale"])
                if index + 1 < len(products) and delay_seconds:
                    time.sleep(max(0, delay_seconds))
        except HomeExitOffline as exc:
            run_status = "home_exit_offline"
            message = str(exc)
        except ServiceError as exc:
            run_status = "failed"
            message = str(exc)
        except Exception as exc:
            run_status = "failed"
            message = f"Collector setup failed: {type(exc).__name__}."
        finally:
            if session is not None:
                session.close()
            completed_at = utc_now()
            with conn:
                remaining = remaining_cycle_count(
                    conn,
                    store_id,
                    cycle_started_at,
                )
                if run_status == "completed" and remaining:
                    run_status = "partial"
                    message = "Collection paused at the configured product limit."
                conn.execute(
                    """
                    UPDATE collection_runs
                    SET status = ?, completed_at = ?, checked_count = ?,
                        sale_count = ?, message = ?
                    WHERE id = ?
                    """,
                    (
                        run_status,
                        completed_at,
                        checked,
                        sales,
                        validate_text(message, "message", 300),
                        run_id,
                    ),
                )
                conn.execute(
                    """
                    UPDATE collection_progress
                    SET status = ?, updated_at = ?, completed_at = ?
                    WHERE store_id = ? AND route = ?
                    """,
                    (
                        run_status,
                        completed_at,
                        completed_at if run_status == "completed" else None,
                        store_id,
                        route,
                    ),
                )
        return {
            "contract": CONTRACT,
            "status": run_status,
            "checked": checked,
            "deals": sales,
            "remaining": remaining,
            "route": route,
            "cycle_started_at": cycle_started_at,
            "resumed": resumed,
            "completed_at": completed_at,
        }


def probe_request(payload):
    if payload.get("contract") != CONTRACT:
        raise ServiceError("Unsupported price-watch contract.")
    proxy = validate_home_proxy(payload.get("proxy"))
    return probe_socks_proxy(proxy)


def run_request(database, payload):
    if payload.get("contract") != CONTRACT:
        raise ServiceError("Unsupported price-watch contract.")
    store_id = validate_text(
        payload.get("store_id"), "store_id", 64, required=True
    )
    if not STORE_ID_PATTERN.fullmatch(store_id):
        raise ServiceError("store_id is invalid.")
    store_name = validate_text(
        payload.get("store_name"), "store_name", 200, required=True
    )
    proxy = validate_home_proxy(payload.get("proxy"))
    return run_my_items_collection(
        database,
        store_id,
        store_name,
        DEFAULT_AUTH_PROFILE,
        route="home_exit",
        proxy=proxy,
    )


def enqueue_request(payload, trigger_path=DEFAULT_MANUAL_TRIGGER):
    if payload.get("contract") != CONTRACT:
        raise ServiceError("Unsupported price-watch contract.")
    store_id = validate_text(
        payload.get("store_id"), "store_id", 64, required=True
    )
    if not STORE_ID_PATTERN.fullmatch(store_id):
        raise ServiceError("store_id is invalid.")
    store_name = validate_text(
        payload.get("store_name"), "store_name", 200, required=True
    )
    store_query = validate_text(
        payload.get("store_query"), "store_query", 100, required=True
    )
    delay_seconds = payload.get("delay_seconds", 75)
    if (
        isinstance(delay_seconds, bool)
        or not isinstance(delay_seconds, int)
        or not 30 <= delay_seconds <= 120
    ):
        raise ServiceError("delay_seconds must be an integer from 30 to 120.")
    queued_at = utc_now()
    atomic_write_json(
        trigger_path,
        {
            "contract": CONTRACT,
            "queued_at": queued_at,
            "store_id": store_id,
            "store_name": store_name,
            "store_query": store_query,
            "delay_seconds": delay_seconds,
        },
    )
    previous = read_json(DEFAULT_SCHEDULE_STATUS, default={}) or {}
    if not isinstance(previous, dict):
        previous = {}
    previous.update(
        {
            "contract": CONTRACT,
            "status": "queued",
            "timezone": "America/Toronto",
            "mode": "manual_only",
            "strategy": "my_items",
            "queued_at": queued_at,
        }
    )
    for stale_key in (
        "cadence_days",
        "scheduled_local_time",
        "next_due_at",
        "last_attempt_local_date",
    ):
        previous.pop(stale_key, None)
    atomic_write_json(DEFAULT_SCHEDULE_STATUS, previous)
    return {
        "contract": CONTRACT,
        "status": "queued",
        "route": "direct",
        "queued_at": queued_at,
    }


def get_deals(database, store_id, cursor=""):
    if not STORE_ID_PATTERN.fullmatch(store_id):
        raise ServiceError("store_id is invalid.")
    generated_at = utc_now()
    with closing(connect_database(database)) as conn:
        run = latest_my_items_run(conn, store_id)
        rows = conn.execute(
            """
            SELECT source_name, regular_price, current_price, checked_at
            FROM my_items_deals
            WHERE store_id = ?
            ORDER BY source_name
            """,
            (store_id,),
        ).fetchall()
    deals = [
        {
            "source_name": row["source_name"],
            "regular_price": row["regular_price"],
            "current_price": row["current_price"],
            "on_sale": True,
            "checked_at": row["checked_at"],
        }
        for row in rows
        if row["current_price"] < row["regular_price"]
    ]
    status = run["status"] if run else "never_run"
    next_retry_at = ""
    if run and status == "blocked":
        completed = parse_timestamp(run["completed_at"])
        if completed:
            next_retry_at = (
                completed + timedelta(seconds=21600)
            ).replace(microsecond=0).isoformat()
    schedule = read_json(DEFAULT_SCHEDULE_STATUS, default={}) or {}
    safe_schedule = {
        key: schedule[key]
        for key in (
            "status",
            "timezone",
            "cadence_days",
            "scheduled_local_time",
            "last_attempt_at",
            "last_attempt_local_date",
            "last_result_status",
            "next_due_at",
            "mode",
            "strategy",
            "checked",
            "remaining",
            "deals",
        )
        if isinstance(schedule.get(key), (str, int))
        and not isinstance(schedule.get(key), bool)
    }
    return {
        "contract": CONTRACT,
        "generated_at": generated_at,
        "cursor": hashlib.sha256(generated_at.encode("utf-8")).hexdigest()[:24],
        "collector_status": status,
        "route": run["route"] if run else "",
        "last_run_at": run["completed_at"] if run else "",
        "next_retry_at": next_retry_at,
        "checked": int(run["item_count"]) if run else 0,
        "schedule": safe_schedule,
        "deals": deals,
        "checks": [],
    }


def get_status(database):
    with closing(connect_database(database)) as conn:
        stores = [
            dict(row)
            for row in conn.execute(
                """
                SELECT store_id, COUNT(*) AS products
                FROM products
                GROUP BY store_id
                ORDER BY store_id
                """
            )
        ]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    schedule = read_json(DEFAULT_SCHEDULE_STATUS, default={}) or {}
    return {
        "contract": CONTRACT,
        "status": "ok" if integrity == "ok" else "failed",
        "database_integrity": integrity,
        "stores": stores,
        "generated_at": utc_now(),
        "schedule": schedule if isinstance(schedule, dict) else {},
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Store and check the cumulative Walmart purchased-product watchlist."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ingest")
    subparsers.add_parser("probe")
    subparsers.add_parser("run")
    subparsers.add_parser("enqueue")
    deals = subparsers.add_parser("deals")
    deals.add_argument("--store-id", required=True)
    deals.add_argument("--cursor", default="")
    check = subparsers.add_parser("check")
    check.add_argument("--store-id", required=True)
    check.add_argument("--store-name", required=True)
    check.add_argument("--store-query", required=True)
    check.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    check.add_argument("--limit", type=int, default=MAX_PRODUCTS)
    check.add_argument("--delay-seconds", type=int, default=75)
    check.add_argument("--cooldown-seconds", type=int, default=21600)
    check.add_argument("--force", action="store_true")
    check.add_argument("--force-new-cycle", action="store_true")
    subparsers.add_parser("status")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == "ingest":
            result = ingest_watchlist(args.database, read_stdin_json())
        elif args.command == "probe":
            result = probe_request(read_stdin_json())
        elif args.command == "run":
            result = run_request(args.database, read_stdin_json())
        elif args.command == "enqueue":
            result = enqueue_request(read_stdin_json())
        elif args.command == "deals":
            result = get_deals(args.database, args.store_id, args.cursor)
        elif args.command == "check":
            result = run_my_items_collection(
                args.database,
                args.store_id,
                args.store_name,
                DEFAULT_AUTH_PROFILE,
                cooldown_seconds=args.cooldown_seconds,
                force=args.force,
            )
        else:
            result = get_status(args.database)
    except ServiceError as exc:
        print(json.dumps({
            "contract": CONTRACT,
            "status": "failed",
            "message": str(exc),
        }, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
