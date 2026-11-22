import csv
import io
import json
import math
import os
import sqlite3
import sys
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "expenses.sqlite"
LEDGER_DIR = Path(__file__).resolve().parent
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from schema import create_schema

TRANSACTION_FIELDS = [
    "date",
    "platform",
    "order_id",
    "merchant",
    "fulfillment",
    "category",
    "item",
    "quantity",
    "item_total",
    "tax",
    "fees",
    "tip",
    "total",
    "currency",
    "source_url",
    "notes",
]

ITEM_FIELDS = [
    "date",
    "platform",
    "order_id",
    "category",
    "product_name",
    "quantity",
    "price",
    "delivery_status",
    "product_link",
    "currency",
]

LEGACY_ITEM_STAGING = (
    "data/walmart_items.csv",
    "data/amazon_items.csv",
    "data/delivery_items.csv",
)


def item_staging_paths(root=ROOT):
    root = Path(root).resolve()
    manifest_path = root / "config" / "collectors.json"
    relative_paths = []
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Collector manifest could not be read: {exc}") from exc
        collectors = payload.get("collectors")
        if not isinstance(collectors, list):
            raise ValueError("Collector manifest does not define collectors.")
        for collector in collectors:
            value = (collector.get("outputs") or {}).get("item_staging")
            if value:
                relative_paths.append(str(value))
    else:
        relative_paths.extend(LEGACY_ITEM_STAGING)
    relative_paths.append("data/bank_items.csv")

    result = []
    seen = set()
    for value in relative_paths:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe item staging path: {value!r}")
        resolved = (root / relative).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Item staging path escaped the workspace: {value!r}") from exc
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def read_csv(path, fields=None, *, recover=True):
    path = Path(path)
    if recover and path.parent.name == 'data':
        recover_csv_publications(path.parent.parent)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if fields is None:
            return list(reader)
        return [{field: row.get(field, "") for field in fields} for row in reader]


def write_csv(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path, text, encoding="utf-8-sig"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path, payload):
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


PUBLICATION_FILES = frozenset({
    'transactions.csv', 'walmart_items.csv', 'amazon_items.csv',
    'delivery_items.csv', 'the_ordinary_items.csv', 'tnt_items.csv',
    'bank_items.csv', 'payment_assignments.csv',
})


def _publication_path(root, filename):
    root = Path(root).resolve()
    if filename not in PUBLICATION_FILES:
        raise ValueError('Unmanaged CSV publication path.')
    path = root / 'data' / filename
    if path.is_symlink() or path.resolve().parent != root / 'data':
        raise ValueError('CSV publication path escaped the data directory.')
    return path


def _publish_csv_publications(conn, root):
    """Caller holds BEGIN IMMEDIATE. Retain the entire outbox until all files succeed."""
    rows = conn.execute('SELECT filename, content FROM pending_csv_publications ORDER BY filename').fetchall()
    # Validate all destinations before publishing any of them.
    paths = [_publication_path(root, row[0]) for row in rows]
    for path, (_, content) in zip(paths, rows):
        atomic_write_text(path, content)
    conn.execute('DELETE FROM pending_csv_publications')
    return len(rows)


def queue_csv_publications(conn, root, publications):
    """Queue (filename, fields, rows) in the caller's ledger transaction."""
    for filename, fields, rows in publications:
        _publication_path(root, filename)
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        conn.execute('INSERT OR REPLACE INTO pending_csv_publications(filename,content) VALUES (?,?)',
                     (filename, buffer.getvalue()))


def recover_csv_publications(root=ROOT, db_path=None):
    """Finish a committed import before a consumer can re-import stale CSV files."""
    root = Path(root).resolve()
    db_path = Path(db_path) if db_path else root / 'data' / 'expenses.sqlite'
    if not db_path.exists():
        return 0
    # No schema initialization or database creation on this read-side check.
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=rw', uri=True, timeout=10)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_csv_publications'").fetchone():
            return 0
        if not conn.execute('SELECT 1 FROM pending_csv_publications LIMIT 1').fetchone():
            return 0
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            return _publish_csv_publications(conn, root)


