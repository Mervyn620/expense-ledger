import argparse
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs, urlparse


CODE_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE = str(os.environ.get("EXPENSE_COLLECTOR_WORKSPACE_ROOT") or "").strip()
ROOT = Path(_WORKSPACE).resolve() if _WORKSPACE else CODE_ROOT
if _WORKSPACE and (not Path(_WORKSPACE).is_absolute() or Path(_WORKSPACE).is_symlink() or ROOT != Path.cwd().resolve()):
    raise RuntimeError("Collector workspace environment is invalid.")
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import import_orders_with_csv, recover_csv_publications


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

MONTHS = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "sept": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}

MIN_DATE = "2026-05-01"
PLATFORM = "uber_eats"

ACTION_LINES = {
    "Get Help",
    "Past Orders",
    "Rate your order",
    "Reorder",
    "Request Invoice",
    "Search Uber Eats",
    "Show more",
    "View receipt",
    "View store",
}


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def money(value):
    match = re.search(r"CA\$(\d+(?:\.\d{2})?)", value or "")
    return match.group(1) if match else ""


def normalize_date(text, year="2026"):
    match = re.search(r"\b([A-Za-z]{3,})\s+(\d{1,2})\b", text or "")
    if not match:
        return ""
    month_name, day = match.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return ""
    return f"{year}-{month}-{int(day):02d}"


def is_order_summary(line):
    return bool(re.search(r"\b\d+\s+items?\s+for\s+CA\$", line or ""))


def is_rating(line):
    # The order list can show the user's rating between the store name and the
    # "N items for CA$" summary, e.g. "5 out of 5 stars".
    return bool(re.fullmatch(
        r"(?:rated\s+)?\d(?:\.\d)?\s+out\s+of\s+5(?:\s+stars?)?",
        clean(line),
        re.IGNORECASE,
    ))


def is_marker(line):
    value = clean(line)
    if not value:
        return True
    ascii_part = value.encode("ascii", "ignore").decode("ascii").strip(" ?.-")
    return len(value) <= 4 and not ascii_part


def is_noise(line):
    value = clean(line)
    if not value or value in ACTION_LINES or is_marker(value) or is_rating(value):
        return True
    return value.lower().startswith("delivered")


def quantity_line(line):
    match = re.match(r"^(\d+)$", clean(line))
    return match.group(1) if match else ""


def count_line(line):
    match = re.match(r"^(\d+)\s+count$", clean(line), re.IGNORECASE)
    return match.group(1) if match else ""


def category_for_merchant(merchant):
    if merchant.lower() == "walmart":
        return "grocery_delivery"
    return "food_delivery"


def upsert(existing, incoming, fields, key_func):
    merged = OrderedDict()
    for row in existing:
        merged[key_func(row)] = {field: row.get(field, "") for field in fields}
    for row in incoming:
        merged[key_func(row)] = {field: row.get(field, "") for field in fields}
    return list(merged.values())


def receipt_ids(links):
    ids = []
    for link in links:
        if clean(link.get("text")) != "View receipt":
            continue
        query = parse_qs(urlparse(link.get("href", "")).query)
        order_id = (query.get("modctx") or [""])[0]
        if order_id:
            ids.append((order_id, link.get("href", "")))
    return ids


def order_start_indexes(lines):
    starts = []
    for index in range(1, len(lines)):
        if not is_order_summary(lines[index]) or is_order_summary(lines[index - 1]):
            continue
        start = index - 1
        while start > 0 and is_rating(lines[start]):
            start -= 1
        if is_rating(lines[start]) or (starts and start <= starts[-1]):
            continue
        starts.append(start)
    return starts


def next_non_noise(block, index):
    for cursor in range(index + 1, len(block)):
        line = block[cursor]
        if is_noise(line):
            continue
        return line
    return ""


