import argparse
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import (
    read_csv,
    upsert_orders_and_items_from_rows,
    write_csv,
)


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
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "sept": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}


def clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def money(value):
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", str(value))
    if not match:
        return ""
    return f"{float(match.group(0).replace(',', '')):.2f}"


def normalize_date(value):
    text = clean(value)
    if not text:
        return ""

    if isinstance(value, datetime):
        return value.date().isoformat()

    iso = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if iso:
        year, month, day = iso.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    named = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if named:
        month_name, day, year = named.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            return f"{year}-{month}-{int(day):02d}"

    return text


def row_values(row):
    return [clean(cell.value) for cell in row]


def find_header(rows, required):
    required_lower = {item.lower() for item in required}
    for index, values in enumerate(rows):
        lowered = {value.lower() for value in values if value}
        if required_lower.issubset(lowered):
            return index, values
    return None, None


def parse_combined_sheet(sheet):
    rows = [row_values(row) for row in sheet.iter_rows()]
    header_index, headers = find_header(rows, ["Order Number", "Product Name"])
    if header_index is None:
        return [], []

    index_by_name = {header: idx for idx, header in enumerate(headers)}

    def get(values, name):
        idx = index_by_name.get(name)
        return values[idx] if idx is not None and idx < len(values) else ""

    order_items = []
    for values in rows[header_index + 1:]:
        order_id = get(values, "Order Number")
        product = get(values, "Product Name")
        if not order_id and not product:
            continue
        order_items.append({
            "order_id": order_id,
            "date": normalize_date(get(values, "Order Date")),
            "product_name": product,
            "quantity": clean(get(values, "Quantity")),
            "price": money(get(values, "Price")),
            "delivery_status": get(values, "Delivery Status"),
            "product_link": get(values, "Product Link"),
            "subtotal": money(get(values, "Subtotal")),
            "total": money(get(values, "Order Total")),
            "delivery_charges": money(get(values, "Delivery Charges")),
            "tax": money(get(values, "Tax")),
            "tip": money(get(values, "Tip")),
        })
    return build_order_rows(order_items), order_items


def parse_single_invoice_sheet(sheet):
    rows = [row_values(row) for row in sheet.iter_rows()]
    header_index, headers = find_header(rows, ["Product Name", "Quantity", "Price"])
    if header_index is None:
        return [], []

    index_by_name = {header: idx for idx, header in enumerate(headers)}

    def get(values, name):
        idx = index_by_name.get(name)
        return values[idx] if idx is not None and idx < len(values) else ""

    summary = {}
    for values in rows:
        if len(values) >= 2 and values[0] in {
            "Order Number",
            "Order Date",
            "Subtotal",
            "Delivery Charges",
            "Tax",
            "Tip",
            "Order Total",
        }:
            summary[values[0]] = values[1]

    order_items = []
    for values in rows[header_index + 1:]:
        product = get(values, "Product Name")
        if not product:
            continue
        if product in {"Order Number", "Order Date", "Subtotal", "Order Total"}:
            continue
        order_items.append({
            "order_id": summary.get("Order Number", ""),
            "date": normalize_date(summary.get("Order Date", "")),
            "product_name": product,
            "quantity": clean(get(values, "Quantity")),
            "price": money(get(values, "Price")),
            "delivery_status": get(values, "Delivery Status"),
            "product_link": get(values, "Product Link"),
            "subtotal": money(summary.get("Subtotal", "")),
            "total": money(summary.get("Order Total", "")),
            "delivery_charges": money(summary.get("Delivery Charges", "")),
            "tax": money(summary.get("Tax", "")),
            "tip": money(summary.get("Tip", "")),
        })
    return build_order_rows(order_items), order_items