def import_orders_with_csv(transactions, item_rows, *, root=ROOT, item_filename,
                           delete_order_keys=None):
    """Commit ledger changes and recoverable CSV contents together, then publish.

    Constraint/serialization failures change neither the ledger nor the CSVs.
    A publication failure leaves the committed ledger authoritative and the
    durable outbox pending; callers must report failure and retry recovery.
    """
    root = Path(root).resolve()
    tx_path = _publication_path(root, 'transactions.csv')
    item_path = _publication_path(root, item_filename)
    if item_filename == 'transactions.csv':
        raise ValueError('Order items require a separate CSV file.')
    transactions, item_rows = list(transactions), list(item_rows)
    keys = {tx_key_tuple(row) for row in transactions}
    deleted_keys = set(delete_order_keys or [])
    owned_keys = keys | deleted_keys
    if keys & deleted_keys:
        raise ValueError('An import cannot update and delete the same order.')
    if any(tx_key_tuple(row) not in keys for row in item_rows):
        raise ValueError('Every imported item must belong to an imported order.')
    with closing(connect(root / 'data' / 'expenses.sqlite')) as conn:
        conn.commit()
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            _publish_csv_publications(conn, root)
            existing_tx = read_csv(tx_path, TRANSACTION_FIELDS, recover=False)
            existing_items = read_csv(item_path, ITEM_FIELDS, recover=False)
            merged_tx = upsert_rows(
                [row for row in existing_tx if tx_key_tuple(row) not in owned_keys],
                transactions, TRANSACTION_FIELDS, tx_key)
            merged_items = upsert_rows(
                [row for row in existing_items if tx_key_tuple(row) not in owned_keys],
                item_rows, ITEM_FIELDS, lambda row: (tx_key_tuple(row), line_key(row)))
            publications = []
            for path, fields, rows in ((tx_path, TRANSACTION_FIELDS, merged_tx),
                                        (item_path, ITEM_FIELDS, merged_items)):
                buffer = io.StringIO(newline='')
                writer = csv.DictWriter(buffer, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
                publications.append((path.name, buffer.getvalue()))
            upsert_cards(conn, root)
            deleted = _delete_orders(conn, deleted_keys)
            upsert_orders(conn, transactions)
            replace_items_for_orders(conn, item_rows, keys)
            ensure_payments_for_orders(conn, transactions, root)
            conn.executemany('INSERT OR REPLACE INTO pending_csv_publications(filename,content) VALUES (?,?)', publications)
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                _publish_csv_publications(conn, root)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise RuntimeError('Ledger import committed, but CSV publication is pending. Retry the import or CSV recovery before syncing.') from exc
    return deleted


def read_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def upsert_rows(existing, incoming, fields, key_func):
    merged = OrderedDict()
    for row in existing:
        merged[key_func(row)] = {field: row.get(field, "") for field in fields}
    for row in incoming:
        merged[key_func(row)] = {field: row.get(field, "") for field in fields}
    return list(merged.values())


def money(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def item_price_text(value):
    """Canonical staging price. An explicit zero is a price, a blank is unknown."""
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    return f"{amount:.2f}" if math.isfinite(amount) else ""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def tx_key(row):
    return f"{row.get('platform', '')}|{row.get('order_id', '')}"


def tx_key_tuple(row):
    return (row.get("platform", ""), row.get("order_id", ""))


def connect(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    return conn


def load_card_payload(root=ROOT):
    return read_json(Path(root) / "config" / "payment_cards.json", {"cards": {}, "default_card_id": "unassigned"})


def upsert_cards(conn, root=ROOT):
    payload = load_card_payload(root)
    for card_id, card in payload.get("cards", {}).items():
        conn.execute(
            """
            INSERT INTO cards (card_id, display_name, network, issuer, last4, kind)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(card_id) DO UPDATE SET
              display_name=excluded.display_name,
              network=excluded.network,
              issuer=excluded.issuer,
              last4=excluded.last4,
              kind=excluded.kind
            """,
            (
                card_id,
                card.get("display_name", card_id),
                card.get("network", ""),
                card.get("issuer", ""),
                card.get("last4", ""),
                card.get("kind", ""),
            ),
        )


def prune_unconfigured_cards(conn, root=ROOT):
    configured = set(load_card_payload(root).get("cards", {}))
    if not configured:
        return
    placeholders = ", ".join("?" for _ in configured)
    conn.execute(
        f"""
        DELETE FROM cards
        WHERE card_id NOT IN ({placeholders})
          AND card_id NOT IN (
            SELECT DISTINCT card_id
            FROM order_payments
            WHERE card_id IS NOT NULL
          )
        """,
        sorted(configured),
    )


def default_card_id(root=ROOT):
    payload = load_card_payload(root)
    candidate = payload.get("default_card_id", "unassigned")
    return candidate if candidate in payload.get("cards", {}) else "unassigned"


def upsert_orders(conn, transactions):
    timestamp = now_iso()
    for row in transactions:
        if not row.get("platform") or not row.get("order_id"):
            continue
        date = row.get("date", "")
        year = int_or_none(date[:4])
        month = date[:7] if len(date) >= 7 else ""
        conn.execute(
            """
            INSERT INTO orders (
              platform, order_id, date, year, month, merchant, fulfillment,
              category, item_summary, quantity, item_total, tax, fees, tip,
              total, currency, source_url, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, order_id) DO UPDATE SET
              date=excluded.date,
              year=excluded.year,
              month=excluded.month,
              merchant=excluded.merchant,
              fulfillment=excluded.fulfillment,
              category=excluded.category,
              item_summary=excluded.item_summary,
              quantity=excluded.quantity,
              item_total=excluded.item_total,
              tax=excluded.tax,
              fees=excluded.fees,
              tip=excluded.tip,
              total=excluded.total,
              currency=excluded.currency,
              source_url=excluded.source_url,
              notes=excluded.notes,
              updated_at=excluded.updated_at
            """,
            (
                row.get("platform", ""),
                row.get("order_id", ""),
                date,
                year,
                month,
                row.get("merchant", ""),
                row.get("fulfillment", ""),
                row.get("category", ""),
                row.get("item", ""),
                row.get("quantity", ""),
                money(row.get("item_total")),
                money(row.get("tax")),
                money(row.get("fees")),
                money(row.get("tip")),
                money(row.get("total")),
                row.get("currency", "CAD"),
                row.get("source_url", ""),
                row.get("notes", ""),
                timestamp,
            ),
        )


def order_pk(conn, platform, order_id):
    row = conn.execute(
        "SELECT id FROM orders WHERE platform = ? AND order_id = ?",
        (platform, order_id),
    ).fetchone()
    return row[0] if row else None


def line_key(row):
    return "|".join(str(row[k]) if row.get(k) is not None else ""
                    for k in ("category", "product_name", "quantity", "price"))


def insert_item(conn, row, timestamp):
    pk = order_pk(conn, row.get("platform", ""), row.get("order_id", ""))
    if not pk or not row.get("product_name"):
        return
    conn.execute(
        """
        INSERT INTO order_items (
          order_pk, line_key, category, product_name, quantity, price,
          delivery_status, product_link, currency, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_pk, line_key) DO UPDATE SET
          category=excluded.category,
          product_name=excluded.product_name,
          quantity=excluded.quantity,
          price=excluded.price,
          delivery_status=excluded.delivery_status,
          product_link=excluded.product_link,
          currency=excluded.currency,
          updated_at=excluded.updated_at
        """,
        (
            pk,
            line_key(row),
            row.get("category", ""),
            row.get("product_name", ""),
            row.get("quantity", ""),
            item_price_text(row.get("price")) or None,
            row.get("delivery_status", ""),
            row.get("product_link", ""),
            row.get("currency", "CAD"),
            timestamp,
        ),
    )


def replace_items(conn, item_rows):
    conn.execute("DELETE FROM order_items")
    timestamp = now_iso()
    for row in item_rows:
        insert_item(conn, row, timestamp)


def replace_items_for_orders(conn, item_rows, order_keys=None):
    keys = set(order_keys or [])
    keys.update(tx_key_tuple(row) for row in item_rows if row.get("platform") and row.get("order_id"))
    for platform, order_id in keys:
        pk = order_pk(conn, platform, order_id)
        if pk:
            conn.execute("DELETE FROM order_items WHERE order_pk = ?", (pk,))

    timestamp = now_iso()
    for row in item_rows:
        insert_item(conn, row, timestamp)


def ensure_payments_for_orders(conn, transactions, root=ROOT):
    timestamp = now_iso()
    fallback_card_id = default_card_id(root)
    for tx in transactions:
        pk = order_pk(conn, tx.get("platform", ""), tx.get("order_id", ""))
        if not pk:
            continue
        conn.execute(
            """
            INSERT INTO order_payments (
              order_pk, payment_index, card_id, payment_method, amount,
              currency, status, source, payment_channel, notes, updated_at
            )
            VALUES (?, 1, ?, '', ?, ?, 'needs_review', 'auto_unassigned', ?, '', ?)
            ON CONFLICT(order_pk, payment_index) DO UPDATE SET
              amount=excluded.amount,
              currency=excluded.currency,
              payment_channel=excluded.payment_channel,
              updated_at=excluded.updated_at
            """,
            (
                pk,
                fallback_card_id,
                money(tx.get("total")),
                tx.get("currency", "CAD"),
                (
                    "online"
                    if tx.get("platform") in {
                        "walmart_ca",
                        "amazon_ca",
                        "uber_eats",
                        "fantuan",
                        "tnt_ca",
                    }
                    else "unknown"
                ),
                timestamp,
            ),
        )


def _delete_orders(conn, order_keys):
    deleted = 0
    for platform, order_id in sorted(set(order_keys or [])):
        cursor = conn.execute(
            "DELETE FROM orders WHERE platform = ? AND order_id = ?",
            (platform, order_id),
        )
        deleted += cursor.rowcount
    return deleted


def upsert_orders_and_items_from_rows(
    transactions,
    item_rows,
    root=ROOT,
    db_path=DB_PATH,
    order_keys=None,
    delete_order_keys=None,
):
    root = Path(root)
    with closing(connect(db_path)) as conn:
        upsert_cards(conn, root)
        _delete_orders(conn, delete_order_keys)
        upsert_orders(conn, transactions)
        replace_items_for_orders(conn, item_rows, order_keys)
        ensure_payments_for_orders(conn, transactions, root)
        conn.commit()


def card_payment_method(assignment, evidence):
    if evidence:
        method = evidence.get("payment_method", "")
        last4 = evidence.get("card_last4", "")
        network = evidence.get("card_network", "")
        if last4:
            return f"{network or 'card'} ending {last4}"
        if method:
            return method

    notes = assignment.get("notes", "")
    for marker in ["payment_method=", "receipt_payment="]:
        if marker in notes:
            return notes.split(marker, 1)[1].split(";", 1)[0].strip()
    return ""


def load_evidence(root=ROOT):
    payload = read_json(Path(root) / "data" / "payment_evidence.json", {"evidence": []})
    return {
        tx_key(row): row
        for row in payload.get("evidence", [])
        if row.get("platform") and row.get("order_id")
    }


def load_uber_receipt_evidence(root=ROOT):
    payload = read_json(Path(root) / "data" / "uber_eats_receipts.json", {"receipts": []})
    rows = {}
    for receipt in payload.get("receipts", []):
        order_id = receipt.get("order_id") or receipt.get("workflowUuid") or ""
        if not order_id:
            continue
        snippet = " | ".join(receipt.get("payment_snippets", [])[:3])
        rows[f"uber_eats|{order_id}"] = {
            "platform": "uber_eats",
            "order_id": order_id,
            "source": "uber_receipt_api",
            "payment_method": "card" if receipt.get("card_last4") else "",
            "card_network": receipt.get("card_network", ""),
            "card_last4": receipt.get("card_last4", ""),
            "status": "confirmed" if receipt.get("card_last4") else "not_found",
            "evidence_snippet": snippet[:240],
            "page_url": "",
            "captured_at": payload.get("generated_at", ""),
        }
    return rows


def upsert_evidence_rows(conn, evidence_rows):
    timestamp = now_iso()
    for row in evidence_rows:
        pk = order_pk(conn, row.get("platform", ""), row.get("order_id", ""))
        if not pk:
            continue
        source = row.get("source") or f"{row.get('platform')}_payment_evidence"
        conn.execute(
            """
            INSERT INTO payment_evidence (
              order_pk, source, payment_method, card_network, card_last4,
              status, evidence_snippet, page_url, captured_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_pk, source) DO UPDATE SET
              payment_method=excluded.payment_method,
              card_network=excluded.card_network,
              card_last4=excluded.card_last4,
              status=excluded.status,
              evidence_snippet=excluded.evidence_snippet,
              page_url=excluded.page_url,
              captured_at=excluded.captured_at,
              updated_at=excluded.updated_at
            """,
            (
                pk,
                source,
                row.get("payment_method", ""),
                row.get("card_network", ""),
                row.get("card_last4", ""),
                row.get("status", ""),
                row.get("evidence_snippet", ""),
                row.get("page_url", ""),
                row.get("captured_at", ""),
                timestamp,
            ),
        )


def replace_evidence(conn, evidence_rows):
    conn.execute("DELETE FROM payment_evidence")
    upsert_evidence_rows(conn, evidence_rows)


def replace_payments(conn, transactions, assignments, evidence_by_key, *, replace_all=True):
    timestamp = now_iso()
    assignment_by_key = {tx_key(row): row for row in assignments}
    if replace_all:
        conn.execute("DELETE FROM order_payments")
    for tx in transactions:
        key = tx_key(tx)
        pk = order_pk(conn, tx.get("platform", ""), tx.get("order_id", ""))
        if not pk:
            continue
        assignment = assignment_by_key.get(key, {})
        evidence = evidence_by_key.get(key, {})
        card_id = assignment.get("card_id") or "unassigned"
        method = card_payment_method(assignment, evidence)
        status = assignment.get("assignment_status") or evidence.get("status", "")
        source = assignment.get("assignment_source") or evidence.get("source", "")
        channel = assignment.get("payment_channel") or (
            "online"
            if tx.get("platform") in {
                "walmart_ca",
                "amazon_ca",
                "uber_eats",
                "fantuan",
                "tnt_ca",
            }
            else "unknown"
        )
        notes = assignment.get("notes", "")
        conn.execute(
            """
            INSERT INTO order_payments (
              order_pk, payment_index, card_id, payment_method, amount,
              currency, status, source, payment_channel, notes, updated_at
            )
            VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_pk, payment_index) DO UPDATE SET
              card_id=excluded.card_id, payment_method=excluded.payment_method,
              amount=excluded.amount, currency=excluded.currency, status=excluded.status,
              source=excluded.source, payment_channel=excluded.payment_channel,
              notes=excluded.notes, updated_at=excluded.updated_at
            """,
            (
                pk,
                card_id,
                method,
                money(tx.get("total")),
                tx.get("currency", "CAD"),
                status,
                source,
                channel,
                notes,
                timestamp,
            ),
        )


from tnt_payment_evidence import merge_tnt_evidence

def file_evidence_by_key(root=ROOT):
    evidence_by_key = load_uber_receipt_evidence(root)
    evidence_by_key.update(load_evidence(root))
    merge_tnt_evidence(evidence_by_key, root)
    return evidence_by_key


def sync_evidence_and_payments_from_files(root=ROOT, db_path=DB_PATH):
    root = Path(root)
    with closing(connect(db_path)) as conn:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        _publish_csv_publications(conn, root)
        transactions = read_csv(root / "data" / "transactions.csv", TRANSACTION_FIELDS, recover=False)
        assignments = read_csv(root / "data" / "payment_assignments.csv", recover=False)
        evidence_by_key = file_evidence_by_key(root)
        upsert_cards(conn, root)
        upsert_orders(conn, transactions)
        replace_evidence(conn, evidence_by_key.values())
        replace_payments(conn, transactions, assignments, evidence_by_key)
        prune_unconfigured_cards(conn, root)
        conn.commit()

        return {
            "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            "order_payments": conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0],
            "payment_evidence": conn.execute("SELECT COUNT(*) FROM payment_evidence").fetchone()[0],
        }


def sync_all(root=ROOT, db_path=DB_PATH):
    root = Path(root)
    with closing(connect(db_path)) as conn:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        _publish_csv_publications(conn, root)
        transactions = read_csv(root / "data" / "transactions.csv", TRANSACTION_FIELDS, recover=False)
        item_rows = []
        for item_path in item_staging_paths(root):
            item_rows.extend(read_csv(item_path, ITEM_FIELDS, recover=False))
        assignments = read_csv(root / "data" / "payment_assignments.csv", recover=False)
        evidence_by_key = file_evidence_by_key(root)
        upsert_cards(conn, root)
        upsert_orders(conn, transactions)
        replace_items(conn, item_rows)
        replace_evidence(conn, evidence_by_key.values())
        replace_payments(conn, transactions, assignments, evidence_by_key)
        prune_unconfigured_cards(conn, root)
        conn.commit()

        counts = {
            "cards": conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0],
            "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            "order_items": conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0],
            "order_payments": conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0],
            "payment_evidence": conn.execute("SELECT COUNT(*) FROM payment_evidence").fetchone()[0],
            "total": conn.execute("SELECT COALESCE(SUM(total), 0) FROM orders").fetchone()[0],
        }
    return counts
