"""Bank-specific reading of statement rows.

The reconciliation engine only needs a few answers about each bank row: is it a
purchase, a transfer or a same-merchant correction, which payment channel does
it show, and what merchant does it name. Each bank writes descriptions
differently, so those answers live in an adapter. The matching logic itself
(amounts, dates, split charges, reversals) stays bank-neutral.

* ``ScotiaAdapter`` reads rows from the Scotia online-banking collector.
* ``GenericAdapter`` reads rows imported from any bank's OFX/QFX or CSV export
  (see ``scripts/importers/bank_statement.py``). It is deliberately
  conservative: when a row could be a transfer or a card payment it is not
  treated as a purchase, because importing it would double-count spending.
"""
import re


# Bank-only ledger rows: (platform, order_id prefix) for each adapter.
BANK_ONLY_PLATFORMS = frozenset({"scotia", "scotiabank", "bank"})
BANK_ONLY_ORDER_PREFIXES = ("scotia-", "bank-")

# ``bank_transactions.source`` values written by the statement importer.
STATEMENT_SOURCES = frozenset({"statement_ofx", "statement_csv"})


def is_bank_only_order(platform, order_id):
    return (str(platform or "") in BANK_ONLY_PLATFORMS
            or str(order_id or "").startswith(BANK_ONLY_ORDER_PREFIXES))


def _text(row_or_description):
    if isinstance(row_or_description, dict):
        return str(row_or_description.get("description") or "")
    return str(row_or_description or "")


def _kind(row):
    return str(row.get("kind") or "").lower() if isinstance(row, dict) else ""


class ScotiaAdapter:
    id = "scotia"
    institution = "Scotiabank"
    short_name = "Scotia"
    platform = "scotiabank"
    order_prefix = "scotia-"
    default_source = "scotia_dom"
    payment_source = "scotia_account_instrument_mapping"
    debit_source = "scotia_statement_account_debit"

    def merchant_key(self, row):
        value = _text(row).lower()
        value = re.sub(r"^(pos purchase|correction)\s+", "", value)
        value = re.sub(r"^(opos|apos|fpos)\s+", "", value)
        return re.sub(r"\s+", " ", value).strip()

    def is_transfer(self, row):
        value = _text(row).lower()
        return "interac e-transfer" in value or value.startswith("withdrawal ")

    def is_purchase(self, row):
        value = _text(row).lower()
        return "pos purchase" in value or "service charge" in value

    def is_correction(self, row):
        return bool(re.match(r"^correction\b", _text(row), re.IGNORECASE))

    def is_service_charge(self, row):
        return "service charge" in _text(row).lower()

    def payment_channel(self, row, account=None):
        value = _text(row).lower()
        # OPOS is an online card purchase; AIPS appears on in-app purchases such as
        # "Pos Purchase Aips Presto Mobi" (the PRESTO mobile app).
        if " opos " in f" {value} " or " aips " in f" {value} ":
            return "online"
        if " fpos " in f" {value} " or " apos " in f" {value} ":
            return "in_store"
        if "service charge" in value:
            return "account_debit"
        return "unknown"

    def merchant_name(self, row):
        return re.sub(r"^(pos purchase|correction)\s+(opos|apos|fpos)\s+", "",
                      _text(row), flags=re.IGNORECASE).strip()


# Statement prefixes that describe the channel rather than the merchant.
_GENERIC_PREFIX = re.compile(
    r"^(?:(?:pos|opos|apos|fpos|point of sale|purchase|debit card purchase|visa debit"
    r"|interac purchase|interac|contactless|pre-?authorized(?: debit)?|pad|correction"
    r"|reversal)\b[\s:\-]*)+",
    re.IGNORECASE,
)
_TRANSFER_TERMS = (
    "e-transfer", "etransfer", "e transfer", "transfer", "withdrawal", "atm ",
    "cash advance", "payment - thank you", "payment thank you", "payment received",
    "credit card payment", "card payment", "bill payment", "online banking payment",
    "loc pay", "preauth pymt", "investment", "tfsa", "rrsp",
)
_FEE_TERMS = ("service charge", "monthly fee", "account fee", "overdraft fee", "nsf fee")
_PURCHASE_KINDS = {"pos", "debit", "purchase", "fee", "srvchg", "check", "directdebit", "repeatpmt"}
_TRANSFER_KINDS = {"xfer", "atm", "cash", "transfer", "payment", "dep", "directdep", "int", "div"}


class GenericAdapter:
    """Rows imported from a bank's own OFX/QFX or CSV statement export."""

    id = "generic"
    institution = "Bank"
    short_name = "bank"
    platform = "bank"
    order_prefix = "bank-"
    default_source = "statement_csv"
    payment_source = "bank_account_instrument_mapping"
    debit_source = "bank_statement_account_debit"

    def merchant_key(self, row):
        value = _GENERIC_PREFIX.sub("", _text(row).lower())
        return re.sub(r"\s+", " ", value).strip()

    def _excluded(self, row):
        terms = row.get("exclude_terms", ()) if isinstance(row, dict) else ()
        value = _text(row).lower()
        return any(str(term).lower() in value for term in terms if str(term).strip())

    def is_transfer(self, row):
        value = _text(row).lower()
        return (_kind(row) in _TRANSFER_KINDS or self._excluded(row)
                or any(term in value for term in _TRANSFER_TERMS))

    def is_purchase(self, row):
        if self.is_transfer(row):
            return False
        kind = _kind(row)
        # CSV exports carry no transaction type; a debit that is not a transfer
        # is the purchase. OFX types outside the purchase set stay out.
        return kind in _PURCHASE_KINDS or kind in {"", "other"}

    def is_correction(self, row):
        return bool(re.match(r"^\s*(correction|reversal)\b", _text(row), re.IGNORECASE))

    def is_service_charge(self, row):
        value = _text(row).lower()
        return _kind(row) in {"fee", "srvchg"} or any(term in value for term in _FEE_TERMS)

    def payment_channel(self, row, account=None):
        if self.is_service_charge(row):
            return "account_debit"
        if (account or {}).get("statement_type") == "credit_card":
            return "card_statement"
        return "unknown"

    def merchant_name(self, row):
        return _GENERIC_PREFIX.sub("", _text(row)).strip(" -:")


ADAPTERS = {adapter.id: adapter for adapter in (ScotiaAdapter(), GenericAdapter())}
SCOTIA = ADAPTERS["scotia"]


def adapter_for(row):
    """Adapter for a bank row from a collector cache, a statement import or SQLite."""
    if isinstance(row, dict):
        explicit = row.get("bank_adapter")
        if explicit in ADAPTERS:
            return ADAPTERS[explicit]
        if row.get("source") in STATEMENT_SOURCES:
            return ADAPTERS["generic"]
    return SCOTIA
