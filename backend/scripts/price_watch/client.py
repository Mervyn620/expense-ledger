import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = "expense-price-watch/v1"
TOKEN_ENVIRONMENT_VARIABLE = "EXPENSE_PRICE_WATCH_TOKEN"
CONFIG_RELATIVE_PATH = Path("config") / "price_watch.json"
DATABASE_RELATIVE_PATH = Path("data") / "expenses.sqlite"
DEALS_RELATIVE_PATH = Path("data") / "walmart_deals.json"
SYNC_STATE_RELATIVE_PATH = Path(".codex") / "price-watch-sync-state.json"
SYNC_STATUS_RELATIVE_PATH = Path(".codex") / "price-watch-sync-status.json"
RECEIVE_STATE_RELATIVE_PATH = Path(".codex") / "price-watch-receive-state.json"
RECEIVE_STATUS_RELATIVE_PATH = Path(".codex") / "price-watch-receive-status.json"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SSH_HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
REMOTE_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9_./-]{1,512}$")
CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{0,128}$")


class PriceWatchError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
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
    except (OSError, json.JSONDecodeError) as exc:
        raise PriceWatchError(f"Could not read {path}: {exc}") from exc


def load_config(root):
    path = Path(root) / CONFIG_RELATIVE_PATH
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise PriceWatchError(f"Price-watch configuration is missing or invalid: {path}")
    config = {
        "enabled": bool(payload.get("enabled", False)),
        "transport": str(payload.get("transport", "https")).strip().lower(),
        "base_url": str(payload.get("base_url", "")).strip(),
        "ssh_host": str(payload.get("ssh_host", "")).strip(),
        "remote_python": str(payload.get("remote_python", "")).strip(),
        "remote_script": str(payload.get("remote_script", "")).strip(),
        "store_id": str(payload.get("store_id", "")).strip(),
        "store_name": str(payload.get("store_name", "")).strip(),
        "store_query": str(payload.get("store_query", "")).strip(),
        "home_exit_remote_port": payload.get("home_exit_remote_port", 1080),
        "home_exit_ready_timeout_seconds": payload.get(
            "home_exit_ready_timeout_seconds", 20
        ),
        "collection_timeout_seconds": payload.get(
            "collection_timeout_seconds", 180
        ),
        "check_limit": payload.get("check_limit", 5000),
        "check_delay_seconds": payload.get("check_delay_seconds", 30),
        "sync_path": str(payload.get("sync_path", "/api/v1/walmart/watchlist")).strip(),
        "deals_path": str(payload.get("deals_path", "/api/v1/walmart/deals")).strip(),
        "request_timeout_seconds": payload.get("request_timeout_seconds", 20),
    }
    timeout = config["request_timeout_seconds"]
    if not isinstance(timeout, int) or not 1 <= timeout <= 120:
        raise PriceWatchError("request_timeout_seconds must be an integer from 1 to 120.")
    if not config["enabled"]:
        return config
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", config["store_id"]):
        raise PriceWatchError("Enabled price watch requires a valid store_id.")
    if not config["store_name"] or len(config["store_name"]) > 200:
        raise PriceWatchError("Enabled price watch requires a valid store_name.")
    if not config["store_query"] or len(config["store_query"]) > 100:
        raise PriceWatchError("Enabled price watch requires a valid store_query.")
    integer_bounds = {
        "home_exit_remote_port": (1024, 65535),
        "home_exit_ready_timeout_seconds": (5, 60),
        "collection_timeout_seconds": (60, 14400),
        "check_limit": (1, 5000),
        "check_delay_seconds": (30, 120),
    }
    for key, (minimum, maximum) in integer_bounds.items():
        value = config[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise PriceWatchError(
                f"{key} must be an integer from {minimum} to {maximum}."
            )
    if config["transport"] == "ssh":
        if not SSH_HOST_PATTERN.fullmatch(config["ssh_host"]):
            raise PriceWatchError("SSH price watch requires a simple ssh_host alias.")
        for key in ("remote_python", "remote_script"):
            if not REMOTE_PATH_PATTERN.fullmatch(config[key]):
                raise PriceWatchError(
                    f"SSH price watch requires a safe absolute {key} path."
                )
        return config
    if config["transport"] != "https":
        raise PriceWatchError("transport must be either https or ssh.")
    if not config["base_url"]:
        raise PriceWatchError("HTTPS price watch requires base_url.")
    parsed = urlsplit(config["base_url"])
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PriceWatchError("base_url cannot contain credentials, a query, or a fragment.")
    loopback = (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise PriceWatchError("base_url must use HTTPS; HTTP is allowed only for loopback tests.")
    for key in ("sync_path", "deals_path"):
        if not config[key].startswith("/") or urlsplit(config[key]).netloc:
            raise PriceWatchError(f"{key} must be an absolute URL path.")
    return config


def canonicalize_product_url(value):
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return ""
    if hostname not in {"walmart.ca", "www.walmart.ca"}:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "").rstrip("/")
    if "/ip/" not in path.lower():
        return ""
    return urlunsplit(("https", "www.walmart.ca", path, "", ""))


def product_key(product_url):
    path = urlsplit(product_url).path.rstrip("/")
    product_id = re.search(r"/(\d{6,})$", path)
    if product_id:
        return f"walmart-ca:{product_id.group(1)}"
    digest = hashlib.sha256(product_url.encode("utf-8")).hexdigest()[:24]
    return f"walmart-ca:url:{digest}"


def read_local_products(root):
    database = (Path(root) / DATABASE_RELATIVE_PATH).resolve()
    if not database.exists():
        raise PriceWatchError(f"Expense database does not exist: {database}")
    uri = f"{database.as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute(
                """
                SELECT
                    i.product_name,
                    i.product_link,
                    i.price,
                    i.currency,
                    o.date
                FROM order_items i
                JOIN orders o ON o.id = i.order_pk
                WHERE o.platform = 'walmart_ca'
                  AND TRIM(COALESCE(i.product_link, '')) <> ''
                ORDER BY o.date, i.id
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise PriceWatchError(f"Could not read Walmart products from the ledger: {exc}") from exc

    products = {}
    for name, raw_url, price, currency, purchase_date in rows:
        url = canonicalize_product_url(raw_url)
        if not url:
            continue
        key = product_key(url)
        existing = products.get(key)
        purchased_on = str(purchase_date or "")
        candidate = {
            "product_key": key,
            "name": re.sub(r"\s+", " ", str(name or "")).strip(),
            "url": url,
            "first_purchased_on": purchased_on,
            "last_purchased_on": purchased_on,
            "last_purchase_price": round(float(price), 2) if price is not None else None,
            "currency": str(currency or "CAD"),
        }
        if not existing:
            products[key] = candidate
            continue
        known_dates = [
            value for value in (existing["first_purchased_on"], purchased_on) if value
        ]
        existing["first_purchased_on"] = min(known_dates) if known_dates else ""
        if purchased_on >= existing["last_purchased_on"]:
            existing.update({
                "name": candidate["name"] or existing["name"],
                "url": candidate["url"],
                "last_purchased_on": purchased_on,
                "last_purchase_price": candidate["last_purchase_price"],
                "currency": candidate["currency"],
            })
    return [products[key] for key in sorted(products)]


def product_fingerprint(product):
    encoded = json.dumps(
        product,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def endpoint(config, path_key):
    return urljoin(
        config["base_url"].rstrip("/") + "/",
        config[path_key].lstrip("/"),
    )


def request_json(method, url, token, payload=None, timeout=20):
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "expense-price-watch-client/1",
    }
    if payload is not None:
        data = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                raise PriceWatchError("VPS response exceeded the local size limit.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise PriceWatchError("VPS response exceeded the local size limit.")
            if not raw:
                return {}
            result = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        raise PriceWatchError(f"VPS returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise PriceWatchError(f"Could not reach the VPS: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PriceWatchError("VPS returned an invalid JSON response.") from exc
    if not isinstance(result, dict):
        raise PriceWatchError("VPS response must be a JSON object.")
    return result


def ssh_request_json(config, action, payload=None, cursor="", timeout=None):
    if action not in {"ingest", "deals", "status", "probe", "run", "enqueue"}:
        raise PriceWatchError("Unsupported VPS SSH action.")
    timeout = timeout or config["request_timeout_seconds"]
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={min(timeout, 30)}",
        config["ssh_host"],
        config["remote_python"],
        config["remote_script"],
        action,
    ]
    if action == "deals":
        command.extend(["--store-id", config["store_id"]])
        if cursor:
            command.extend(["--cursor", cursor])
    encoded = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if payload is not None
        else ""
    )
    try:
        completed = subprocess.run(
            command,
            input=encoded,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PriceWatchError("OpenSSH client was not found on this computer.") from exc
    except subprocess.TimeoutExpired as exc:
        raise PriceWatchError("VPS SSH request timed out.") from exc
    except UnicodeError as exc:
        raise PriceWatchError("VPS SSH response was not valid UTF-8.") from exc
    raw = completed.stdout.encode("utf-8")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise PriceWatchError("VPS response exceeded the local size limit.")
    if completed.returncode != 0:
        raise PriceWatchError(
            f"VPS SSH command failed with exit code {completed.returncode}."
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PriceWatchError("VPS returned an invalid JSON response.") from exc
    if not isinstance(result, dict):
        raise PriceWatchError("VPS response must be a JSON object.")
    return result


def require_token(environ):
    token = str(environ.get(TOKEN_ENVIRONMENT_VARIABLE, "")).strip()
    if not token:
        raise PriceWatchError(
            f"Set {TOKEN_ENVIRONMENT_VARIABLE} before enabling VPS price watch."
        )
    return token


def call_transport(config, action, payload, cursor, environ, transport):
    if transport is not None:
        token = require_token(environ)
        if action == "ingest":
            return transport(
                "POST",
                endpoint(config, "sync_path"),
                token,
                payload,
                config["request_timeout_seconds"],
            )
        query = {"store_id": config["store_id"]}
        if cursor:
            query["cursor"] = cursor
        return transport(
            "GET",
            f"{endpoint(config, 'deals_path')}?{urlencode(query)}",
            token,
            None,
            config["request_timeout_seconds"],
        )
    if config["transport"] == "ssh":
        return ssh_request_json(config, action, payload=payload, cursor=cursor)
    token = require_token(environ)
    if action == "ingest":
        return request_json(
            "POST",
            endpoint(config, "sync_path"),
            token,
            payload,
            config["request_timeout_seconds"],
        )
    query = {"store_id": config["store_id"]}
    if cursor:
        query["cursor"] = cursor
    return request_json(
        "GET",
        f"{endpoint(config, 'deals_path')}?{urlencode(query)}",
        token,
        None,
        config["request_timeout_seconds"],
    )


def sanitized_acknowledgement(response):
    allowed = {}
    for key in ("accepted", "created", "updated", "unchanged"):
        value = response.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            allowed[key] = value
    return allowed


def sync_products(root=ROOT, environ=None, transport=None):
    root = Path(root).resolve()
    config = load_config(root)
    if not config["enabled"]:
        result = {
            "contract": CONTRACT,
            "status": "disabled",
            "completed_at": utc_now(),
            "sent": 0,
        }
        atomic_write_json(root / SYNC_STATUS_RELATIVE_PATH, result)
        return result

    products = read_local_products(root)
    state = read_json(root / SYNC_STATE_RELATIVE_PATH, default={}) or {}
    sent_state = state.get("products", {})
    if not isinstance(sent_state, dict):
        sent_state = {}
    pending = [
        product for product in products
        if sent_state.get(product["product_key"]) != product_fingerprint(product)
    ]
    if not pending:
        result = {
            "contract": CONTRACT,
            "status": "no_changes",
            "completed_at": utc_now(),
            "available": len(products),
            "sent": 0,
        }
        atomic_write_json(root / SYNC_STATUS_RELATIVE_PATH, result)
        return result

    fingerprints = {
        product["product_key"]: product_fingerprint(product) for product in pending
    }
    batch_material = "\n".join(
        f"{key}:{fingerprints[key]}" for key in sorted(fingerprints)
    )
    payload = {
        "contract": CONTRACT,
        "batch_id": hashlib.sha256(batch_material.encode("utf-8")).hexdigest(),
        "generated_at": utc_now(),
        "store_id": config["store_id"],
        "products": pending,
    }
    response = call_transport(
        config,
        "ingest",
        payload,
        "",
        environ or os.environ,
        transport,
    )
    response_contract = response.get("contract")
    if response_contract not in {None, CONTRACT}:
        raise PriceWatchError("VPS returned an incompatible sync contract.")
    sent_state.update(fingerprints)
    completed_at = utc_now()
    atomic_write_json(root / SYNC_STATE_RELATIVE_PATH, {
        "contract": CONTRACT,
        "last_success_at": completed_at,
        "products": sent_state,
    })
    result = {
        "contract": CONTRACT,
        "status": "synced",
        "completed_at": completed_at,
        "available": len(products),
        "sent": len(pending),
        "server": sanitized_acknowledgement(response),
    }
    atomic_write_json(root / SYNC_STATUS_RELATIVE_PATH, result)
    return result


def optional_price(value, field):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise PriceWatchError(f"Price-watch result has an invalid {field}.")
    return round(float(value), 2)


def normalized_product_name(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[^\w%]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def normalize_deals(payload, local_products, store_id):
    if payload.get("contract") != CONTRACT:
        raise PriceWatchError("Price-watch returned an incompatible deals contract.")
    raw_deals = payload.get("deals")
    if not isinstance(raw_deals, list):
        raise PriceWatchError("Price-watch response is missing a deals list.")
    if len(raw_deals) > 5000:
        raise PriceWatchError("Price-watch response contains too many records.")
    products_by_key = {product["product_key"]: product for product in local_products}
    products_by_name = {}
    ambiguous_names = set()
    for product in local_products:
        name_key = normalized_product_name(product["name"])
        if not name_key:
            continue
        if name_key in products_by_name:
            ambiguous_names.add(name_key)
        else:
            products_by_name[name_key] = product
    for name_key in ambiguous_names:
        products_by_name.pop(name_key, None)
    deals = {}
    ignored = 0
    for raw in raw_deals:
        if not isinstance(raw, dict):
            ignored += 1
            continue
        source_name = str(raw.get("source_name", "")).strip()
        if source_name:
            local = products_by_name.get(normalized_product_name(source_name))
            key = local["product_key"] if local else ""
        else:
            key = str(raw.get("product_key", ""))
            local = products_by_key.get(key)
        if not local:
            ignored += 1
            continue
        remote_url = raw.get("url") if not source_name else ""
        if remote_url and canonicalize_product_url(remote_url) != local["url"]:
            ignored += 1
            continue
        current_price = optional_price(raw.get("current_price"), "current_price")
        regular_price = optional_price(raw.get("regular_price"), "regular_price")
        on_sale = (
            current_price is not None
            and regular_price is not None
            and current_price < regular_price
        )
        if not on_sale:
            continue
        discount_percent = None
        if current_price is not None and regular_price:
            discount_percent = round(
                max(0.0, (regular_price - current_price) / regular_price * 100),
                1,
            )
        deals[key] = {
            "product_key": key,
            "name": local["name"],
            "url": local["url"],
            "store_id": store_id,
            "regular_price": regular_price,
            "current_price": current_price,
            "discount_percent": discount_percent,
            "checked_at": str(raw.get("checked_at") or payload.get("generated_at") or ""),
        }
    return [deals[key] for key in sorted(deals)], ignored


def normalize_checks(payload, local_products, store_id):
    raw_checks = payload.get("checks", [])
    if not isinstance(raw_checks, list) or len(raw_checks) > 5000:
        raise PriceWatchError("Price-watch response has an invalid checks list.")
    products_by_key = {product["product_key"]: product for product in local_products}
    checks = {}
    ignored = 0
    for raw in raw_checks:
        if not isinstance(raw, dict):
            ignored += 1
            continue
        key = str(raw.get("product_key", ""))
        local = products_by_key.get(key)
        if not local:
            ignored += 1
            continue
        remote_url = raw.get("url")
        if remote_url and canonicalize_product_url(remote_url) != local["url"]:
            ignored += 1
            continue
        status = str(raw.get("status", ""))
        if status not in {"ok", "unavailable"}:
            ignored += 1
            continue
        regular_price = optional_price(
            raw.get("regular_price"), "regular_price"
        )
        current_price = optional_price(
            raw.get("current_price"), "current_price"
        )
        checks[key] = {
            "product_key": key,
            "name": local["name"],
            "url": local["url"],
            "store_id": store_id,
            "status": status,
            "regular_price": regular_price,
            "current_price": current_price,
            "on_sale": bool(
                regular_price is not None
                and current_price is not None
                and current_price < regular_price
            ),
            "checked_at": str(raw.get("checked_at") or ""),
        }
    return [checks[key] for key in sorted(checks)], ignored


def sanitized_collector_status(payload):
    status = str(payload.get("collector_status", "")).strip()
    if status not in {
        "never_run",
        "completed",
        "partial",
        "blocked",
        "cooldown",
        "wrong_store",
        "home_exit_offline",
        "failed",
        "queued",
        "running",
        "login_required",
    }:
        status = "unknown"
    result = {"status": status}
    route = str(payload.get("route", "")).strip()
    if route in {"direct", "home_exit", "local"}:
        result["route"] = route
    for key in ("last_run_at", "next_retry_at"):
        value = str(payload.get(key, "")).strip()
        if value:
            result[key] = value
    return result


def sanitized_schedule_status(payload):
    raw = payload.get("schedule", {})
    if not isinstance(raw, dict):
        return {}
    result = {}
    allowed_strings = {
        "status",
        "timezone",
        "scheduled_local_time",
        "last_attempt_at",
        "last_attempt_local_date",
        "last_result_status",
        "next_due_at",
        "mode",
        "strategy",
    }
    allowed_integers = {"cadence_days", "checked", "remaining", "deals"}
    for key in allowed_strings:
        value = raw.get(key)
        if isinstance(value, str) and len(value) <= 100:
            result[key] = value
    for key in allowed_integers:
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    progress = raw.get("progress")
    if isinstance(progress, dict):
        safe_progress = {}
        for key in (
            "route",
            "cycle_started_at",
            "status",
            "updated_at",
            "completed_at",
        ):
            value = progress.get(key)
            if isinstance(value, str) and len(value) <= 100:
                safe_progress[key] = value
        if safe_progress:
            result["progress"] = safe_progress
    return result


def persist_deals_payload(
    root,
    payload,
    *,
    cursor="",
    merge_existing=False,
    output_relative_path=DEALS_RELATIVE_PATH,
    update_receive_state=True,
):
    root = Path(root).resolve()
    config = load_config(root)
    products = read_local_products(root)
    deals, ignored = normalize_deals(payload, products, config["store_id"])
    checks, ignored_checks = normalize_checks(
        payload, products, config["store_id"]
    )
    destination = root / output_relative_path
    existing = read_json(destination, default={}) or {}
    if merge_existing:
        if (
            isinstance(existing, dict)
            and existing.get("contract") == CONTRACT
            and existing.get("store_id") == config["store_id"]
        ):
            old_deals, _ = normalize_deals(
                existing,
                products,
                config["store_id"],
            )
            old_checks, _ = normalize_checks(
                existing,
                products,
                config["store_id"],
            )
            checked_keys = {row["product_key"] for row in checks}
            deals = [
                row for row in old_deals
                if row["product_key"] not in checked_keys
            ] + deals
            checks = [
                row for row in old_checks
                if row["product_key"] not in checked_keys
            ] + checks
            deals.sort(key=lambda row: row["product_key"])
            checks.sort(key=lambda row: row["product_key"])
    received_at = utc_now()
    collector = sanitized_collector_status(payload)
    schedule = sanitized_schedule_status(payload)
    if not schedule and isinstance(existing, dict):
        schedule = sanitized_schedule_status(existing)
    checked = payload.get("checked", len(checks))
    if (
        isinstance(checked, bool)
        or not isinstance(checked, int)
        or checked < 0
    ):
        checked = len(checks)
    if collector["status"] == "completed":
        last_valid_at = received_at
        last_valid_checked = checked
    else:
        last_valid_at = ""
        last_valid_checked = 0
        if isinstance(existing, dict):
            existing_last_valid_at = existing.get("last_valid_at")
            if isinstance(existing_last_valid_at, str):
                last_valid_at = existing_last_valid_at[:40]
            elif (
                isinstance(existing.get("collector"), dict)
                and existing["collector"].get("status") == "completed"
                and isinstance(existing.get("received_at"), str)
            ):
                last_valid_at = existing["received_at"][:40]
            existing_last_valid_checked = existing.get(
                "last_valid_checked",
                existing.get("checked", 0),
            )
            if (
                isinstance(existing_last_valid_checked, int)
                and not isinstance(existing_last_valid_checked, bool)
                and existing_last_valid_checked >= 0
            ):
                last_valid_checked = existing_last_valid_checked
    atomic_write_json(destination, {
        "contract": CONTRACT,
        "received_at": received_at,
        "server_generated_at": str(payload.get("generated_at", "")),
        "last_valid_at": last_valid_at,
        "last_valid_checked": last_valid_checked,
        "store_id": config["store_id"],
        "store_name": config["store_name"],
        "collector": collector,
        "schedule": schedule,
        "checked": checked,
        "deals": deals,
        "checks": checks,
    })
    next_cursor = str(payload.get("cursor", cursor)).strip()
    if not CURSOR_PATTERN.fullmatch(next_cursor):
        raise PriceWatchError("Price-watch returned an invalid cursor.")
    if update_receive_state:
        atomic_write_json(root / RECEIVE_STATE_RELATIVE_PATH, {
            "contract": CONTRACT,
            "last_success_at": received_at,
            "cursor": next_cursor,
        })
    result = {
        "contract": CONTRACT,
        "status": "received",
        "completed_at": received_at,
        "checked": checked,
        "deals": len(deals),
        "checks": len(checks),
        "ignored": ignored + ignored_checks,
        "collector_status": collector["status"],
    }
    if update_receive_state:
        atomic_write_json(root / RECEIVE_STATUS_RELATIVE_PATH, result)
    return result


def pull_deals(root=ROOT, environ=None, transport=None):
    root = Path(root).resolve()
    config = load_config(root)
    if not config["enabled"]:
        result = {
            "contract": CONTRACT,
            "status": "disabled",
            "completed_at": utc_now(),
            "deals": 0,
        }
        atomic_write_json(root / RECEIVE_STATUS_RELATIVE_PATH, result)
        return result

    state = read_json(root / RECEIVE_STATE_RELATIVE_PATH, default={}) or {}
    cursor = str(state.get("cursor", "")).strip()
    if not CURSOR_PATTERN.fullmatch(cursor):
        raise PriceWatchError("Saved VPS cursor is invalid.")
    payload = call_transport(
        config,
        "deals",
        None,
        cursor,
        environ or os.environ,
        transport,
    )
    collector_status = str(payload.get("collector_status", ""))
    return persist_deals_payload(
        root,
        payload,
        cursor=cursor,
        merge_existing=collector_status != "completed",
    )


def write_failure_status(root, command, error):
    relative = (
        SYNC_STATUS_RELATIVE_PATH
        if command == "sync"
        else RECEIVE_STATUS_RELATIVE_PATH
    )
    atomic_write_json(Path(root) / relative, {
        "contract": CONTRACT,
        "status": "failed",
        "completed_at": utc_now(),
        "message": str(error),
    })


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Sync purchased Walmart products and receive VPS deal results."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("command", choices=("sync", "pull"))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        result = (
            sync_products(args.root)
            if args.command == "sync"
            else pull_deals(args.root)
        )
    except PriceWatchError as exc:
        write_failure_status(args.root, args.command, exc)
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
