import json
import os
import re
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

from db import import_orders_with_csv, item_price_text, recover_csv_publications


def money(value):
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def is_cancelled_status(value):
    status = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return bool(re.search(r"已取消|取消成功|订单取消|cancelled|canceled", status))


def fulfillment_status(value):
    status = str(value or "").strip().casefold()
    if re.search(r"已完成|已送达|completed|delivered", status):
        return "delivered"
    if re.search(r"预约|预订单|scheduled|pre.?order", status):
        return "scheduled"
    if re.search(r"配送中|送餐中|骑手配送|on the way|out for delivery|in transit", status):
        return "in_transit"
    if re.search(r"等待.*接单|待接单|awaiting.*accept|pending.*accept", status):
        return "pending_acceptance"
    if re.search(r"备餐|制作|已接单|preparing|accepted", status):
        return "preparing"
    if re.search(r"退款成功|refunded", status):
        return "refunded"
    return "pending"


def normalize_payload(payload):
    transactions = []
    items = []
    owned_keys = set()
    excluded_keys = set()
    for order in payload.get("orders", []):
        order_id = str(order.get("order_id") or "").strip()
        if not order_id:
            continue
        if re.search(r"待支付|未支付|等待支付|unpaid|awaiting payment", str(order.get("status") or ""), re.I):
            continue
        key = ("fantuan", order_id)
        owned_keys.add(key)
        if is_cancelled_status(order.get("status")):
            excluded_keys.add(key)
            continue

        fulfillment = fulfillment_status(order.get("status"))
        goods = order.get("goods") or []
        item_names = [item.get("name", "") for item in goods if item.get("name")]
        transactions.append({
            "date": order.get("date", ""),
            "platform": "fantuan",
            "order_id": order_id,
            "merchant": order.get("merchant", ""),
            "fulfillment": fulfillment,
            "category": order.get("category", "food_delivery"),
            "item": "; ".join(item_names) or order.get("item", ""),
            "quantity": order.get("quantity", ""),
            "item_total": money(order.get("item_total")),
            "tax": money(order.get("tax")),
            "fees": money(order.get("fees")),
            "tip": money(order.get("tip")),
            "total": money(order.get("total")),
            "currency": order.get("currency", "CAD"),
            "source_url": order.get("source_url", ""),
            "notes": order.get("notes", ""),
        })

        for item in goods:
            items.append({
                "date": order.get("date", ""),
                "platform": "fantuan",
                "order_id": order_id,
                "category": order.get("category", "food_delivery"),
                "product_name": item.get("name", ""),
                "quantity": item.get("quantity", ""),
                "price": item_price_text(item.get("price")),
                "delivery_status": fulfillment,
                "product_link": "",
                "currency": order.get("currency", "CAD"),
            })
    return transactions, items, owned_keys, excluded_keys


def import_file(root=ROOT):
    root = Path(root)
    recover_csv_publications(root)
    source_path = root / "data" / "fantuan_orders.json"
    if not source_path.exists():
        return {
            "orders": 0,
            "items": 0,
            "excluded_cancelled": 0,
            "message": "No Fantuan sanitized order export found.",
        }

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    orders = payload.get("orders", [])
    if not orders:
        return {
            "orders": 0,
            "items": 0,
            "excluded_cancelled": 0,
            "message": f"No Fantuan orders found in {source_path}.",
        }

    transactions, items, owned_keys, excluded_keys = normalize_payload(payload)

    import_orders_with_csv(transactions, items, root=root,
                           item_filename='delivery_items.csv', delete_order_keys=excluded_keys)
    return {
        "orders": len(transactions),
        "items": len(items),
        "excluded_cancelled": len(excluded_keys),
        "message": (
            f"Imported {len(transactions)} Fantuan orders and {len(items)} item rows "
            f"from {source_path}; excluded {len(excluded_keys)} cancelled order(s)."
        ),
    }


def main():
    result = import_file(ROOT)
    print(result["message"])
    print("Updated data/expenses.sqlite")


if __name__ == "__main__":
    main()
