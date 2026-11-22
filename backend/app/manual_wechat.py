"""Manual WeChat payments, kept apart from imported CAD order accounting."""

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from scripts.ledger.schema import create_schema


MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class DuplicateRequestConflict(Exception):
    """The same request key was reused with different payment details."""


def minor_units(amount):
    value = Decimal(str(amount))
    if not value.is_finite() or value <= 0 or value > Decimal("99999999.99"):
        raise ValueError("金额须大于零，且不超过 99,999,999.99。")
    minor = value * 100
    if minor != minor.to_integral_value():
        raise ValueError("金额最多保留两位小数。")
    return int(minor)


def fields(draft):
    merchant = re.sub(r"\s+", " ", draft.merchant).strip()
    note = draft.note.strip()
    if not merchant:
        raise ValueError("请填写收款方或用途。")
    if len(merchant) > 120 or len(note) > 300:
        raise ValueError("收款方或备注过长。")
    return (draft.paid_on.isoformat(), merchant, minor_units(draft.amount),
            draft.currency, note)


class ManualWechatStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)

    def _connect(self, write=False):
        if not self.db_path.exists():
            raise FileNotFoundError("Ledger database is missing.")
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        if write:
            create_schema(conn)
        else:
            conn.execute("PRAGMA query_only = ON")
        return conn

    @staticmethod
    def _row(row):
        return dict(row) if row else None

    def list(self, month="all", limit=200):
        if month != "all" and not MONTH.fullmatch(month):
            raise ValueError("月份须为 YYYY-MM 或 all。")
        limit = max(1, min(int(limit), 500))
        clause = "" if month == "all" else "WHERE SUBSTR(paid_on, 1, 7) = ?"
        params = [] if month == "all" else [month]
        with closing(self._connect()) as conn:
            months = [row[0] for row in conn.execute(
                "SELECT DISTINCT SUBSTR(paid_on, 1, 7) FROM manual_wechat_payments "
                "ORDER BY paid_on DESC")]
            rows = [self._row(row) for row in conn.execute(
                f"SELECT id, paid_on, merchant, amount_minor, currency, note, created_at, updated_at "
                f"FROM manual_wechat_payments {clause} ORDER BY paid_on DESC, id DESC LIMIT ?",
                [*params, limit])]
            totals = {currency: {"count": 0, "amount_minor": 0}
                      for currency in ("CNY", "CAD")}
            for row in conn.execute(
                f"SELECT currency, COUNT(*) AS count, SUM(amount_minor) AS amount_minor "
                f"FROM manual_wechat_payments {clause} GROUP BY currency", params):
                totals[row["currency"]] = {"count": row["count"],
                                           "amount_minor": row["amount_minor"]}
            return {"selected_month": month, "months": months, "rows": rows, "by_currency": totals,
                    "total_count": sum(item["count"] for item in totals.values())}

    def create(self, request_id, draft):
        values = fields(draft)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect(write=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""INSERT INTO manual_wechat_payments
                (request_id, paid_on, merchant, amount_minor, currency, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO NOTHING""",
                (str(request_id), *values, now, now))
            row = conn.execute("""SELECT id, paid_on, merchant, amount_minor,
                currency, note, created_at, updated_at FROM manual_wechat_payments
                WHERE request_id = ?""", (str(request_id),)).fetchone()
            if tuple(row[key] for key in ("paid_on", "merchant", "amount_minor", "currency", "note")) != values:
                raise DuplicateRequestConflict()
            conn.commit()
            return self._row(row)

    def update(self, payment_id, draft):
        values = fields(draft)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect(write=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute("""UPDATE manual_wechat_payments SET
                paid_on = ?, merchant = ?, amount_minor = ?, currency = ?,
                note = ?, updated_at = ? WHERE id = ?""",
                (*values, now, payment_id)).rowcount
            row = conn.execute("""SELECT id, paid_on, merchant, amount_minor,
                currency, note, created_at, updated_at FROM manual_wechat_payments
                WHERE id = ?""", (payment_id,)).fetchone() if changed else None
            conn.commit()
            return self._row(row)

    def delete(self, payment_id):
        with closing(self._connect(write=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute("DELETE FROM manual_wechat_payments WHERE id = ?",
                                   (payment_id,)).rowcount
            conn.commit()
            return bool(changed)
