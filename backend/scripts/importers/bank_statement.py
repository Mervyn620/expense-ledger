"""Import a bank's own statement export (OFX/QFX or CSV) for reconciliation.

Most Canadian banks let customers download transactions as OFX/QFX (an
industry format) or CSV. This importer normalizes either into the same row
shape the Scotia collector writes, and stores one file per account under
``data/bank_statements/<account_id>.json``. ``scripts/reconciliation/scotia.py``
then reconciles these rows with the generic bank adapter.

Re-importing an overlapping export is safe: rows are keyed by the bank's
transaction id (OFX FITID) or, for CSV, by date, amount, description and the
occurrence of that row on that day.

Only the last four digits of an account number are kept, and long digit runs
in descriptions (card or reference numbers) are masked.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import atomic_write_text  # noqa: E402


CONTRACT = "expense-bank-statement/v1"
SOURCE_BY_FORMAT = {"ofx": "statement_ofx", "csv": "statement_csv"}
STATEMENT_TYPES = {"bank_account", "credit_card"}
ACCOUNT_ID = re.compile(r"^[a-z0-9_]{2,64}$")
MAX_BYTES = 5 * 1024 * 1024


class StatementError(ValueError):
    """The statement cannot be read without guessing; the message says why."""


def statements_dir(root=ROOT):
    return Path(root) / "data" / "bank_statements"


def clean_description(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    # Card and reference numbers: masked card (4506********1234) or 8+ digits.
    text = re.sub(r"\b\d{4,6}[*xX•]{4,}\d{0,4}\b", "••••", text)
    text = re.sub(r"\d{8,}", "••••", text)
    return text


def parse_amount(value):
    text = str(value or "").strip().replace("$", "").replace("CAD", "").replace(" ", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if "," in text and "." not in text:
        text = text.replace(",", ".")      # 12,34 decimal comma
    text = text.replace(",", "")           # 1,234.56 thousands separator
    try:
        amount = round(float(text), 2)
    except ValueError:
        raise StatementError(f"无法读取金额：{value!r}") from None
    return -amount if negative else amount


# ---------------------------------------------------------------- OFX / QFX

def _ofx_value(block, tag):
    match = re.search(rf"<{tag}>([^<\r\n]*)", block, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def parse_ofx(text):
    blocks = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, re.IGNORECASE | re.DOTALL)
    if not blocks:
        raise StatementError("OFX 文件里没有找到交易记录（STMTTRN）。")
    account_number = _ofx_value(text, "ACCTID")
    meta = {
        "currency": (_ofx_value(text, "CURDEF") or "CAD").upper(),
        "statement_type": "credit_card" if re.search(r"<CCSTMTRS>", text, re.IGNORECASE) else "bank_account",
        "last4": re.sub(r"\D", "", account_number)[-4:],
        "institution": _ofx_value(text, "ORG"),
    }
    rows = []
    for block in blocks:
        posted = _ofx_value(block, "DTPOSTED")
        if not re.match(r"^\d{8}", posted):
            raise StatementError(f"OFX 交易日期无效：{posted!r}")
        amount = parse_amount(_ofx_value(block, "TRNAMT"))
        if amount is None:
            raise StatementError("OFX 交易缺少金额（TRNAMT）。")
        name, memo = _ofx_value(block, "NAME"), _ofx_value(block, "MEMO")
        description = name if not memo or memo.lower() in name.lower() else f"{name} {memo}".strip()
        rows.append({
            "date": f"{posted[:4]}-{posted[4:6]}-{posted[6:8]}",
            "amount": amount,
            "description": description,
            "kind": _ofx_value(block, "TRNTYPE").lower(),
            "reference": _ofx_value(block, "FITID"),
            "currency": (_ofx_value(block, "CURRENCY") or meta["currency"]).upper()[:3],
        })
    return meta, rows


# ---------------------------------------------------------------------- CSV

HEADER_ALIASES = {
    "date": ("date", "transaction date", "posted date", "posting date", "trans. date", "date posted"),
    "description": ("description", "transaction description", "description 1", "payee", "merchant",
                    "name", "details", "transaction details"),
    "memo": ("memo", "description 2", "notes"),
    "amount": ("amount", "transaction amount", "cad$", "amount (cad)", "cad amount"),
    "debit": ("debit", "withdrawal", "withdrawals", "money out", "debits", "paid out"),
    "credit": ("credit", "deposit", "deposits", "money in", "credits", "paid in"),
    "currency": ("currency",),
}
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%b %d %Y")


def _header_map(header):
    names = [str(cell or "").strip().lower() for cell in header]
    mapping = {}
    for field, aliases in HEADER_ALIASES.items():
        for index, name in enumerate(names):
            if name in aliases and field not in mapping:
                mapping[field] = index
    return mapping


def _date_parser(values, date_format):
    if date_format:
        return lambda value: datetime.strptime(value.strip(), date_format).date()
    samples = [value.strip() for value in values if value.strip()]
    for fmt in DATE_FORMATS:
        try:
            for value in samples:
                datetime.strptime(value, fmt)
        except ValueError:
            continue
        return lambda value, fmt=fmt: datetime.strptime(value.strip(), fmt).date()
    slashed = [re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", value) for value in samples]
    if samples and all(slashed):
        firsts = {int(match.group(1)) for match in slashed}
        seconds = {int(match.group(2)) for match in slashed}
        if max(firsts) > 12 and max(seconds) <= 12:
            fmt = "%d/%m/%Y"
        elif max(seconds) > 12 and max(firsts) <= 12:
            fmt = "%m/%d/%Y"
        else:
            raise StatementError(
                "无法判断日期是“月/日/年”还是“日/月/年”。请指定日期格式，例如 %m/%d/%Y。")
        return lambda value, fmt=fmt: datetime.strptime(re.sub(r"[.-]", "/", value.strip()), fmt).date()
    raise StatementError("无法识别日期格式。请指定日期格式，例如 %Y-%m-%d。")


def parse_csv(text, *, columns=None, date_format=None, purchases_positive=False, currency="CAD"):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    table = [row for row in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in row)]
    if not table:
        raise StatementError("CSV 文件是空的。")
    if columns:
        names = [name.strip().lower() for name in columns]
        unknown = set(names) - set(HEADER_ALIASES) - {"skip", "balance"}
        if unknown:
            raise StatementError(f"未知的列名：{', '.join(sorted(unknown))}")
        mapping = {name: index for index, name in enumerate(names) if name in HEADER_ALIASES}
        body = table[1:] if _header_map(table[0]) else table
    else:
        mapping = _header_map(table[0])
        if "date" not in mapping:
            raise StatementError(
                "CSV 没有可识别的表头。请按顺序指定各列，例如 date,description,debit,credit,balance。")
        body = table[1:]
    if "date" not in mapping or "description" not in mapping:
        raise StatementError("CSV 至少需要日期列和描述列。")
    if "amount" not in mapping and not ({"debit", "credit"} & mapping.keys()):
        raise StatementError("CSV 需要金额列，或者支出/存入两列。")

    cell = lambda row, field: row[mapping[field]].strip() if field in mapping and mapping[field] < len(row) else ""
    to_date = _date_parser([cell(row, "date") for row in body], date_format)
    rows = []
    for row in body:
        if not cell(row, "date"):
            continue
        if "amount" in mapping:
            amount = parse_amount(cell(row, "amount"))
            if amount is None:
                continue
            if purchases_positive:
                amount = -amount
        else:
            debit, credit = parse_amount(cell(row, "debit")), parse_amount(cell(row, "credit"))
            if debit is None and credit is None:
                continue
            amount = round((credit or 0) - abs(debit or 0), 2)
        memo = cell(row, "memo")
        description = cell(row, "description")
        if memo and memo.lower() not in description.lower():
            description = f"{description} {memo}"
        try:
            posted = to_date(cell(row, "date"))
        except ValueError:
            raise StatementError(f"日期和其他行格式不一致：{cell(row, 'date')!r}") from None
        rows.append({
            "date": posted.isoformat(),
            "amount": amount,
            "description": description,
            "kind": "",
            "reference": "",
            "currency": (cell(row, "currency") or currency).upper()[:3],
        })
    return {"currency": currency, "statement_type": "", "last4": "", "institution": ""}, rows


# ------------------------------------------------------------------ storage

def detect_format(text, filename=""):
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".ofx", ".qfx", ".qbo"} or re.search(r"<OFX>|OFXHEADER", text[:2048], re.IGNORECASE):
        return "ofx"
    return "csv"


def normalize(rows, account_id, source):
    seen = Counter()
    result = []
    for row in rows:
        if not row["amount"]:
            continue
        description = clean_description(row["description"])
        direction = "debit" if row["amount"] < 0 else "credit"
        amount = f"{abs(row['amount']):.2f}"
        if row["reference"]:
            identity = f"{account_id}|fitid|{row['reference']}"
        else:
            base = f"{account_id}|{row['date']}|{direction}|{amount}|{description.lower()}"
            seen[base] += 1
            identity = f"{base}|{seen[base]}"
        result.append({
            "transaction_id": hashlib.sha1(identity.encode("utf-8")).hexdigest()[:20],
            "date": row["date"],
            "description": description,
            "amount": amount,
            "direction": direction,
            "currency": row["currency"] or "CAD",
            "kind": row["kind"],
            "account_id": account_id,
            "source": source,
        })
    return result


def import_statement(text, *, account_id, filename="", institution="", statement_type="",
                     fmt="auto", columns=None, date_format=None, purchases_positive=False,
                     currency="CAD", root=ROOT, dry_run=False):
    if not ACCOUNT_ID.match(account_id or ""):
        raise StatementError("账户 ID 只能包含小写字母、数字和下划线，例如 rbc_visa。")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise StatementError("流水文件超过 5 MB。")
    if statement_type and statement_type not in STATEMENT_TYPES:
        raise StatementError("账户类型只能是 bank_account 或 credit_card。")
    fmt = detect_format(text, filename) if fmt == "auto" else fmt
    if fmt == "ofx":
        meta, parsed = parse_ofx(text)
    elif fmt == "csv":
        meta, parsed = parse_csv(text, columns=columns, date_format=date_format,
                                 purchases_positive=purchases_positive, currency=currency)
    else:
        raise StatementError(f"不支持的格式：{fmt}")
    rows = normalize(parsed, account_id, SOURCE_BY_FORMAT[fmt])
    if not rows:
        raise StatementError("文件里没有可导入的交易。")

    path = statements_dir(root) / f"{account_id}.json"
    existing = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    merged = {row["transaction_id"]: row for row in existing.get("transactions", [])}
    added = sum(row["transaction_id"] not in merged for row in rows)
    merged.update({row["transaction_id"]: row for row in rows})
    payload = {
        "contract": CONTRACT,
        "adapter": "generic",
        "account_id": account_id,
        "institution": institution or existing.get("institution") or meta.get("institution") or "Bank",
        "statement_type": statement_type or existing.get("statement_type") or meta.get("statement_type") or "bank_account",
        "last4": meta.get("last4") or existing.get("last4", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "transactions": sorted(merged.values(), key=lambda row: (row["date"], row["transaction_id"])),
    }
    if not dry_run:
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    dates = [row["date"] for row in rows]
    return {
        "account_id": account_id,
        "format": fmt,
        "path": str(path),
        "rows_in_file": len(rows),
        "added": added,
        "already_present": len(rows) - added,
        "total_rows": len(payload["transactions"]),
        "date_range": [min(dates), max(dates)],
        "institution": payload["institution"],
        "statement_type": payload["statement_type"],
        "dry_run": dry_run,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import a bank OFX/QFX or CSV statement for reconciliation.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--account", required=True, help="Account id, e.g. rbc_visa (lowercase, digits, _).")
    parser.add_argument("--institution", default="", help="Bank name shown in the ledger, e.g. RBC.")
    parser.add_argument("--statement-type", default="", choices=["", *sorted(STATEMENT_TYPES)])
    parser.add_argument("--format", default="auto", choices=["auto", "ofx", "csv"])
    parser.add_argument("--columns", default="", help="Headerless CSV column order, e.g. date,description,debit,credit,balance.")
    parser.add_argument("--date-format", default=None, help="strptime format when dates are ambiguous, e.g. %%m/%%d/%%Y.")
    parser.add_argument("--purchases-positive", action="store_true",
                        help="CSV amount column shows purchases as positive numbers (common for credit cards).")
    parser.add_argument("--currency", default="CAD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    raw = args.file.read_bytes()
    text = raw.decode("utf-8-sig", errors="strict") if raw[:3] == b"\xef\xbb\xbf" else raw.decode("utf-8", errors="replace")
    try:
        summary = import_statement(
            text, account_id=args.account, filename=args.file.name, institution=args.institution,
            statement_type=args.statement_type, fmt=args.format,
            columns=[c for c in args.columns.split(",") if c] or None, date_format=args.date_format,
            purchases_positive=args.purchases_positive, currency=args.currency, dry_run=args.dry_run)
    except StatementError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
