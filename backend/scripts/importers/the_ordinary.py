import json
import os
import sys
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE = str(os.environ.get("EXPENSE_COLLECTOR_WORKSPACE_ROOT") or "").strip()
ROOT = Path(_WORKSPACE).resolve() if _WORKSPACE else CODE_ROOT
if _WORKSPACE and (not Path(_WORKSPACE).is_absolute() or Path(_WORKSPACE).is_symlink() or ROOT != Path.cwd().resolve()):
    raise RuntimeError("Collector workspace environment is invalid.")
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import (
    import_orders_with_csv,
    item_price_text,
    recover_csv_publications,
)


PLATFORM = "the_ordinary_ca"


def money(value):
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def normalize_payload(payload):
    transactions = []
    items = []
    for order in payload.get("orders", []):
        order_id = str(order.get("order_id") or "").strip()
        date = str(order.get("date") or "").strip()
        total = money(order.get("total"))
        if not order_id or not date or float(total) <= 0:
            continue

        order_items = []
        for item in order.get("items") or []:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            quantity = str(max(1, int(item.get("quantity") or 1)))
            items.append({
                "date": date,
                "platform": PLATFORM,
                "order_id": order_id,
                "category": "personal_care",
                "product_name": title,
                "quantity": quantity,
                "price": item_price_text(item.get("line_total")) if item.get("line_total") or item.get("price_known") else "",
                "delivery_status": order.get("fulfillment", ""),
                "product_link": order.get("product_url", "") or item.get("product_url", ""),
                "currency": order.get("currency", "CAD"),
            })
            order_items.append(title)

        transactions.append({
            "date": date,
            "platform": PLATFORM,
            "order_id": order_id,
            "merchant": "The Ordinary",
            "fulfillment": order.get("fulfillment", ""),
            "category": order.get("category", "personal_care"),
            "item": "; ".join(order_items) or order.get("item", ""),
            "quantity": order.get("quantity", ""),
            "item_total": money(order.get("item_total")),
            "tax": money(order.get("tax")),
            "fees": money(order.get("fees")),
            "tip": "0.00",
            "total": total,
            "currency": order.get("currency", "CAD"),
            "source_url": order.get("source_url", ""),
            "notes": order.get("notes", ""),
        })
    return transactions, items


def import_file(root=ROOT):
    root = Path(root)
    recover_csv_publications(root)
    source_path = root / "data" / "the_ordinary_orders.json"
    if not source_path.exists():
        return {"orders": 0, "items": 0, "message": "No The Ordinary sanitized order export found."}

    payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
    if payload.get("contract") != "expense-the-ordinary-orders/v1":
        raise ValueError("The Ordinary order export contract is invalid.")
    transactions, items = normalize_payload(payload)
    if not transactions:
        return {
            "orders": 0,
            "items": 0,
            "message": f"No The Ordinary orders found in {source_path}.",
        }

    import_orders_with_csv(transactions, items, root=root, item_filename='the_ordinary_items.csv')
    return {
        "orders": len(transactions),
        "items": len(items),
        "message": (
            f"Imported {len(transactions)} The Ordinary orders and "
            f"{len(items)} item rows from {source_path}."
        ),
    }


def main():
    result = import_file()
    print(result["message"])
    if result["orders"]:
        print("Updated data/expenses.sqlite")


if __name__ == "__main__":
    main()
