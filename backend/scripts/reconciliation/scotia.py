import json
import re
import sys
from collections import Counter
from contextlib import closing, nullcontext
from datetime import datetime, timedelta
from itertools import groupby
from pathlib import Path


LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
RECONCILIATION_DIR = Path(__file__).resolve().parent
for _path in (LEDGER_DIR, RECONCILIATION_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bank_adapters import (
    ADAPTERS,
    BANK_ONLY_ORDER_PREFIXES,
    BANK_ONLY_PLATFORMS,
    adapter_for,
)

from db import (
    DB_PATH,
    ITEM_FIELDS,
    ROOT,
    TRANSACTION_FIELDS,
    atomic_write_text,
    connect,
    now_iso,
    order_pk,
    read_csv,
    _delete_orders,
    _publish_csv_publications,
    queue_csv_publications,
    replace_items_for_orders,
    replace_payments,
    tx_key_tuple,
    tx_key,
    upsert_cards,
    upsert_orders,
    upsert_rows,
    write_csv,
)
from payment_assignments import ASSIGNMENT_FIELDS
from uber_receipt_parts import card_portion, load_receipts_by_key


SCOTIA_PATH = ROOT / "data" / "scotia_transactions.json"
# Imported OFX/CSV statements (scripts/importers/bank_statement.py). None means
# "bank_statements" beside SCOTIA_PATH, so tests that move SCOTIA_PATH move it too.
BANK_STATEMENTS_DIR = None
TRANSACTIONS_PATH = ROOT / "data" / "transactions.csv"
DELIVERY_ITEMS_PATH = ROOT / "data" / "delivery_items.csv"
BANK_ITEMS_PATH = ROOT / "data" / "bank_items.csv"
ASSIGNMENTS_PATH = ROOT / "data" / "payment_assignments.csv"
REPORT_DIR = ROOT / "exports" / "reconciliation"
REPORT_CSV = REPORT_DIR / "scotia.csv"
REPORT_MD = REPORT_DIR / "scotia.md"
MERCHANT_RULES_PATH = ROOT / "config" / "merchant_rules.json"
PAYMENT_CONFIG_PATH = ROOT / "config" / "payment_cards.json"
UBER_RECEIPTS_PATH = ROOT / "data" / "uber_eats_receipts.json"

REPORT_FIELDS = [
    "date",
    "description",
    "amount",
    "direction",
    "merchant",
    "category",
    "detected_platform",
    "match_status",
    "match_method",
    "matched_order_id",
    "matched_order_date",
    "matched_order_total",
    "notes",
]

DEFAULT_MERCHANT_RULES = {
    "platforms": {},
    "merchant_profiles": [],
    "service_charge_profile": {
        "merchant": "Scotiabank",
        "category": "bank_fees",
        "item": "Bank service fee",
        "fulfillment": "bank_reconciled",
    },
}


_MERCHANT_RULES = None


def load_merchant_rules(path=MERCHANT_RULES_PATH):
    global _MERCHANT_RULES
    if _MERCHANT_RULES is not None:
        return _MERCHANT_RULES
    path = Path(path)
    if path.exists():
        _MERCHANT_RULES = json.loads(path.read_text(encoding="utf-8-sig"))
    else:
        _MERCHANT_RULES = DEFAULT_MERCHANT_RULES
    return _MERCHANT_RULES


def load_scotia(path=SCOTIA_PATH):
    if not Path(path).exists():
        raise FileNotFoundError(f"Scotia transaction export not found: {path}")
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return payload, payload.get("transactions", [])


def statements_dir():
    return Path(BANK_STATEMENTS_DIR) if BANK_STATEMENTS_DIR else Path(SCOTIA_PATH).parent / "bank_statements"


def load_bank_rows():
    """All bank rows to reconcile, each tagged with its account and adapter.

    Sources: the Scotia collector cache, plus one file per account written by the
    statement importer. Returns (rows, accounts by account_id).
    """
    rows, accounts, sources = [], {}, 0

    def take(payload, adapter_id):
        default_account = payload.get("account_id")
        defaults = {key: payload.get(key) for key in
                    ("institution", "statement_type", "display_name", "last4") if payload.get(key)}
        default_details = account_details(default_account, defaults)
        accounts.setdefault(default_details["account_id"], default_details)
        for row in payload.get("transactions", []):
            row = dict(row)
            account_id = row.get("account_id") or default_details["account_id"]
            if account_id not in accounts:
                details = account_details(account_id, defaults)
                accounts.setdefault(details["account_id"], details)
                account_id = details["account_id"]
            account = accounts[account_id]
            row["account_id"] = account["account_id"]
            row["bank_adapter"] = adapter_id
            if adapter_id != "scotia":
                row["institution"] = account.get("institution", "")
                row["exclude_terms"] = account.get("exclude_terms", [])
            rows.append(row)

    if Path(SCOTIA_PATH).exists():
        payload, _ = load_scotia(SCOTIA_PATH)
        take(payload, "scotia")
        sources += 1
    directory = statements_dir()
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            take(payload, payload.get("adapter") if payload.get("adapter") in ADAPTERS else "generic")
            sources += 1
    if not sources:
        raise FileNotFoundError(
            f"No bank transactions to reconcile: neither {SCOTIA_PATH} nor statements in {directory}")
    return rows, accounts


def money(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


# The helpers below accept a bank row (dict) or a bare description. A bare
# description, as in older callers, is read with the Scotia adapter.
def _description(row_or_description):
    if isinstance(row_or_description, dict):
        return str(row_or_description.get("description") or "")
    return str(row_or_description or "")


def merchant_key(row_or_description):
    return adapter_for(row_or_description).merchant_key(row_or_description)


def detected_platform(row_or_description):
    value = _description(row_or_description).lower()
    for platform, rule in load_merchant_rules().get("platforms", {}).items():
        terms = rule.get("terms", [])
        if not any(term in value for term in terms):
            continue
        required = rule.get("required_any", [])
        if required and not any(term in value for term in required):
            continue
        return platform
    return ""


def is_transfer(row_or_description):
    return adapter_for(row_or_description).is_transfer(row_or_description)


def is_purchase(row_or_description):
    return adapter_for(row_or_description).is_purchase(row_or_description)


def merchant_profile(row_or_description):
    adapter = adapter_for(row_or_description)
    value = _description(row_or_description).lower()
    rules = load_merchant_rules()
    platform = detected_platform(row_or_description)
    if platform:
        rule = rules.get("platforms", {}).get(platform, {})
        membership = any(term in value for term in rule.get("membership_terms", []))
        merchant = rule.get("merchant", platform)
        return {
            "merchant": rule.get("membership_merchant", merchant) if membership else merchant,
            "category": rule.get("membership_category", rule.get("category", "food_delivery")) if membership else rule.get("category", "food_delivery"),
            "item": rule.get("membership_item", rule.get("item", f"Bank-reconciled {merchant} charge")) if membership else rule.get("item", f"Bank-reconciled {merchant} charge"),
            "platform": platform,
            "requires_order_match": bool(
                rule.get("membership_requires_order_match", False)
                if membership
                else rule.get("requires_order_match", False)
            ),
            "fulfillment": rule.get("membership_fulfillment", rule.get("fulfillment", "bank_reconciled")) if membership else rule.get("fulfillment", "bank_reconciled"),
        }

    for profile in rules.get("merchant_profiles", []):
        terms = profile.get("terms", [])
        if any(term in value for term in terms):
            return {
                "merchant": profile.get("merchant", "Unclassified merchant"),
                "category": profile.get("category", "uncategorized"),
                "item": profile.get("item", "Bank-recorded purchase"),
                "platform": "",
                "requires_order_match": False,
                "fulfillment": profile.get("fulfillment", "bank_reconciled"),
            }

    if adapter.is_service_charge(row_or_description):
        profile = rules.get("service_charge_profile", DEFAULT_MERCHANT_RULES["service_charge_profile"])
        institution = adapter.institution
        if isinstance(row_or_description, dict) and row_or_description.get("institution"):
            institution = row_or_description["institution"]
        return {
            # The configured fee merchant names the Scotia account; other banks use their own name.
            "merchant": profile.get("merchant", institution) if adapter is ADAPTERS["scotia"] else institution,
            "category": profile.get("category", "bank_fees"),
            "item": profile.get("item", "Bank service fee"),
            "platform": "",
            "requires_order_match": False,
            "fulfillment": profile.get("fulfillment", "bank_reconciled"),
        }

    merchant = adapter.merchant_name(row_or_description)
    return {
        "merchant": merchant or "Unclassified merchant",
        "category": "uncategorized",
        "item": "Bank-recorded purchase",
        "platform": "",
        "requires_order_match": False,
        "fulfillment": "bank_reconciled",
    }


def find_reversals(bank_rows):
    def identity(row):
        return (
            row.get("account_id", ""),
            row.get("currency", "CAD"),
            money(row.get("amount")),
            merchant_key(row),
        )

    credits = {}
    for row in bank_rows:
        if row.get("direction") != "credit":
            continue
        key = (row.get("date", ""), *identity(row))
        credits.setdefault(key, []).append(row)

    reversed_ids = set()
    pairs = {}
    for row in bank_rows:
        if row.get("direction") != "debit":
            continue
        key = (row.get("date", ""), *identity(row))
        candidates = credits.get(key, [])
        if not candidates:
            continue
        credit = candidates.pop(0)
        reversed_ids.update([row["transaction_id"], credit["transaction_id"]])
        pairs[row["transaction_id"]] = credit["transaction_id"]
        pairs[credit["transaction_id"]] = row["transaction_id"]

    # A posted correction can arrive the next day. Match it only when both
    # sides are unique for the same account, merchant and exact amount.
    unmatched_debits = {}
    unmatched_corrections = {}
    for row in bank_rows:
        if row["transaction_id"] in reversed_ids:
            continue
        try:
            date = parse_date(row.get("date", ""))
        except ValueError:
            continue
        key = (date, *identity(row))
        if row.get("direction") == "debit":
            unmatched_debits.setdefault(key, []).append(row)
        elif row.get("direction") == "credit" and adapter_for(row).is_correction(row):
            unmatched_corrections.setdefault(key, []).append(row)

    for (date, *details), correction_rows in unmatched_corrections.items():
        debit_rows = unmatched_debits.get((date - timedelta(days=1), *details), [])
        if len(correction_rows) != 1 or len(debit_rows) != 1:
            continue
        debit, credit = debit_rows[0], correction_rows[0]
        reversed_ids.update((debit["transaction_id"], credit["transaction_id"]))
        pairs[debit["transaction_id"]] = credit["transaction_id"]
        pairs[credit["transaction_id"]] = debit["transaction_id"]
    return reversed_ids, pairs


def order_reference(bank_row, order):
    number = str(order.get("order_id") or "")
    return bool(len(number) >= 4 and re.search(
        r"(?<![a-z0-9])" + re.escape(number) + r"(?![a-z0-9])",
        bank_row.get("description", ""), re.I))


_FILE_CACHE = {}


def _cached(path, loader):
    path = Path(path)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = _FILE_CACHE.get(str(path))
    if not cached or cached[0] != stamp:
        cached = (stamp, loader(path))
        _FILE_CACHE[str(path)] = cached
    return cached[1]


def account_card_last4(account_id):
    """Last four digits of the card that the bank account's purchases are drawn from."""
    def load(path):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        cards = payload.get("cards", {})
        return {
            account: str(cards.get(config.get("default_payment_instrument_id"), {}).get("last4") or "")
            for account, config in payload.get("bank_accounts", {}).items()
        }
    mapping = _cached(PAYMENT_CONFIG_PATH, load) or {}
    return mapping.get(account_id or "scotia_chequing_3333", "")


def split_card_charge(order, bank_row):
    """Card portion of an Uber order paid partly with Uber Cash, when it belongs to this account."""
    if order.get("platform") != "uber_eats":
        return None
    receipt = (_cached(UBER_RECEIPTS_PATH, load_receipts_by_key) or {}).get(tx_key(order))
    portion = card_portion(receipt, order.get("total")) if receipt else None
    if not portion:
        return None
    expected = account_card_last4((bank_row or {}).get("account_id"))
    if expected and portion["last4"] != expected:
        return None
    return portion


def charge_amounts(order, bank_row):
    """Amounts a single bank debit may show for this order: its total, or its card-paid split."""
    amounts = {money(order.get("total"))}
    portion = split_card_charge(order, bank_row)
    if portion:
        amounts.add(money(portion["amount"]))
    return amounts


def exact_candidates(bank_row, platform, transactions, used_keys):
    bank_date = parse_date(bank_row["date"])
    bank_amount = money(bank_row["amount"])
    candidates = []
    for tx in transactions:
        if tx.get("platform") != platform:
            continue
        if str(tx.get("order_id", "")).startswith(BANK_ONLY_ORDER_PREFIXES):
            continue
        key = f"{tx.get('platform')}|{tx.get('order_id')}"
        if (key in used_keys or bank_amount <= 0 or bank_amount not in charge_amounts(tx, bank_row)
                or tx.get("currency", "CAD") != bank_row.get("currency", "CAD")):
            continue
        try:
            days = abs((bank_date - parse_date(tx.get("date", ""))).days)
        except ValueError:
            continue
        if days <= 3:
            candidates.append((not order_reference(bank_row, tx), days, tx))
    return candidates


def select_existing_order_match(bank_row, platform, transactions, used_keys):
    candidates = exact_candidates(bank_row, platform, transactions, used_keys)
    if not candidates:
        return None, "no_candidate", 0

    best = min(entry[:2] for entry in candidates)
    nearest = [tx for *rank, tx in candidates if tuple(rank) == best]
    if len(nearest) != 1:
        return None, "ambiguous_candidates", len(nearest)

    match = nearest[0]
    used_keys.add(f"{match.get('platform')}|{match.get('order_id')}")
    return match, "unique_nearest_candidate", 1


def review_candidates(bank, orders):
    profile = merchant_profile(bank)
    if bank["direction"] != "debit" or is_transfer(bank) or not is_purchase(bank):
        return []
    if profile["fulfillment"] == "subscription":
        return []
    merchant = profile["merchant"].casefold()
    candidates = []
    for order in orders:
        if order["platform"] in BANK_ONLY_PLATFORMS:
            continue
        if profile["platform"]:
            if order["platform"] != profile["platform"]:
                continue
        elif (order.get("merchant") or "").casefold() != merchant:
            continue
        candidates.extend(exact_candidates(bank, order["platform"], [order], ()))
    return [order for _, _, order in sorted(candidates, key=lambda e: (*e[:2], e[2]["order_id"]))]


def match_existing_order(bank_row, platform, transactions, used_keys):
    match, _, _ = select_existing_order_match(
        bank_row,
        platform,
        transactions,
        used_keys,
    )
    return match


def split_adjustment_tolerance(order_total):
    return round(min(3.0, max(1.0, money(order_total) * 0.02)), 2)


def plan_existing_order_matches(bank_rows, transactions, reversed_ids=None, reserved_keys=()):
    """Plan exact and conservative split-charge matches before mutating the ledger."""
    reversed_ids = set(reversed_ids or [])
    used_keys = set(reserved_keys)
    proposed = []
    plans = {}
    attempts = {}
    unmatched = []

    for bank_row in bank_rows:
        tx_id = bank_row.get("transaction_id", "")
        if (
            not tx_id
            or tx_id in reversed_ids
            or bank_row.get("direction") != "debit"
            or not is_purchase(bank_row)
        ):
            continue
        profile = merchant_profile(bank_row)
        platform = profile.get("platform", "")
        if not platform or profile.get("fulfillment") == "subscription":
            continue
        match, reason, candidate_count = select_existing_order_match(
            bank_row,
            platform,
            transactions,
            used_keys.copy(),
        )
        attempts[tx_id] = (reason, candidate_count)
        proposed.append((bank_row, profile, match))

    claims = Counter(tx_key_tuple(match) for _, _, match in proposed if match)
    for bank_row, profile, match in proposed:
        tx_id = bank_row["transaction_id"]
        if match and claims[tx_key_tuple(match)] > 1:
            attempts[tx_id] = ("ambiguous_candidates", claims[tx_key_tuple(match)])
            used_keys.add(tx_key(match))
        elif match:
            used_keys.add(tx_key(match))
            portion = (split_card_charge(match, bank_row)
                       if money(match.get("total")) != money(bank_row.get("amount")) else None)
            if portion:
                method = "platform_split_tender_card_portion"
                notes = (f"Receipt split payment {portion['summary']}; the bank charge is the card portion "
                         f"of the {money(match.get('total')):.2f} order and is not a separate expense.")
            else:
                method = ("platform_order_reference_amount_date" if order_reference(bank_row, match)
                          else "platform_amount_unique_nearest_date_3d")
                notes = ""
            plans[tx_id] = {"order": match, "method": method, "notes": notes}
        elif attempts[tx_id][0] != "ambiguous_candidates":
            unmatched.append((bank_row, profile))

    group_key = lambda entry: (
        entry[1].get("platform", ""),
        entry[0].get("account_id", ""),
        entry[0].get("currency", "CAD"),
        entry[0].get("date", ""),
        merchant_key(entry[0]),
    )
    unmatched.sort(key=group_key)
    split_plans = []
    for key, entries in groupby(unmatched, key=group_key):
        group = list(entries)
        if not 2 <= len(group) <= 4:
            continue
        rows = [entry[0] for entry in group]
        try:
            bank_date = parse_date(key[3])
        except ValueError:
            continue
        combined = round(sum(money(row.get("amount")) for row in rows), 2)
        candidates = []
        for order in transactions:
            if order.get("platform") != key[0] or order.get("currency", "CAD") != key[2]:
                continue
            if str(order.get("order_id", "")).startswith(BANK_ONLY_ORDER_PREFIXES):
                continue
            order_key = f"{order.get('platform')}|{order.get('order_id')}"
            if order_key in used_keys:
                continue
            order_total = money(order.get("total"))
            if order_total <= 0 or any(
                money(row.get("amount")) >= order_total for row in rows
            ):
                continue
            try:
                days = abs((bank_date - parse_date(order.get("date", ""))).days)
            except ValueError:
                continue
            difference = round(abs(combined - order_total), 2)
            if days <= 1 and difference <= split_adjustment_tolerance(order_total):
                candidates.append((order, difference))

        if len(candidates) != 1:
            if candidates:
                for row in rows:
                    attempts[row["transaction_id"]] = ("ambiguous_candidates", len(candidates))
            continue
        order, difference = candidates[0]
        split_plans.append((rows, combined, order, difference))
    split_claims = Counter(tx_key_tuple(order) for _, _, order, _ in split_plans)
    for rows, combined, order, difference in split_plans:
        if split_claims[tx_key_tuple(order)] > 1:
            for row in rows:
                attempts[row["transaction_id"]] = ("ambiguous_candidates", split_claims[tx_key_tuple(order)])
            continue
        notes = (
            f"{len(rows)} same-day platform charges total {combined:.2f}; "
            f"matched to the unique item-level order total {money(order.get('total')):.2f}. "
            f"The {difference:.2f} difference is within the weighted-item/order-adjustment tolerance."
        )
        for row in rows:
            plans[row["transaction_id"]] = {
                "order": order,
                "method": "platform_split_amount_adjusted_same_day_unique",
                "notes": notes,
            }

    return plans, attempts


def account_details(account_id, defaults=None):
    """Account display and payment mapping from payment_cards.json.

    ``defaults`` carries what a statement import recorded (institution,
    statement_type, display_name, last4) for accounts missing from the config.
    """
    defaults = defaults or {}
    account_id = account_id or "scotia_chequing_3333"
    payment_config = json.loads(
        PAYMENT_CONFIG_PATH.read_text(encoding="utf-8-sig")
    )
    account_config = payment_config.get("bank_accounts", {}).get(account_id, {})
    cards = payment_config.get("cards", {})
    last4_match = re.search(r"(\d{4})$", account_id)
    last4 = last4_match.group(1) if last4_match else str(defaults.get("last4") or "")
    institution = account_config.get("institution") or defaults.get("institution") or "Scotiabank"
    display = account_config.get("display_name") or defaults.get("display_name")
    if not display and institution == "Scotiabank":
        display = (
            f"Scotiabank Chequing {last4}".strip()
            if "chequing" in account_id
            else f"Scotiabank Account {last4}".strip()
        )
    elif not display:
        display = f"{institution} {last4}".strip()
    default_instrument_id = account_config.get(
        "default_payment_instrument_id",
        "unassigned",
    )
    direct_debit_id = account_config.get(
        "direct_debit_instrument_id",
        "account_debit",
    )
    return {
        "account_id": account_id,
        "display_name": display,
        "last4": last4,
        "institution": institution,
        "statement_type": account_config.get("statement_type") or defaults.get("statement_type") or "bank_account",
        "exclude_terms": list(account_config.get("exclude_terms", [])),
        "default_payment_instrument_id": default_instrument_id,
        "default_payment_instrument_label": cards.get(
            default_instrument_id,
            cards.get("unassigned", {}),
        ).get("display_name", default_instrument_id),
        "direct_debit_instrument_id": direct_debit_id,
        "direct_debit_instrument_label": cards.get(
            direct_debit_id,
            {},
        ).get("display_name", "账户直接扣款"),
    }


def payment_channel(row_or_description, account=None):
    return adapter_for(row_or_description).payment_channel(row_or_description, account)


def inferred_payment(bank_row, account):
    adapter = adapter_for(bank_row)
    channel = adapter.payment_channel(bank_row, account)
    assignable = account["default_payment_instrument_id"] not in {None, "", "unassigned", "account_debit"}
    if channel == "card_statement":
        # A credit-card statement lists only that card's charges, so the card is known.
        return {
            "card_id": account["default_payment_instrument_id"],
            "card_label": account["default_payment_instrument_label"],
            "status": "confirmed" if assignable else "needs_review",
            "source": f"{adapter.id}_card_statement",
            "method": account["default_payment_instrument_label"],
            "channel": channel,
        }
    if adapter.is_purchase(bank_row) and channel != "account_debit":
        return {
            "card_id": account["default_payment_instrument_id"],
            "card_label": account["default_payment_instrument_label"],
            "status": "inferred" if channel in {"online", "in_store"} and assignable else "needs_review",
            "source": adapter.payment_source,
            "method": account["default_payment_instrument_label"],
            "channel": channel,
        }
    return {
        "card_id": account["direct_debit_instrument_id"],
        "card_label": account["direct_debit_instrument_label"],
        "status": "confirmed",
        "source": adapter.debit_source,
        "method": account["direct_debit_instrument_label"],
        "channel": channel,
    }


def bank_only_rows(bank_row, profile, account):
    adapter = adapter_for(bank_row)
    order_id = f"{adapter.order_prefix}{bank_row['transaction_id']}"
    platform = adapter.platform
    detected_merchant_platform = profile["platform"]
    amount = f"{money(bank_row.get('amount')):.2f}"
    tx = {
        "date": bank_row.get("date", ""),
        "platform": platform,
        "order_id": order_id,
        "merchant": profile["merchant"],
        "fulfillment": profile["fulfillment"],
        "category": profile["category"],
        "item": profile["item"],
        "quantity": "1",
        "item_total": amount,
        "tax": "0.00",
        "fees": "0.00",
        "tip": "0.00",
        "total": amount,
        "currency": bank_row.get("currency", "CAD"),
        "source_url": "",
        "notes": (
            f"Imported from sanitized {account['display_name']} reconciliation; "
            "component breakdown unavailable"
            + (
                f"; detected merchant platform={detected_merchant_platform}."
                if detected_merchant_platform
                else "."
            )
        ),
    }
    item = {
        "date": tx["date"],
        "platform": platform,
        "order_id": order_id,
        "category": profile["category"],
        "product_name": profile["item"],
        "quantity": "1",
        "price": amount,
        "delivery_status": "bank_reconciled",
        "product_link": "",
        "currency": tx["currency"],
    }
    payment = inferred_payment(bank_row, account)
    assignment = {
        "date": tx["date"],
        "platform": platform,
        "order_id": order_id,
        "merchant": profile["merchant"],
        "total": amount,
        "currency": tx["currency"],
        "card_id": payment["card_id"],
        "card_label": payment["card_label"],
        "assignment_status": payment["status"],
        "assignment_source": payment["source"],
        "payment_channel": payment["channel"],
        "notes": (
            f"payment_method={payment['method']}; "
            f"funding_account={account['account_id']}; "
            f"confidence={payment['status']}"
        ),
    }
    return tx, item, assignment


def write_report(rows):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(REPORT_CSV, REPORT_FIELDS, rows)
    lines = [
        "# Scotia Reconciliation",
        "",
        f"- Bank transactions reviewed: {len(rows)}",
        f"- Reversed/corrected rows: {sum(row['match_status'] == 'reversed' for row in rows)}",
        f"- Credits excluded: {sum(row['match_status'] == 'credit' for row in rows)}",
        f"- Transfers excluded: {sum(row['match_status'] == 'excluded_transfer' for row in rows)}",
        f"- Matched to existing orders: {sum(row['match_status'] == 'matched_order' for row in rows)}",
        f"- Imported bank-only expenses: {sum(row['match_status'] == 'bank_only_imported' for row in rows)}",
        f"- Platform charges requiring review: {sum(row['match_status'] == 'unmatched_platform_review' for row in rows)}",
        "",
        "| Date | Merchant | Category | Amount | Status | Matched Order |",
        "|---|---|---|---:|---|---|",
    ]
    for row in rows:
        if row["match_status"] in {"credit", "excluded_transfer"}:
            continue
        lines.append(
            f"| {row['date']} | {row['merchant']} | {row['category']} | "
            f"{row['amount']} | {row['match_status']} | {row['matched_order_id']} |"
        )
    atomic_write_text(REPORT_MD, "\n".join(lines) + "\n")


def upsert_bank_database(bank_rows, reconciliations, account, db_path=DB_PATH, *, connection=None,
                         accounts=None):
    """Store bank accounts, rows and their reconciliation status.

    ``account`` is the fallback account for rows without one; ``accounts`` lists
    every account the rows belong to (one per bank or card).
    """
    timestamp = now_iso()
    with (closing(connect(db_path)) if connection is None else nullcontext(connection)) as conn:
        for entry in (accounts or [account]):
            conn.execute(
                """
                INSERT INTO bank_accounts (account_id, institution, display_name, last4, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                  institution=excluded.institution,
                  display_name=excluded.display_name,
                  last4=excluded.last4,
                  updated_at=excluded.updated_at
                """,
                (entry["account_id"], entry.get("institution") or "Scotiabank",
                 entry["display_name"], entry["last4"], timestamp),
            )
        for row in bank_rows:
            conn.execute(
                """
                INSERT INTO bank_transactions (
                  transaction_id, account_id, date, description, amount,
                  direction, currency, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                  account_id=excluded.account_id,
                  date=excluded.date,
                  description=excluded.description,
                  amount=excluded.amount,
                  direction=excluded.direction,
                  currency=excluded.currency,
                  source=excluded.source,
                  updated_at=excluded.updated_at
                """,
                (
                    row.get("transaction_id", ""),
                    row.get("account_id", account["account_id"]),
                    row.get("date", ""),
                    row.get("description", ""),
                    money(row.get("amount")),
                    row.get("direction", ""),
                    row.get("currency", "CAD"),
                    row.get("source") or adapter_for(row).default_source,
                    timestamp,
                ),
            )

        for row in reconciliations:
            bank_pk = conn.execute(
                "SELECT id FROM bank_transactions WHERE transaction_id = ?",
                (row["transaction_id"],),
            ).fetchone()[0]
            linked_order_pk = None
            if row.get("matched_platform") and row.get("matched_order_id"):
                linked_order_pk = order_pk(
                    conn,
                    row["matched_platform"],
                    row["matched_order_id"],
                )
                if linked_order_pk is None:
                    raise ValueError('Matched platform order is missing from SQLite; complete the platform import before reconciliation.')
            conn.execute(
                """
                INSERT INTO bank_reconciliations (
                  bank_transaction_pk, order_pk, match_status, match_method,
                  notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bank_transaction_pk) DO UPDATE SET
                  order_pk=excluded.order_pk,
                  match_status=excluded.match_status,
                  match_method=excluded.match_method,
                  notes=excluded.notes,
                  updated_at=excluded.updated_at
                """,
                (
                    bank_pk,
                    linked_order_pk,
                    row["match_status"],
                    row.get("match_method", ""),
                    row.get("notes", ""),
                    timestamp,
                ),
            )
        if connection is None:
            conn.commit()


def reconcile():
    with closing(connect(DB_PATH)) as conn:
        conn.commit()
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            _publish_csv_publications(conn, ROOT)
            stats, report_rows = _reconcile(conn)
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            _publish_csv_publications(conn, ROOT)
    write_report(report_rows)
    return stats


def _reconcile(conn):
    bank_rows, accounts = load_bank_rows()
    account_for = lambda row: accounts.get(row.get("account_id")) or next(iter(accounts.values()))
    existing_transactions = read_csv(TRANSACTIONS_PATH, TRANSACTION_FIELDS, recover=False)
    bank_keys = {(row[0], row[1]) for row in conn.execute(
        "SELECT platform,order_id FROM orders WHERE (order_id LIKE 'scotia-%' OR order_id LIKE 'bank-%') "
        "AND (platform IN ('scotia','scotiabank','bank') OR fulfillment='bank_reconciled')")}
    bank_keys.update(tx_key_tuple(row) for row in existing_transactions
                     if str(row.get('order_id', '')).startswith(BANK_ONLY_ORDER_PREFIXES)
                     and (row.get('platform') in BANK_ONLY_PLATFORMS
                          or row.get('fulfillment') == 'bank_reconciled'))
    reviewed_ids = {f"{adapter_for(row).order_prefix}{row['transaction_id']}" for row in bank_rows}
    owned_keys = {key for key in bank_keys if key[1] in reviewed_ids}
    # Bounded bank exports do not prove that absent historical purchases disappeared.
    transactions = [row for row in existing_transactions if tx_key_tuple(row) not in bank_keys]
    preserved_transactions = [row for row in existing_transactions if tx_key_tuple(row) not in owned_keys]
    delivery_items = [row for row in read_csv(DELIVERY_ITEMS_PATH, ITEM_FIELDS, recover=False)
                      if tx_key_tuple(row) not in owned_keys]
    bank_items = [row for row in read_csv(BANK_ITEMS_PATH, ITEM_FIELDS, recover=False)
                  if tx_key_tuple(row) not in owned_keys]
    assignments = [row for row in read_csv(ASSIGNMENTS_PATH, ASSIGNMENT_FIELDS, recover=False)
                   if tx_key_tuple(row) not in owned_keys]
    reversed_ids, reversal_pairs = find_reversals(bank_rows)
    bank_dates = {row["transaction_id"]: row.get("date", "") for row in bank_rows}
    prior_manual = {}
    reserved = set()
    for tid, method, source, number, when, merchant, amount, currency in conn.execute("""
        SELECT bt.transaction_id,br.match_method,o.platform,o.order_id,o.date,o.merchant,o.total,o.currency
        FROM bank_reconciliations br JOIN bank_transactions bt ON bt.id=br.bank_transaction_pk
        LEFT JOIN orders o ON o.id=br.order_pk WHERE br.match_status='matched_order' OR br.match_method='manual_conflict'
    """):
        if method in {"manual_confirmed","manual_conflict"} and tid in bank_dates:
            prior_manual[tid] = ([dict(platform=source, order_id=number, date=when, merchant=merchant,
                                       total=amount, currency=currency)] if source else [])
        if tid not in bank_dates or tid in prior_manual:
            reserved.add(f"{source}|{number}")
    planned_matches, match_attempts = plan_existing_order_matches(
        bank_rows,
        transactions,
        reversed_ids | prior_manual.keys(), reserved,
    )
    for bank in bank_rows:
        tid = bank["transaction_id"]
        if tid in prior_manual:
            matches = review_candidates(bank, prior_manual[tid])
            if matches:
                planned_matches[tid] = {"order": matches[0], "method": "manual_confirmed", "notes": ""}
    imported_transactions = []
    imported_items = []
    imported_assignments = []
    reconciliation_rows = []
    report_rows = []

    for bank_row in bank_rows:
        tx_id = bank_row["transaction_id"]
        profile = merchant_profile(bank_row)
        platform = profile["platform"]
        match_status = "ignored_non_purchase"
        match_method = ""
        notes = ""
        matched = None
        order_match_reason = "not_attempted"
        order_match_candidate_count = 0

        if tx_id in reversed_ids:
            match_status = "reversed"
            match_method = (
                "next_day_amount_merchant_correction"
                if bank_dates.get(reversal_pairs.get(tx_id)) != bank_row.get("date")
                else "same_date_amount_merchant_correction"
            )
            notes = f"Offset by {adapter_for(bank_row).short_name} transaction {reversal_pairs.get(tx_id, '')}."
        elif bank_row.get("direction") == "credit":
            match_status = "credit"
        elif is_transfer(bank_row):
            match_status = "excluded_transfer"
        elif is_purchase(bank_row):
            plan = planned_matches.get(tx_id)
            if plan:
                matched = plan["order"]
                match_status = "matched_order"
                match_method = plan["method"]
                notes = plan["notes"]
            elif (profile.get("requires_order_match") or tx_id in prior_manual
                  or match_attempts.get(tx_id, ("",))[0] == "ambiguous_candidates"):
                order_match_reason, order_match_candidate_count = match_attempts.get(
                    tx_id,
                    ("no_candidate", 0),
                )
                match_status = "unmatched_platform_review"
                if tx_id in prior_manual:
                    match_method = "manual_conflict"
                    notes = "先前人工确认的订单或扣款凭证已变化，请重新核对。"
                elif order_match_reason == "ambiguous_candidates":
                    match_method = "ambiguous_platform_order_candidates"
                    notes = (
                        f"{order_match_candidate_count} equally ranked item-level "
                        f"{profile['merchant']} orders were found; manual review is "
                        "required and the bank charge was excluded from the expense "
                        "ledger."
                    )
                else:
                    match_method = "authoritative_platform_order_required"
                    notes = (
                        f"No matching item-level {profile['merchant']} order was found; "
                        "excluded from the expense ledger."
                    )
            else:
                tx, item, assignment = bank_only_rows(bank_row, profile, account_for(bank_row))
                imported_transactions.append(tx)
                imported_items.append(item)
                imported_assignments.append(assignment)
                matched = tx
                match_status = "bank_only_imported"
                match_method = "net_bank_purchase_unmatched"

        reconciliation_rows.append({
            "transaction_id": tx_id,
            "match_status": match_status,
            "match_method": match_method,
            "notes": notes,
            "matched_platform": matched.get("platform", "") if matched else "",
            "matched_order_id": matched.get("order_id", "") if matched else "",
        })
        report_rows.append({
            "date": bank_row.get("date", ""),
            "description": bank_row.get("description", ""),
            "amount": f"{money(bank_row.get('amount')):.2f}",
            "direction": bank_row.get("direction", ""),
            "merchant": profile["merchant"],
            "category": profile["category"],
            "detected_platform": platform,
            "match_status": match_status,
            "match_method": match_method,
            "matched_order_id": matched.get("order_id", "") if matched else "",
            "matched_order_date": matched.get("date", "") if matched else "",
            "matched_order_total": matched.get("total", "") if matched else "",
            "notes": notes,
        })

    merged_transactions = upsert_rows(
        preserved_transactions,
        imported_transactions,
        TRANSACTION_FIELDS,
        lambda row: f"{row.get('platform')}|{row.get('order_id')}",
    )
    merged_bank_items = upsert_rows(
        bank_items,
        imported_items,
        ITEM_FIELDS,
        lambda row: "|".join([
            row.get("platform", ""),
            row.get("order_id", ""),
            row.get("product_name", ""),
            row.get("quantity", ""),
            row.get("price", ""),
        ]),
    )
    merged_assignments = upsert_rows(
        assignments,
        imported_assignments,
        ASSIGNMENT_FIELDS,
        lambda row: f"{row.get('platform')}|{row.get('order_id')}",
    )

    desired_keys = {tx_key_tuple(row) for row in imported_transactions}
    _delete_orders(conn, owned_keys - desired_keys)
    upsert_cards(conn, ROOT)
    upsert_orders(conn, imported_transactions)
    replace_items_for_orders(conn, imported_items, desired_keys)
    replace_payments(conn, imported_transactions, imported_assignments, {}, replace_all=False)
    upsert_bank_database(bank_rows, reconciliation_rows, next(iter(accounts.values())),
                         connection=conn, accounts=list(accounts.values()))
    queue_csv_publications(conn, ROOT, (
        ('transactions.csv', TRANSACTION_FIELDS, merged_transactions),
        ('delivery_items.csv', ITEM_FIELDS, delivery_items),
        ('bank_items.csv', ITEM_FIELDS, merged_bank_items),
        ('payment_assignments.csv', ASSIGNMENT_FIELDS, merged_assignments),
    ))

    return {
        "bank_transactions": len(bank_rows),
        "reversed": sum(row["match_status"] == "reversed" for row in report_rows),
        "credits": sum(row["match_status"] == "credit" for row in report_rows),
        "transfers": sum(row["match_status"] == "excluded_transfer" for row in report_rows),
        "matched": sum(row["match_status"] == "matched_order" for row in report_rows),
        "imported": sum(row["match_status"] == "bank_only_imported" for row in report_rows),
        "review_required": sum(
            row["match_status"] == "unmatched_platform_review"
            for row in report_rows
        ),
        "imported_total": sum(
            money(row["amount"])
            for row in report_rows
            if row["match_status"] == "bank_only_imported"
        ),
        "account": ", ".join(entry["display_name"] for entry in accounts.values()),
    }, report_rows


def main():
    stats = reconcile()
    print(
        "Reconciled bank transactions. "
        f"account={stats['account']} bank={stats['bank_transactions']} "
        f"reversed={stats['reversed']} credits={stats['credits']} "
        f"transfers={stats['transfers']} matched={stats['matched']} "
        f"imported={stats['imported']} review_required={stats['review_required']} "
        f"imported_total={stats['imported_total']:.2f}"
    )


if __name__ == "__main__":
    main()
