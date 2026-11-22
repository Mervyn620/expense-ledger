"""Read split tenders (Uber Cash plus one card) from captured Uber receipt text.

Uber Eats can pay one order partly from Uber Cash and partly from a card. The
bank then shows only the card portion, so reconciliation must compare the bank
charge with that portion instead of the order total. Parsing is deliberately
strict: refunds, negative tenders, more than one card, or tenders that do not
add up to the order total all return None.
"""
import json
import re
from pathlib import Path


CARD_NETWORKS = ("Visa", "Mastercard", "American Express", "Amex", "Discover")
_TENDER = re.compile(
    r"(Uber Cash|" + "|".join(re.escape(name) for name in CARD_NETWORKS) + r")"
    r"(?:\s*[•·*]+\s*(\d{4}))?\s*(-)?\s*CA\$\s?(\d+(?:\.\d{2})?)",
    re.I,
)
_START = re.compile(r"(?:^|\s)(付款|Payments?)\s", re.I)
_END = re.compile(r"订单详情|退款|Order details|Refund", re.I)


def _cents(value):
    return int(round(float(value) * 100))


def split_tenders(receipt):
    """Return [{'method','last4','amount'}] when the payment section is a clean split."""
    for snippet in receipt.get("payment_snippets") or []:
        start = _START.search(str(snippet))
        if not start:
            continue
        section = str(snippet)[start.end():]
        end = _END.search(section)
        if end:
            section = section[:end.start()]
        tenders = []
        for method, last4, negative, amount in _TENDER.findall(section):
            if negative:
                return None
            tenders.append({"method": method, "last4": last4 or "", "amount": _cents(amount) / 100})
        cards = [t for t in tenders if t["method"].casefold() != "uber cash"]
        if len(tenders) >= 2 and len(cards) == 1 and cards[0]["last4"]:
            return tenders
        return None
    return None


def tolerance_cents(order_total):
    """Same adjustment tolerance the bank split-charge matcher already accepts."""
    return int(round(min(3.0, max(1.0, float(order_total) * 0.02)) * 100))


def card_portion(receipt, order_total):
    """Card-paid part of a split Uber order, or None."""
    tenders = split_tenders(receipt)
    if not tenders:
        return None
    try:
        total = _cents(order_total)
    except (TypeError, ValueError):
        return None
    paid = sum(_cents(t["amount"]) for t in tenders)
    if total <= 0 or abs(paid - total) > tolerance_cents(order_total):
        return None
    card = next(t for t in tenders if t["method"].casefold() != "uber cash")
    summary = " + ".join(
        f"{t['method']}{' ••••' + t['last4'] if t['last4'] else ''} {t['amount']:.2f}" for t in tenders
    )
    return {"amount": card["amount"], "last4": card["last4"], "network": card["method"], "summary": summary}


def load_receipts_by_key(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    rows = {}
    for receipt in payload.get("receipts", []) if isinstance(payload, dict) else []:
        order_id = receipt.get("order_id") or receipt.get("workflowUuid") or ""
        if order_id:
            rows[f"uber_eats|{order_id}"] = receipt
    return rows
