import json
import sys
from collections import OrderedDict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from db import read_csv, write_csv


ROOT = Path(__file__).resolve().parents[2]
TRANSACTIONS_PATH = ROOT / "data" / "transactions.csv"
ASSIGNMENTS_PATH = ROOT / "data" / "payment_assignments.csv"
CARDS_PATH = ROOT / "config" / "payment_cards.json"

ASSIGNMENT_FIELDS = [
    "date",
    "platform",
    "order_id",
    "merchant",
    "total",
    "currency",
    "card_id",
    "card_label",
    "assignment_status",
    "assignment_source",
    "payment_channel",
    "notes",
]


def money_text(value):
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def tx_key(row):
    return f"{row.get('platform', '')}|{row.get('order_id', '')}"


def load_cards(path=CARDS_PATH):
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    cards = payload.get("cards", {})
    default_card_id = payload.get("default_card_id", "unassigned")
    if default_card_id not in cards:
        raise ValueError(f"default_card_id is not defined in cards: {default_card_id}")
    return cards, default_card_id


def card_label(cards, card_id):
    return cards.get(card_id, cards["unassigned"]).get("display_name", card_id)


def normalize_network(value):
    return str(value or "").strip().lower().replace(" ", "")


def card_for_last4(last4, network, cards):
    last4 = str(last4 or "").strip()
    network = normalize_network(network)
    if not last4:
        return ""

    for card_id, card in cards.items():
        if card_id == "unassigned":
            continue
        if last4 != str(card.get("last4") or "").strip():
            continue
        card_network = normalize_network(card.get("network"))
        if network and card_network and network != card_network:
            continue
        return card_id
    return ""


def base_assignment(tx, existing, cards, default_card_id):
    card_id = existing.get("card_id") or default_card_id
    if card_id not in cards:
        card_id = default_card_id
    status = existing.get("assignment_status") or (
        "needs_review" if card_id == default_card_id else "confirmed"
    )
    payment_channel = existing.get("payment_channel") or (
        "online"
        if tx.get("platform") in {"walmart_ca", "amazon_ca", "uber_eats", "fantuan", "tnt_ca"}
        else "unknown"
    )
    return {
        "date": tx.get("date", ""),
        "platform": tx.get("platform", ""),
        "order_id": tx.get("order_id", ""),
        "merchant": tx.get("merchant", ""),
        "total": money_text(tx.get("total")),
        "currency": tx.get("currency", "CAD"),
        "card_id": card_id,
        "card_label": card_label(cards, card_id),
        "assignment_status": status,
        "assignment_source": existing.get("assignment_source") or "manual_required",
        "payment_channel": payment_channel,
        "notes": existing.get("notes", ""),
    }


def refresh_assignment_rows(transactions, assignments, cards, default_card_id):
    existing_by_key = {tx_key(row): row for row in assignments}
    merged = OrderedDict()
    for tx in transactions:
        key = tx_key(tx)
        merged[key] = base_assignment(
            tx,
            existing_by_key.get(key, {}),
            cards,
            default_card_id,
        )
    return list(merged.values())


def refresh_assignment_file(
    transactions_path=TRANSACTIONS_PATH,
    assignments_path=ASSIGNMENTS_PATH,
    cards_path=CARDS_PATH,
):
    cards, default_card_id = load_cards(cards_path)
    rows = refresh_assignment_rows(
        read_csv(transactions_path),
        read_csv(assignments_path),
        cards,
        default_card_id,
    )
    write_csv(assignments_path, ASSIGNMENT_FIELDS, rows)
    return {
        "assignments": len(rows),
        "unassigned": sum(row["card_id"] == default_card_id for row in rows),
    }