def extract_items(block, date_index):
    items = []
    seen = set()
    pending_quantity = ""

    def add_item(name, quantity=""):
        product_name = clean(name)
        if not product_name or product_name in seen:
            return
        seen.add(product_name)
        items.append({
            "product_name": product_name,
            "quantity": quantity,
        })

    for index in range(date_index + 1, len(block)):
        line = clean(block[index])
        if is_noise(line):
            continue

        quantity = quantity_line(line)
        if quantity:
            pending_quantity = quantity
            continue

        count = count_line(line)
        if count:
            if items and not items[-1]["quantity"]:
                items[-1]["quantity"] = count
            pending_quantity = ""
            continue

        next_count = count_line(next_non_noise(block, index))
        if pending_quantity or next_count or not items:
            add_item(line, pending_quantity or next_count)
            pending_quantity = ""

    return items


def parse_orders(snapshot, min_date=MIN_DATE):
    lines = [clean(line) for line in snapshot.get("lines", [])]
    receipts = receipt_ids(snapshot.get("links", []))
    starts = order_start_indexes(lines)
    snapshot_year = clean(snapshot.get("capturedAt", ""))[:4] or "2026"
    orders = []

    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        merchant = block[0]
        summary_index = next(
            (cursor for cursor in range(1, len(block)) if is_order_summary(block[cursor])),
            1,
        )
        summary = block[summary_index] if len(block) > summary_index else ""
        total = money(summary)
        quantity_match = re.search(r"\b(\d+)\s+items?\s+for\s+CA\$", summary)
        quantity = quantity_match.group(1) if quantity_match else ""

        date = ""
        date_index = 0
        for cursor in range(summary_index + 1, len(block)):
            date = normalize_date(block[cursor], snapshot_year)
            if date:
                date_index = cursor
                break

        if not date or date < min_date:
            continue

        if position >= len(receipts):
            order_id = f"{date}|{merchant}|{total}"
            source_url = snapshot.get("url", "")
        else:
            order_id, source_url = receipts[position]

        items = extract_items(block, date_index)
        item_names = [item["product_name"] for item in items]
        category = category_for_merchant(merchant)
        orders.append({
            "transaction": {
                "date": date,
                "platform": PLATFORM,
                "order_id": order_id,
                "merchant": merchant,
                "fulfillment": "delivered",
                "category": category,
                "item": "; ".join(item_names[:8]),
                "quantity": quantity,
                "item_total": total,
                "tax": "0.00",
                "fees": "0.00",
                "tip": "0.00",
                "total": total,
                "currency": "CAD",
                "source_url": source_url,
                "notes": (
                    "Imported from Uber Eats order list; total includes taxes/fees/tip "
                    "if shown by Uber. Tax/fee/tip breakdown is not captured (stored as 0)."
                ),
            },
            "items": [
                {
                    "date": date,
                    "platform": PLATFORM,
                    "order_id": order_id,
                    "category": category,
                    "product_name": item["product_name"],
                    "quantity": item["quantity"],
                    "price": "",
                    "delivery_status": "delivered",
                    "product_link": "",
                    "currency": "CAD",
                }
                for item in items
            ],
        })

    return orders


def latest_snapshot():
    files = sorted((ROOT / "raw").glob("*_uber_eats_*.json"), key=lambda path: path.stat().st_mtime)
    if not files:
        return None
    return files[-1]


def main():
    recover_csv_publications(ROOT)
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-date", default=MIN_DATE)
    args = parser.parse_args()

    path = latest_snapshot()
    if not path:
        print("No Uber Eats snapshot found.")
        return

    snapshot = json.loads(path.read_text(encoding="utf-8"))
    orders = parse_orders(snapshot, args.min_date)
    transactions = [entry["transaction"] for entry in orders]
    items = [item for entry in orders for item in entry["items"]]

    if not transactions:
        print(f"No Uber Eats orders parsed since {args.min_date} from {path}.")
        return

    import_orders_with_csv(transactions, items, root=ROOT, item_filename='delivery_items.csv')
    print(f"Imported {len(transactions)} Uber Eats orders and {len(items)} item labels from {path}.")
    print("Updated data/expenses.sqlite")


if __name__ == "__main__":
    main()
