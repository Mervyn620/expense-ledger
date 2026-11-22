import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


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

SNAPSHOT_NAME_RE = re.compile(r"_(\d{8,})\.json$")


def discover_snapshots(raw_dir):
    snapshots = {}
    for path in raw_dir.glob("*_walmart_ca_*.json"):
        match = SNAPSHOT_NAME_RE.search(path.name)
        if match:
            snapshots[match.group(1)] = path
    return snapshots


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def money_to_float(value):
    match = re.search(r"-?\$?\s*(\d+(?:,\d{3})*(?:\.\d{2})?)", str(value or ""))
    if not match:
        return 0.0
    return float(match.group(1).replace(",", ""))


def money(value):
    return f"{float(value):.2f}"


def normalize_product_name(value):
    text = clean(value)
    text = re.sub(r"\s*\|\s*Actual Color:.*$", "", text, flags=re.I)
    text = re.sub(r"\s*Actual Color:.*$", "", text, flags=re.I)
    text = re.sub(r"\s*Colou?r:\s*.*$", "", text, flags=re.I)
    text = re.sub(r"\s*Size:\s*.*$", "", text, flags=re.I)
    # Weight-adjusted rows can concatenate the purchased weight calculation
    # directly onto the catalog title (for example, "KG0.605ea x $18.67/ea").
    text = re.sub(
        r"\d+(?:\.\d+)?(?:kg|ea)\s*x\s*\$\d+(?:\.\d+)?/(?:kg|ea)\s*$",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s+", " ", text)
    return clean(text.strip(" |"))


def canonicalize_product_link(value):
    try:
        parsed = urlsplit(clean(value))
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


def product_match_text(value):
    return re.sub(r"[^a-z0-9]+", " ", normalize_product_name(value).casefold()).strip()


def product_link_score(item_name, candidate):
    wanted = product_match_text(item_name)
    candidate_name = product_match_text(candidate.get("productName", ""))
    context = product_match_text(candidate.get("itemCandidate", ""))
    if not wanted:
        return 0
    if candidate_name == wanted:
        return 100
    if candidate_name and min(len(candidate_name), len(wanted)) >= 10:
        if candidate_name in wanted or wanted in candidate_name:
            return 85
    if context and wanted in context:
        return 70
    return 0


def associate_product_links(items, candidates):
    normalized = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        product_url = canonicalize_product_link(candidate.get("productUrl", ""))
        if not product_url:
            continue
        normalized.append({**candidate, "productUrl": product_url})

    for item in items:
        scored = []
        for candidate in normalized:
            score = product_link_score(item.get("product_name", ""), candidate)
            if score:
                scored.append((score, candidate["productUrl"]))
        if not scored:
            continue
        best_score = max(score for score, _ in scored)
        best_urls = {url for score, url in scored if score == best_score}
        if len(best_urls) == 1:
            item["product_link"] = best_urls.pop()
    return items


def normalize_date(value):
    text = clean(value)
    match = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if not match:
        return text
    month_name, day, year = match.groups()
    month = MONTHS.get(month_name.lower())
    return f"{year}-{month}-{int(day):02d}" if month else text


def categorize(name):
    text = name.lower()
    # Only specific bag types are household goods: "Green Tea 100 Bags" is food.
    household = [
        "garbage", "waste can", "trash", "toilet", "cleaner", "swiffer",
        "bathroom", "freezer bags", "storage bags", "sandwich bags",
        "kitchen bags", "dish soap", "sponge", "broom", "facial tissues",
        "aluminum foil", "plastic wrap", "parchment paper", "insect killer",
    ]
    kitchenware = [
        "ladle", "bowl", "utensil", "spoon", "fork", "colander", "tongs",
        "turner", "ceramic", "stainless", "kitchenaid", "oxo", "mainstays",
    ]
    groceries = [
        "milk", "olive oil", "chicken", "wings", "ginger", "green onion",
        "cola", "spaghetti", "pasta", "tomatoes", "vegetables", "rice",
        "soy sauce", "salt", "sausages", "garlic", "natrel", "mutti",
        "barilla", "mina", "tilda", "lkk", "windsor", "pickles", "bread",
        "onion", "parmigiano", "cheese",
    ]

    if any(term in text for term in household):
        return "household"
    if any(term in text for term in kitchenware):
        return "kitchenware"
    if any(term in text for term in groceries):
        return "groceries"
    # Whole words, so short terms such as "tea" or "oats" do not match inside
    # unrelated words.
    if GROCERY_WORDS.search(text) and not NON_FOOD_WORDS.search(text):
        return "groceries"
    return "uncategorized"


# Product names that mention a food word without being groceries, such as
# "Tea Tree Oil" shampoo or nutrition text on a protein powder.
NON_FOOD_WORDS = re.compile(
    r"\b(?:shampoo|conditioner|soap|body wash|lotion|toothpaste|deodorant|"
    r"tea tree|protein powder|supplement|vitamins?)\b"
)


GROCERY_WORDS = re.compile(
    r"\b(?:tea|coffee|yogurt|yoghurt|oats|oatmeal|eggs?|tuna|bananas?|apples?|"
    r"oreo|cookies|crackers|cereal|pancetta|bacon|juice|flour|sugar|noodles?|"
    r"tofu|shrimp|beef|pork|salmon|potato(?:es)?|carrots?|lettuce|spinach|"
    r"broccoli|mushrooms?|avocados?|cucumbers?|zucchini|edamame|mayonnaise|"
    r"sesame oil|oregano|saffron|pears?|peach(?:es)?|blueberries|berry|"
    r"ice cream|spring water|coconut water|chili)\b"
)


def parse_item_candidate(value):
    text = clean(value.replace("\n", " "))
    if "Qty" not in text or "$" not in text:
        return None

    qty_match = re.search(r"Qty\s*(\d+)", text)
    if not qty_match:
        return None

    prefix = text[:qty_match.start()].strip(" |")
    prefix = re.sub(r"\d+(?:\.\d+)?kg\s+x\s+\$\d+(?:\.\d+)?/kg\s*Weight-adjusted\s*$", "", prefix, flags=re.I)
    prefix = re.sub(r"(?:Shopped|Substitutions|Weight-adjusted)\s*$", "", prefix, flags=re.I)
    prefix = normalize_product_name(prefix)
    if not prefix:
        return None

    suffix = text[qty_match.end():]
    discount = re.search(r"Discount price\s*\$(\d+(?:\.\d{2})?)", suffix, flags=re.I)
    if discount:
        price = float(discount.group(1))
    else:
        amounts = re.findall(r"\$(\d+(?:\.\d{2})?)", suffix)
        if not amounts:
            return None
        price = float(amounts[0])

    status = ""
    if re.search(r"Substitutions?(?=\s*Qty\b|\s|$)", text, flags=re.I):
        status = "substituted"
    elif re.search(r"Shopped(?=\s*Qty\b|\s|$)", text, flags=re.I):
        status = "shopped"
    elif re.search(r"Weight-adjusted(?=\s*Qty\b|\s|$)", text, flags=re.I):
        status = "weight-adjusted"

    return {
        "product_name": prefix,
        "quantity": qty_match.group(1),
        "price": price,
        "delivery_status": status,
        "product_link": "",
    }


def find_next_money(lines, label):
    for index, line in enumerate(lines):
        if clean(line).lower() == label.lower():
            for next_line in lines[index + 1:index + 5]:
                if "$" in next_line:
                    return money_to_float(next_line)
    return 0.0


def find_item_subtotal(lines):
    """Return Walmart's net merchandise subtotal after item savings."""
    for index, line in enumerate(lines):
        if clean(line).casefold() != "subtotal":
            continue

        window = [clean(value) for value in lines[index + 1:index + 10]]
        gross = next((money_to_float(value) for value in window if "$" in value), 0.0)
        if gross <= 0:
            return 0.0

        savings_index = next(
            (offset for offset, value in enumerate(window) if value.casefold() == "savings"),
            None,
        )
        if savings_index is None:
            return gross

        money_after_savings = [
            value for value in window[savings_index + 1:] if "$" in value
        ]
        if not money_after_savings:
            return gross

        savings = money_to_float(money_after_savings[0])
        expected_net = round(max(gross - savings, 0.0), 2)
        for value in money_after_savings[1:]:
            candidate = money_to_float(value)
            if abs(candidate - expected_net) <= 0.02:
                return candidate
        return expected_net

    return 0.0


def find_tax(lines):
    for line in lines:
        match = re.search(r"Taxes\s+\$(\d+(?:\.\d{2})?)", line, flags=re.I)
        if match:
            return float(match.group(1))
    return find_next_money(lines, "Taxes")


def quantity_count(items):
    total = 0
    for item in items:
        try:
            total += int(item.get("quantity") or 0)
        except ValueError:
            continue
    return total


def reconcile_items_to_subtotal(items, subtotal):
    if not items or subtotal <= 0:
        return items

    current = round(sum(item["price"] for item in items), 2)
    if abs(current - subtotal) <= 0.02:
        return items

    # Walmart sometimes exposes both a product row and a variant row for the
    # same purchased item. Prefer the smallest stable subset that reconciles to
    # the receipt subtotal instead of importing DOM duplicates.
    if current > subtotal and len(items) <= 24:
        prices = [int(round(item["price"] * 100)) for item in items]
        target = int(round(subtotal * 100))
        best = {0: []}
        for index, price in enumerate(prices):
            for total, indexes in list(best.items()):
                next_total = total + price
                if next_total > target or next_total in best:
                    continue
                best[next_total] = indexes + [index]
            if target in best:
                keep = set(best[target])
                return [item for index, item in enumerate(items) if index in keep]

    return items


def is_cancelled_order(lines):
    statuses = {clean(line).casefold() for line in lines}
    return bool(statuses & {"cancelled", "canceled"})


def parse_snapshot(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    url = data.get("url", "")
    order_match = re.search(r"/orders/(\d{8,})", url)
    if not order_match:
        return None

    lines = [clean(line) for line in data.get("lines", [])]
    if is_cancelled_order(lines):
        return None
    date = ""
    for line in lines:
        if re.match(r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}$", line):
            date = normalize_date(line)
            break

    items = []
    seen = set()
    for candidate in data.get("itemCandidates", []):
        item = parse_item_candidate(candidate)
        if not item:
            continue
        key = (item["product_name"].lower(), item["quantity"], money(item["price"]))
        if key in seen:
            continue
        seen.add(key)
        item["category"] = categorize(item["product_name"])
        items.append(item)

    subtotal = find_item_subtotal(lines)
    items = reconcile_items_to_subtotal(items, subtotal)
    items = associate_product_links(items, data.get("productLinks", []))
    item_total = round(sum(item["price"] for item in items), 2)
    total = find_next_money(lines, "Total")
    tax = find_tax(lines)
    tip = 0.0
    fees = round(max(total - item_total - tax - tip, 0.0), 2)
    categories = sorted({item["category"] for item in items})
    order_category = categories[0] if len(categories) == 1 else "mixed"
    item_names = [item["product_name"] for item in items]
    # The progress timeline includes a bare "Delivered" milestone before delivery.
    # Only an actual delivery date confirms fulfillment.
    if any(re.match(r"^Delivered on\b", line, re.I) for line in lines):
        fulfillment = "delivered"
    elif any(re.match(r"^(?:Out for delivery|On the way)\b", line, re.I) for line in lines):
        fulfillment = "in_transit"
    elif any(re.match(r"^Shipped(?: on)?\b", line, re.I) for line in lines):
        fulfillment = "shipped"
    elif any(re.match(r"^Scheduled(?: delivery| pickup| order)?\b", line, re.I) for line in lines):
        fulfillment = "scheduled"
    else:
        fulfillment = "pending"

    transaction = {
        "date": date,
        "platform": "walmart_ca",
        "order_id": order_match.group(1),
        "merchant": "Walmart",
        "fulfillment": fulfillment,
        "category": order_category,
        "item": "; ".join(item_names[:12]),
        "quantity": str(quantity_count(items) or len(items)),
        "item_total": money(item_total),
        "tax": money(tax),
        "fees": money(fees),
        "tip": money(tip),
        "total": money(total),
        "currency": "CAD",
        "source_url": url,
        "notes": (
            f"{len(items)} item lines / {quantity_count(items) or len(items)} purchased units "
            "from Walmart Canada detail page"
        ),
    }

    item_rows = []
    for item in items:
        item_rows.append({
            "date": date,
            "platform": "walmart_ca",
            "order_id": order_match.group(1),
            "category": item["category"],
            "product_name": item["product_name"],
            "quantity": item["quantity"],
            "price": money(item["price"]),
            "delivery_status": item["delivery_status"],
            "product_link": item["product_link"],
            "currency": "CAD",
        })

    return transaction, item_rows


def main():
    recover_csv_publications(ROOT)
    raw_dir = ROOT / "raw"
    snapshots = discover_snapshots(raw_dir)

    transactions = []
    items = []
    cancelled_order_keys = set()
    for order_id, path in sorted(snapshots.items()):
        data = json.loads(path.read_text(encoding="utf-8"))
        snapshot_lines = [clean(line) for line in data.get("lines", [])]
        if is_cancelled_order(snapshot_lines):
            cancelled_order_keys.add(("walmart_ca", order_id))
            continue
        parsed = parse_snapshot(path)
        if not parsed:
            continue
        transaction, item_rows = parsed
        transactions.append(transaction)
        items.extend(item_rows)

    if not transactions and not items and not cancelled_order_keys:
        print("No Walmart detail snapshots found; existing normalized ledgers were left unchanged.")
        return

    transactions.sort(key=lambda row: (row["date"], row["order_id"]))
    items.sort(key=lambda row: (row["date"], row["order_id"], row["product_name"]))

    deleted_orders = import_orders_with_csv(
        transactions, items, root=ROOT, item_filename='walmart_items.csv',
        delete_order_keys=cancelled_order_keys)

    print(f"Imported {len(transactions)} Walmart orders and {len(items)} item lines.")
    if cancelled_order_keys:
        print(
            "Excluded "
            f"{len(cancelled_order_keys)} cancelled Walmart order(s); "
            f"removed {deleted_orders} ledger row(s)."
        )
    print("Merged data/transactions.csv")
    print("Merged data/walmart_items.csv")
    print("Updated data/expenses.sqlite")


if __name__ == "__main__":
    main()
