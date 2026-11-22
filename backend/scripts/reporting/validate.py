import csv
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = ROOT / "scripts" / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import item_staging_paths


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def find_duplicates(rows, fields):
    counts = Counter(tuple(row.get(field, "") for field in fields) for row in rows)
    return [(key, count) for key, count in counts.items() if count > 1]


def money(row, field):
    try:
        return float(row.get(field) or 0)
    except ValueError:
        return 0.0


def write_validation_status(path, status, metrics, failures):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "exit_code": 0 if status == "passed" else 1,
        "metrics": metrics,
        "failures": failures,
        "message": "账本校验通过" if status == "passed" else f"账本校验发现 {len(failures)} 个问题",
    }
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def main(root=ROOT, status_output=None):
    root = Path(root)
    transactions_path = root / "data" / "transactions.csv"
    database = root / "data" / "expenses.sqlite"
    payment_assignments_path = root / "data" / "payment_assignments.csv"
    item_files = item_staging_paths(root)
    status_output = Path(
        status_output or (root / "exports" / "current" / "validation-status.json")
    )

    failures = []
    metrics = {}

    def emit(key, value):
        metrics[key] = value
        print(f"{key}={value}")

    transactions = read_csv(transactions_path)
    tx_dupes = find_duplicates(transactions, ["platform", "order_id"])
    if tx_dupes:
        failures.append(f"duplicate transaction keys: {tx_dupes[:10]}")

    emit("transactions_rows", len(transactions))
    emit(
        "unique_transactions",
        len({(row.get("platform"), row.get("order_id")) for row in transactions}),
    )
    emit("transaction_duplicates", len(tx_dupes))
    emit("transaction_total", round(sum(money(row, "total") for row in transactions), 2))

    reconciliation_failures = []
    for row in transactions:
        component_total = (
            money(row, "item_total")
            + money(row, "tax")
            + money(row, "fees")
            + money(row, "tip")
        )
        total = money(row, "total")
        if abs(component_total - total) > 0.02:
            reconciliation_failures.append(
                f"{row.get('platform')} {row.get('order_id')} components={component_total:.2f} total={total:.2f}"
            )
    if reconciliation_failures:
        failures.append(f"transaction reconciliation failures: {reconciliation_failures[:10]}")
    emit("transaction_reconciliation_failures", len(reconciliation_failures))

    item_file_rows = 0
    item_file_duplicates = 0
    item_file_subtotal = 0.0
    for item_path in item_files:
        items = read_csv(item_path)
        if not items:
            continue
        item_file_rows += len(items)
        key_fields = ["platform", "order_id", "product_name", "quantity", "price"]
        missing = [field for field in key_fields if field not in items[0]]
        if missing:
            failures.append(f"{item_path} missing key fields: {missing}")
            continue

        item_dupes = find_duplicates(items, key_fields)
        if item_dupes:
            failures.append(f"{item_path} duplicate item keys: {item_dupes[:10]}")
        item_file_duplicates += len(item_dupes)
        subtotal = sum(money(row, "price") for row in items)
        item_file_subtotal += subtotal
        print(f"{item_path.as_posix()}_rows={len(items)}")
        print(f"{item_path.as_posix()}_duplicates={len(item_dupes)}")
        print(f"{item_path.as_posix()}_subtotal={subtotal:.2f}")

    emit("item_file_rows", item_file_rows)
    emit("item_file_duplicates", item_file_duplicates)
    emit("item_file_subtotal", round(item_file_subtotal, 2))

    assignments = read_csv(payment_assignments_path)
    assignment_dupes = find_duplicates(assignments, ["platform", "order_id"])
    assignment_keys = {(row.get("platform"), row.get("order_id")) for row in assignments}
    transaction_keys = {(row.get("platform"), row.get("order_id")) for row in transactions}
    missing_assignments = sorted(transaction_keys - assignment_keys)
    extra_assignments = sorted(assignment_keys - transaction_keys)
    if assignment_dupes:
        failures.append(f"duplicate payment assignments: {assignment_dupes[:10]}")
    if missing_assignments:
        failures.append(f"transactions missing payment assignments: {missing_assignments[:10]}")
    if extra_assignments:
        failures.append(f"payment assignments without transactions: {extra_assignments[:10]}")
    emit("payment_assignments_rows", len(assignments))
    emit("payment_assignment_duplicates", len(assignment_dupes))
    emit("payment_assignment_missing", len(missing_assignments))
    emit("payment_assignment_extra", len(extra_assignments))

    if not database.exists():
        failures.append(f"SQLite database not found: {database}")
    else:
        with closing(sqlite3.connect(database)) as conn:
            has_outbox = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_csv_publications'").fetchone()
            pending = conn.execute('SELECT COUNT(*) FROM pending_csv_publications').fetchone()[0] if has_outbox else 0
            emit('pending_csv_publications', pending)
            if pending:
                failures.append('Committed CSV publications are pending; run ledger/sync.py --recover-csv before continuing.')
            db_orders, db_total = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(total), 0) FROM orders"
            ).fetchone()
            db_items = conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
            db_payments = conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0]
            db_evidence = conn.execute("SELECT COUNT(*) FROM payment_evidence").fetchone()[0]
            db_order_dupes = conn.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT platform, order_id
                  FROM orders
                  GROUP BY platform, order_id
                  HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            db_item_dupes = conn.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT order_pk, line_key
                  FROM order_items
                  GROUP BY order_pk, line_key
                  HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            orphan_items = conn.execute(
                """
                SELECT COUNT(*)
                FROM order_items i
                LEFT JOIN orders o ON o.id = i.order_pk
                WHERE o.id IS NULL
                """
            ).fetchone()[0]
            orphan_payments = conn.execute(
                """
                SELECT COUNT(*)
                FROM order_payments p
                LEFT JOIN orders o ON o.id = p.order_pk
                WHERE o.id IS NULL
                """
            ).fetchone()[0]
            payment_mismatches = conn.execute(
                """
                SELECT COUNT(*)
                FROM orders o
                LEFT JOIN order_payments p
                  ON p.order_pk = o.id AND p.payment_index = 1
                WHERE p.id IS NULL
                   OR ABS(p.amount - o.total) > 0.02
                """
            ).fetchone()[0]
            bank_source_misclassifications = conn.execute(
                """
                SELECT COUNT(*)
                FROM orders
                WHERE (order_id LIKE 'scotia-%' AND platform <> 'scotiabank')
                   OR (order_id LIKE 'bank-%' AND platform <> 'bank')
                """
            ).fetchone()[0]
            walmart_bank_only_orders = conn.execute(
                """
                SELECT COUNT(*)
                FROM orders
                WHERE (order_id LIKE 'scotia-%' OR order_id LIKE 'bank-%')
                  AND LOWER(merchant) = 'walmart'
                """
            ).fetchone()[0]
            collector_bank_only_orders = conn.execute(
                """
                SELECT COUNT(*)
                FROM orders
                WHERE (order_id LIKE 'scotia-%' OR order_id LIKE 'bank-%')
                  AND (
                    notes LIKE '%detected merchant platform=walmart_ca%'
                    OR notes LIKE '%detected merchant platform=amazon_ca%'
                    OR notes LIKE '%detected merchant platform=uber_eats%'
                    OR notes LIKE '%detected merchant platform=fantuan%'
                  )
                  AND category NOT IN ('delivery_membership', 'retail_membership')
                """
            ).fetchone()[0]
            chequing_payment_rows = conn.execute(
                """
                SELECT COUNT(*)
                FROM order_payments
                WHERE card_id = 'scotia_chequing_3333'
                   OR card_id IN (SELECT account_id FROM bank_accounts)
                """
            ).fetchone()[0]
            missing_payment_channels = conn.execute(
                """
                SELECT COUNT(*)
                FROM order_payments
                WHERE COALESCE(payment_channel, '') = ''
                """
            ).fetchone()[0]
            invalid_inferred_payments = conn.execute(
                """
                SELECT COUNT(*)
                FROM order_payments
                WHERE status = 'inferred'
                  AND (
                    COALESCE(card_id, '') IN ('', 'unassigned', 'account_debit')
                    OR COALESCE(payment_channel, '') IN ('', 'unknown', 'account_debit')
                  )
                """
            ).fetchone()[0]
            bank_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            bank_transactions = 0
            bank_reconciliations = 0
            bank_orphan_reconciliations = 0
            bank_imports_without_orders = 0
            if {"bank_transactions", "bank_reconciliations"}.issubset(bank_tables):
                bank_transactions = conn.execute(
                    "SELECT COUNT(*) FROM bank_transactions"
                ).fetchone()[0]
                bank_reconciliations = conn.execute(
                    "SELECT COUNT(*) FROM bank_reconciliations"
                ).fetchone()[0]
                bank_orphan_reconciliations = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM bank_reconciliations r
                    LEFT JOIN bank_transactions t ON t.id = r.bank_transaction_pk
                    WHERE t.id IS NULL
                    """
                ).fetchone()[0]
                bank_imports_without_orders = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM bank_reconciliations
                    WHERE match_status = 'bank_only_imported'
                      AND order_pk IS NULL
                    """
                ).fetchone()[0]

        csv_total = sum(money(row, "total") for row in transactions)
        csv_items = sum(len(read_csv(path)) for path in item_files)
        if db_orders != len(transactions):
            failures.append(f"SQLite order count {db_orders} != CSV order count {len(transactions)}")
        if abs(db_total - csv_total) > 0.02:
            failures.append(f"SQLite total {db_total:.2f} != CSV total {csv_total:.2f}")
        if db_items != csv_items:
            failures.append(f"SQLite item count {db_items} != CSV item count {csv_items}")
        if db_payments != db_orders:
            failures.append(f"SQLite payment count {db_payments} != order count {db_orders}")
        if db_order_dupes:
            failures.append(f"SQLite duplicate orders: {db_order_dupes}")
        if db_item_dupes:
            failures.append(f"SQLite duplicate item keys: {db_item_dupes}")
        if orphan_items or orphan_payments:
            failures.append(f"SQLite orphans: items={orphan_items} payments={orphan_payments}")
        if payment_mismatches:
            failures.append(f"SQLite missing/mismatched payments: {payment_mismatches}")
        if bank_source_misclassifications:
            failures.append(
                "bank-only orders assigned to merchant platforms: "
                f"{bank_source_misclassifications}"
            )
        if walmart_bank_only_orders:
            failures.append(
                "unmatched Walmart bank charges imported as orders: "
                f"{walmart_bank_only_orders}"
            )
        if collector_bank_only_orders:
            failures.append(
                "collector-backed bank charges imported without platform orders: "
                f"{collector_bank_only_orders}"
            )
        if chequing_payment_rows:
            failures.append(
                "bank accounts used as payment instruments: "
                f"{chequing_payment_rows}"
            )
        if missing_payment_channels:
            failures.append(
                f"payments missing channel classification: {missing_payment_channels}"
            )
        if invalid_inferred_payments:
            failures.append(
                f"invalid inferred payment assignments: {invalid_inferred_payments}"
            )
        if bank_reconciliations not in {0, bank_transactions}:
            failures.append(
                f"bank reconciliation count {bank_reconciliations} "
                f"!= bank transaction count {bank_transactions}"
            )
        if bank_orphan_reconciliations:
            failures.append(
                f"bank reconciliation orphans: {bank_orphan_reconciliations}"
            )
        if bank_imports_without_orders:
            failures.append(
                f"bank-only imports without linked orders: {bank_imports_without_orders}"
            )

        emit("sqlite_orders", db_orders)
        emit("sqlite_items", db_items)
        emit("sqlite_payments", db_payments)
        emit("sqlite_evidence", db_evidence)
        emit("sqlite_total", round(db_total, 2))
        emit("sqlite_order_duplicates", db_order_dupes)
        emit("sqlite_item_duplicates", db_item_dupes)
        emit("sqlite_orphan_items", orphan_items)
        emit("sqlite_orphan_payments", orphan_payments)
        emit("sqlite_payment_mismatches", payment_mismatches)
        emit("bank_source_misclassifications", bank_source_misclassifications)
        emit("walmart_bank_only_orders", walmart_bank_only_orders)
        emit("collector_bank_only_orders", collector_bank_only_orders)
        emit("chequing_payment_rows", chequing_payment_rows)
        emit("missing_payment_channels", missing_payment_channels)
        emit("invalid_inferred_payments", invalid_inferred_payments)
        emit("bank_transactions", bank_transactions)
        emit("bank_reconciliations", bank_reconciliations)
        emit("bank_orphan_reconciliations", bank_orphan_reconciliations)
        emit("bank_imports_without_orders", bank_imports_without_orders)

    status = "failed" if failures else "passed"
    write_validation_status(status_output, status, metrics, failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
