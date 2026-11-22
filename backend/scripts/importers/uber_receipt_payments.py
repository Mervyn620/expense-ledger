import json
import sys
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import read_csv, sync_evidence_and_payments_from_files, write_csv
from payment_assignments import (
    ASSIGNMENT_FIELDS,
    base_assignment,
    card_for_last4,
    card_label,
    load_cards,
    tx_key,
)


TRANSACTIONS_PATH = ROOT / "data" / "transactions.csv"
ASSIGNMENTS_PATH = ROOT / "data" / "payment_assignments.csv"
RECEIPTS_PATH = ROOT / "data" / "uber_eats_receipts.json"
CARDS_PATH = ROOT / "config" / "payment_cards.json"


def card_for_receipt(receipt, cards):
    return card_for_last4(
        receipt.get("card_last4"),
        receipt.get("card_network"),
        cards,
    )


def base_assignment_for_transaction(tx, existing, cards, default_card_id):
    return base_assignment(tx, existing, cards, default_card_id)


def load_receipts(path=RECEIPTS_PATH):
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload.get("receipts", []) if isinstance(payload, dict) else []


def receipt_key(receipt):
    return f"uber_eats|{receipt.get('order_id') or receipt.get('workflowUuid') or receipt.get('workflow_uuid') or ''}"


def update_assignments(transactions, assignments, receipts, cards, default_card_id):
    existing_by_key = {tx_key(row): row for row in assignments}
    receipt_by_key = {receipt_key(receipt): receipt for receipt in receipts}
    merged = OrderedDict()
    stats = {
        "assignments": 0,
        "confirmed": 0,
        "unknown_receipts": 0,
        "failed_receipts": 0,
    }

    for tx in transactions:
        key = tx_key(tx)
        assignment = base_assignment_for_transaction(
            tx,
            existing_by_key.get(key, {}),
            cards,
            default_card_id,
        )
        receipt = receipt_by_key.get(key)
        if receipt and tx.get("platform") == "uber_eats":
            stats["assignments"] += 1
            card_id = card_for_receipt(receipt, cards)
            if card_id:
                assignment["card_id"] = card_id
                assignment["card_label"] = card_label(cards, card_id)
                assignment["assignment_status"] = "confirmed"
                assignment["assignment_source"] = "uber_receipt_api"
                assignment["notes"] = (
                    f"receipt_payment={receipt.get('card_network') or 'card'} ending {receipt.get('card_last4')}"
                )
                stats["confirmed"] += 1
            elif receipt.get("ok") is False or receipt.get("login_likely"):
                stats["failed_receipts"] += 1
            else:
                stats["unknown_receipts"] += 1
                if assignment["card_id"] == default_card_id:
                    assignment["assignment_status"] = "needs_review"
                    assignment["assignment_source"] = "uber_receipt_api"
                    assignment["notes"] = "receipt_payment_not_found"

        merged[key] = assignment

    for assignment in assignments:
        key = tx_key(assignment)
        if key and key not in merged:
            merged[key] = {field: assignment.get(field, "") for field in ASSIGNMENT_FIELDS}

    return list(merged.values()), stats


def main():
    cards, default_card_id = load_cards()
    receipts = load_receipts()
    if not receipts:
        print("No Uber Eats receipt payment export found.")
        return

    rows, stats = update_assignments(
        read_csv(TRANSACTIONS_PATH),
        read_csv(ASSIGNMENTS_PATH),
        receipts,
        cards,
        default_card_id,
    )
    write_csv(ASSIGNMENTS_PATH, ASSIGNMENT_FIELDS, rows)
    db_stats = sync_evidence_and_payments_from_files()
    print(
        "Imported Uber Eats receipt payment cards. "
        f"receipts={len(receipts)} matched={stats['assignments']} "
        f"confirmed={stats['confirmed']} unknown={stats['unknown_receipts']} "
        f"failed={stats['failed_receipts']} "
        f"db_payments={db_stats['order_payments']}"
    )


if __name__ == "__main__":
    main()
