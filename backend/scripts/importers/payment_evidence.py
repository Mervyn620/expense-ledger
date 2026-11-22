import json
import sys
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import read_csv, sync_evidence_and_payments_from_files, write_csv
from tnt_payment_evidence import merge_tnt_evidence
from payment_assignments import (
    ASSIGNMENT_FIELDS,
    base_assignment,
    card_for_last4,
    card_label,
    load_cards,
    normalize_network,
    tx_key,
)


TRANSACTIONS_PATH = ROOT / "data" / "transactions.csv"
ASSIGNMENTS_PATH = ROOT / "data" / "payment_assignments.csv"
EVIDENCE_PATH = ROOT / "data" / "payment_evidence.json"
CARDS_PATH = ROOT / "config" / "payment_cards.json"
DEFAULT_CREDIT_SOURCE = "user_default_credit_card"


def default_credit_card(evidence, cards):
    if evidence.get("card_last4") or evidence.get("status") != "method_only":
        return ""
    method = "".join(str(evidence.get("payment_method") or "").lower().split()).replace("／", "/")
    if method not in {"creditcard", "credit/debitcard", "creditcard/debitcard",
                      "信用卡", "信用卡/借记卡", "信用卡/借記卡"}:
        return ""
    defaults = [(key, card) for key, card in cards.items()
                if card.get("kind") == "credit_card" and card.get("default_for_unspecified_credit") is True]
    if len(defaults) != 1:
        return ""
    card_id, card = defaults[0]
    network = normalize_network(evidence.get("card_network"))
    return card_id if not network or network == normalize_network(card.get("network")) else ""


def card_for_evidence(evidence, cards):
    return card_for_last4(
        evidence.get("card_last4"),
        evidence.get("card_network"),
        cards,
    )


def wallet_for_method(evidence, cards):
    method = str(evidence.get("payment_method") or "").lower()
    wallet_markers = (
        ("wechat_pay", ("wechat", "微信")),
        ("alipay", ("alipay", "支付宝")),
    )
    for card_id, markers in wallet_markers:
        if card_id in cards and any(marker in method for marker in markers):
            return card_id
    return ""


def base_assignment_for_transaction(tx, existing, cards, default_card_id):
    return base_assignment(tx, existing, cards, default_card_id)


def load_evidence(path=EVIDENCE_PATH):
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    rows = payload.get("evidence", []) if isinstance(payload, dict) else []
    merged = {tx_key(row): row for row in rows}
    merge_tnt_evidence(merged, path.parent.parent)
    return list(merged.values())


def evidence_note(row):
    parts = []
    if row.get("payment_method"):
        parts.append(f"payment_method={row.get('payment_method')}")
    if row.get("card_last4"):
        label = row.get("card_network") or "card"
        parts.append(f"receipt_payment={label} ending {row.get('card_last4')}")
    if row.get("status"):
        parts.append(f"evidence_status={row.get('status')}")
    return "; ".join(parts)


def evidence_source(row):
    if row.get("source") == "tnt_order_details_api":
        return "tnt_order_details_api"
    platform = row.get("platform", "platform")
    if row.get("card_last4"):
        return f"{platform}_payment_dom"
    if row.get("payment_method"):
        return f"{platform}_payment_method_dom"
    return f"{platform}_payment_not_found"


def update_assignments(transactions, assignments, evidence_rows, cards, default_card_id):
    existing_by_key = {tx_key(row): row for row in assignments}
    evidence_by_key = {tx_key(row): row for row in evidence_rows}
    merged = OrderedDict()
    stats = {
        "matched": 0,
        "confirmed": 0,
        "method_only": 0,
        "inferred": 0,
        "not_found": 0,
    }

    for tx in transactions:
        key = tx_key(tx)
        assignment = base_assignment_for_transaction(
            tx,
            existing_by_key.get(key, {}),
            cards,
            default_card_id,
        )
        evidence = evidence_by_key.get(key)
        if evidence:
            stats["matched"] += 1
            card_id = card_for_evidence(evidence, cards)
            if card_id:
                assignment["card_id"] = card_id
                assignment["card_label"] = card_label(cards, card_id)
                assignment["assignment_status"] = "confirmed"
                assignment["assignment_source"] = evidence_source(evidence)
                assignment["notes"] = evidence_note(evidence)
                stats["confirmed"] += 1
            elif assignment["card_id"] == default_card_id or (
                assignment["assignment_source"] == DEFAULT_CREDIT_SOURCE
                and (evidence.get("payment_method") or evidence.get("card_last4"))
            ):
                wallet_id = wallet_for_method(evidence, cards)
                credit_id = default_credit_card(evidence, cards)
                assignment["assignment_source"] = evidence_source(evidence)
                if wallet_id:
                    assignment["card_id"] = wallet_id
                    assignment["card_label"] = card_label(cards, wallet_id)
                    assignment["assignment_status"] = "confirmed"
                    stats["confirmed"] += 1
                elif credit_id:
                    assignment["card_id"] = credit_id
                    assignment["card_label"] = card_label(cards, credit_id)
                    assignment["assignment_status"] = "inferred"
                    assignment["assignment_source"] = DEFAULT_CREDIT_SOURCE
                    stats["inferred"] += 1
                else:
                    assignment["card_id"] = default_card_id
                    assignment["card_label"] = card_label(cards, default_card_id)
                    assignment["assignment_status"] = "needs_review"
                    if evidence.get("payment_method"):
                        stats["method_only"] += 1
                    else:
                        stats["not_found"] += 1
                assignment["notes"] = evidence_note(evidence)

        merged[key] = assignment

    for assignment in assignments:
        key = tx_key(assignment)
        if key and key not in merged:
            merged[key] = {field: assignment.get(field, "") for field in ASSIGNMENT_FIELDS}

    return list(merged.values()), stats


def main():
    evidence_rows = load_evidence()
    if not evidence_rows:
        print("No store payment evidence export found.")
        return

    cards, default_card_id = load_cards()
    rows, stats = update_assignments(
        read_csv(TRANSACTIONS_PATH),
        read_csv(ASSIGNMENTS_PATH),
        evidence_rows,
        cards,
        default_card_id,
    )
    write_csv(ASSIGNMENTS_PATH, ASSIGNMENT_FIELDS, rows)
    db_stats = sync_evidence_and_payments_from_files()
    print(
        "Imported payment evidence. "
        f"evidence={len(evidence_rows)} matched={stats['matched']} "
        f"confirmed={stats['confirmed']} inferred={stats['inferred']} method_only={stats['method_only']} "
        f"not_found={stats['not_found']} "
        f"db_payments={db_stats['order_payments']}"
    )


if __name__ == "__main__":
    main()