def build_order_rows(order_items):
    grouped = OrderedDict()
    for item in order_items:
        order_id = item["order_id"]
        if not order_id:
            continue
        grouped.setdefault(order_id, []).append(item)

    rows = []
    for order_id, items in grouped.items():
        first = items[0]
        names = [item["product_name"] for item in items if item["product_name"]]
        statuses = sorted({item["delivery_status"] for item in items if item["delivery_status"]})
        rows.append({
            "date": first["date"],
            "platform": "walmart_ca",
            "order_id": order_id,
            "merchant": "Walmart",
            "fulfillment": "; ".join(statuses),
            "category": "uncategorized",
            "item": "; ".join(names[:12]),
            "quantity": str(len(items)),
            "item_total": first["subtotal"],
            "tax": first["tax"],
            "fees": first["delivery_charges"],
            "tip": first["tip"],
            "total": first["total"] or first["subtotal"],
            "currency": "CAD",
            "source_url": "",
            "notes": f"{len(items)} item lines",
        })
    return rows


def parse_workbook(path):
    workbook = load_workbook(path, data_only=True, read_only=True)
    transaction_rows = []
    item_rows = []
    for sheet in workbook.worksheets:
        rows, items = parse_combined_sheet(sheet)
        if not rows and not items:
            rows, items = parse_single_invoice_sheet(sheet)
        transaction_rows.extend(rows)
        item_rows.extend(items)
    return transaction_rows, item_rows


def upsert(existing, incoming, fields, key_func):
    merged = OrderedDict()
    for row in existing:
        merged[key_func(row)] = {field: row.get(field, "") for field in fields}
    for row in incoming:
        merged[key_func(row)] = {field: row.get(field, "") for field in fields}
    return list(merged.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="downloads", help="XLSX file or folder to import")
    parser.add_argument("--ledger", default="data/transactions.csv")
    parser.add_argument("--items", default="data/walmart_items.csv")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    if not input_path.exists():
        if input_path.suffix:
            raise FileNotFoundError(f"Input file not found: {input_path}")
        input_path.mkdir(parents=True, exist_ok=True)
        files = []
    elif input_path.is_dir():
        files = sorted(input_path.glob("*.xlsx"))
    else:
        if input_path.suffix.lower() != ".xlsx":
            raise ValueError(f"Input file is not an .xlsx file: {input_path}")
        files = [input_path]

    imported_transactions = []
    imported_items = []
    for file in files:
        rows, items = parse_workbook(file)
        imported_transactions.extend(rows)
        imported_items.extend(items)

    ledger_path = Path(args.ledger)
    if not ledger_path.is_absolute():
        ledger_path = ROOT / ledger_path
    items_path = Path(args.items)
    if not items_path.is_absolute():
        items_path = ROOT / items_path

    existing_transactions = read_csv(ledger_path, TRANSACTION_FIELDS)
    existing_items = read_csv(items_path, ITEM_FIELDS)

    transactions = upsert(
        existing_transactions,
        imported_transactions,
        TRANSACTION_FIELDS,
        lambda row: f"{row.get('platform', '')}|{row.get('order_id', '')}",
    )
    item_rows = [{
        "date": item["date"],
        "platform": "walmart_ca",
        "order_id": item["order_id"],
        "category": "uncategorized",
        "product_name": item["product_name"],
        "quantity": item["quantity"],
        "price": item["price"],
        "delivery_status": item["delivery_status"],
        "product_link": item["product_link"],
        "currency": "CAD",
    } for item in imported_items if item.get("order_id")]

    items = upsert(
        existing_items,
        item_rows,
        ITEM_FIELDS,
        lambda row: "|".join([
            row.get("platform", ""),
            row.get("order_id", ""),
            row.get("product_name", ""),
            row.get("price", ""),
            row.get("product_link", ""),
        ]),
    )

    write_csv(ledger_path, TRANSACTION_FIELDS, transactions)
    write_csv(items_path, ITEM_FIELDS, items)
    if imported_transactions or item_rows:
        upsert_orders_and_items_from_rows(
            imported_transactions,
            item_rows,
            order_keys={(row.get("platform", ""), row.get("order_id", "")) for row in imported_transactions},
        )

    print(f"Imported {len(imported_transactions)} orders and {len(imported_items)} item lines from {len(files)} file(s).")
    print(f"Wrote {ledger_path}")
    print(f"Wrote {items_path}")
    if imported_transactions or item_rows:
        print("Updated data/expenses.sqlite")


if __name__ == "__main__":
    main()
