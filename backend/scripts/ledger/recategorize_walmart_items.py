"""Re-apply the current Walmart product categorizer to already imported items.

Walmart item categories come only from walmart_cdp.categorize(); user
corrections live in product_category_rules and are not touched here. Run
without --apply for a dry run. With --apply the changed orders are written
through import_orders_with_csv, so the ledger and the CSV mirrors stay in step.
"""
import argparse
import importlib.util
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

LEDGER_DIR = Path(__file__).resolve().parent
CODE_ROOT = LEDGER_DIR.parents[1]
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import (  # noqa: E402
    ITEM_FIELDS,
    TRANSACTION_FIELDS,
    import_orders_with_csv,
    read_csv,
    recover_csv_publications,
    tx_key_tuple,
)

PLATFORM = "walmart_ca"
AUTO_ORDER_CATEGORIES = {"", "mixed", "uncategorized", "groceries", "household", "kitchenware"}
PLACEHOLDERS = {"bank-reconciled walmart charge", "bank reconciled walmart charge",
                "bank-recorded purchase", "bank recorded purchase"}
COMPARED_FIELDS = ("date", "total", "item_total", "tax", "fees", "tip", "currency")


def load_categorize():
    path = CODE_ROOT / "scripts" / "importers" / "walmart_cdp.py"
    spec = importlib.util.spec_from_file_location("walmart_cdp_categorize", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.categorize


def plan(root, categorize):
    data = Path(root) / "data"
    transactions = read_csv(data / "transactions.csv", TRANSACTION_FIELDS, recover=False)
    items = read_csv(data / "walmart_items.csv", ITEM_FIELDS, recover=False)
    orders = {tx_key_tuple(row): row for row in transactions if row["platform"] == PLATFORM}
    items_by_order = {}
    changes = []
    for row in items:
        if row["platform"] != PLATFORM:
            continue
        items_by_order.setdefault(tx_key_tuple(row), []).append(row)
        name = row["product_name"].strip()
        if name.lower() in PLACEHOLDERS:
            continue
        new = categorize(name)
        if new != (row["category"] or ""):
            changes.append((row["category"], new, name))
            row["category"] = new

    touched = {tx_key_tuple(row) for row in items
               if row["platform"] == PLATFORM
               and any(change[2] == row["product_name"].strip() for change in changes)}
    changed_orders = []
    for key in sorted(touched):
        order = orders.get(key)
        if order is None:
            raise SystemExit(f"Item order {key} is missing from transactions.csv; nothing changed.")
        categories = sorted({row["category"] for row in items_by_order[key]})
        if order["category"] in AUTO_ORDER_CATEGORIES:
            order["category"] = categories[0] if len(categories) == 1 else "mixed"
        changed_orders.append(order)
    return changes, changed_orders, items_by_order


def check_ledger_matches_csv(root, orders):
    """Refuse to rewrite orders whose ledger values drifted from the CSV mirror."""
    db_path = Path(root) / "data" / "expenses.sqlite"
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        for order in orders:
            row = conn.execute(
                "SELECT * FROM orders WHERE platform=? AND order_id=?",
                (order["platform"], order["order_id"]),
            ).fetchone()
            if row is None:
                raise SystemExit(f"Order {order['order_id']} is not in the ledger; nothing changed.")
            for field in COMPARED_FIELDS:
                csv_value, db_value = order[field], row[field]
                if field == "date" or field == "currency":
                    same = str(csv_value) == str(db_value)
                else:
                    same = round(float(csv_value or 0), 2) == round(float(db_value or 0), 2)
                if not same:
                    raise SystemExit(
                        f"Order {order['order_id']} {field} differs (csv={csv_value!r}, "
                        f"ledger={db_value!r}); nothing changed."
                    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(CODE_ROOT))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    recover_csv_publications(root)
    changes, orders, items_by_order = plan(root, load_categorize())
    for old, new, name in changes:
        print(f"{old or '(blank)':>13} -> {new:<13} {name}")
    print(f"{len(changes)} item line(s) in {len(orders)} order(s) would change.")
    if not orders or not args.apply:
        return
    check_ledger_matches_csv(root, orders)
    item_rows = [row for order in orders for row in items_by_order[tx_key_tuple(order)]]
    import_orders_with_csv(orders, item_rows, root=root, item_filename="walmart_items.csv")
    print("Applied to data/expenses.sqlite, data/transactions.csv and data/walmart_items.csv.")


if __name__ == "__main__":
    main()
