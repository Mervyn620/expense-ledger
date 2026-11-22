import csv
import json
import re
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.ledger.schema import create_schema
from app.data_status import data_status
from app.ledger_review import review_queue


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "expenses.sqlite"
CURRENT_EXPORTS = ROOT / "exports" / "current"
MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
CASH_WITHDRAWAL_FILTER = """
bt.direction = 'debit'
AND LOWER(bt.description) LIKE '%withdrawal%'
AND LOWER(bt.description) NOT LIKE '%interac e-transfer%'
"""


def _previous_month(month):
    year, month_number = (int(part) for part in month.split("-"))
    if month_number == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month_number - 1:02d}"


def _read_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return default


class CashNoteSupersededError(Exception):
    """A newer cash-note edit arrived before this request could write."""


class LedgerRepository:
    def __init__(
        self,
        db_path=DB_PATH,
        current_exports=CURRENT_EXPORTS,
        reconciliation_csv=None,
    ):
        self._cash_note_lock = threading.Lock()
        self._cash_note_revisions = {}
        self.db_path = Path(db_path)
        self.current_exports = Path(current_exports)
        self.reconciliation_csv = (
            Path(reconciliation_csv)
            if reconciliation_csv is not None
            else self.current_exports.parent / "reconciliation" / "scotia.csv"
        )

    def connect(self):
        if not self.db_path.exists():
            raise FileNotFoundError(f"Ledger database not found: {self.db_path}")
        conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def ensure_schema(self):
        if not self.db_path.exists():
            return
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA foreign_keys = ON")
            create_schema(conn)
            conn.commit()

    @staticmethod
    def normalize_month(month):
        if not month or month == "latest":
            return None
        if month == "all":
            return "all"
        if not MONTH_PATTERN.fullmatch(month):
            raise ValueError("Month must be YYYY-MM or all.")
        return month

    def months(self, conn=None):
        owns_connection = conn is None
        conn = conn or self.connect()
        try:
            return [
                row["month"]
                for row in conn.execute(
                    "SELECT DISTINCT month FROM orders WHERE month <> '' ORDER BY month DESC"
                )
            ]
        finally:
            if owns_connection:
                conn.close()

    def resolve_month(self, month, conn):
        normalized = self.normalize_month(month)
        if normalized is not None:
            return normalized
        months = self.months(conn)
        return months[0] if months else date.today().strftime("%Y-%m")

    @staticmethod
    def month_clause(month, alias="o"):
        currency = f"WHERE {alias}.currency='CAD'"
        return (currency, []) if month == "all" else (currency + f" AND {alias}.month = ?", [month])

    @staticmethod
    def _group_totals(conn, month, column, label):
        allowed = {
            "platform": "o.platform",
            "category": "o.category",
            "card_label": "COALESCE(c.display_name, 'Needs Review')",
        }
        expression = allowed[column]
        clause, params = LedgerRepository.month_clause(month)
        if column == "category":
            rows = conn.execute(f"""
                SELECT a.category AS key, COUNT(*) AS orders,SUM(a.cents)/100.0 AS total
                FROM order_category_amounts a JOIN expense_orders o ON o.id=a.order_pk
                {clause} GROUP BY a.category ORDER BY total DESC,key
            """, params)
            return [{**dict(row), "label": label(row["key"])} for row in rows]
        rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF({expression}, ''), 'Uncategorized') AS key,
                   COUNT(*) AS orders,
                   ROUND(SUM(o.total), 2) AS total
            FROM expense_orders o
            LEFT JOIN order_payments p
              ON p.order_pk = o.id AND p.payment_index = 1
            LEFT JOIN cards c ON c.card_id = p.card_id
            {clause}
            GROUP BY key
            ORDER BY total DESC, key
            """,
            params,
        ).fetchall()
        return [
            {
                "key": row["key"],
                "label": label(row["key"]),
                "orders": row["orders"],
                "total": row["total"],
            }
            for row in rows
        ]

    @staticmethod
    def _trend_series(conn, trend, platform_labels):
        grouped = {"platform": {}, "payment": {}}
        # A primary-payment join attributes each net order once, consistently
        # with the existing overview; it is not a split-payment cash-flow chart.
        for row in conn.execute("""
            SELECT 'platform' AS dimension, o.platform AS series_id,
                   o.platform AS label, o.month, ROUND(SUM(o.total), 2) AS total
            FROM expense_orders o WHERE o.month <> '' AND o.currency='CAD'
            GROUP BY o.platform, o.month
            UNION ALL
            SELECT 'payment', COALESCE(c.card_id, '__unknown__'),
                   COALESCE(c.display_name, '待确认'), o.month,
                   ROUND(SUM(o.total), 2)
            FROM expense_orders o
            LEFT JOIN order_payments p ON p.order_pk = o.id AND p.payment_index = 1
            LEFT JOIN cards c ON c.card_id = p.card_id
            WHERE o.month <> '' AND o.currency='CAD'
            GROUP BY COALESCE(c.card_id, '__unknown__'), o.month
        """):
            dimension = row["dimension"]
            key = row["series_id"]
            item = grouped[dimension].setdefault(key, {
                "id": key,
                "label": platform_labels.get(key, row["label"]) if dimension == "platform" else row["label"],
                "months": {},
            })
            item["months"][row["month"]] = row["total"]
        result = {}
        for dimension, items in grouped.items():
            result[dimension] = sorted([
                {
                    "id": item["id"], "label": item["label"],
                    "total": round(sum(item["months"].values()), 2),
                    "values": [item["months"].get(row["month"], 0) for row in trend],
                }
                for item in items.values()
            ], key=lambda item: (-item["total"], item["id"]))
        return result

    def overview(self, month=None):
        with closing(self.connect()) as conn:
            selected_month = self.resolve_month(month, conn)
            clause, params = self.month_clause(selected_month)
            summary = dict(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS order_count,
                           ROUND(COALESCE(SUM(o.total), 0), 2) AS total,
                           ROUND(COALESCE(AVG(o.total), 0), 2) AS average_order,
                           MAX(o.updated_at) AS updated_at
                    FROM expense_orders o
                    {clause}
                    """,
                    params,
                ).fetchone()
            )
            item_clause, item_params = self.month_clause(selected_month)
            summary["item_count"] = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM order_items i
                JOIN expense_orders o ON o.id = i.order_pk
                {item_clause}
                """,
                item_params,
            ).fetchone()[0]

            previous_total = None
            change_percent = None
            if selected_month != "all":
                previous_total = conn.execute(
                    "SELECT ROUND(COALESCE(SUM(total), 0), 2) FROM expense_orders WHERE month = ? AND currency='CAD'",
                    (_previous_month(selected_month),),
                ).fetchone()[0]
                if previous_total:
                    change_percent = round(
                        ((summary["total"] - previous_total) / previous_total) * 100,
                        1,
                    )

            trend = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT month, COUNT(*) AS orders, ROUND(SUM(total), 2) AS total
                    FROM expense_orders
                    WHERE month <> '' AND currency='CAD'
                    GROUP BY month
                    ORDER BY month
                    """
                )
            ]
            # Keep the same continuous calendar for every series, including months
            # with no recorded purchases. Zero does not imply collection coverage.
            if trend:
                recorded = {row["month"]: row for row in trend}
                year, number = map(int, trend[0]["month"].split("-"))
                final_month = trend[-1]["month"]
                trend = []
                while (key := f"{year:04d}-{number:02d}") <= final_month:
                    trend.append(recorded.get(key, {"month": key, "orders": 0, "total": 0}))
                    year, number = (year + 1, 1) if number == 12 else (year, number + 1)
            review_clause, review_params = self.month_clause(selected_month)
            review_count = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM expense_orders o
                LEFT JOIN order_payments p
                  ON p.order_pk = o.id AND p.payment_index = 1
                {review_clause}
                {"AND" if review_clause else "WHERE"} COALESCE(p.status, '') IN ('', 'needs_review')
                """,
                review_params,
            ).fetchone()[0]

            platform_labels = {
                "walmart_ca": "Walmart",
                "amazon_ca": "Amazon",
                "the_ordinary_ca": "The Ordinary",
                "uber_eats": "Uber Eats",
                "fantuan": "饭团",
                "tnt_ca": "大统华",
                "scotiabank": "Scotiabank",
                "bank": "其他银行",
            }
            category_labels = {
                "personal_care": "个护美容",
                "food_delivery": "外卖",
                "mixed": "综合购物",
                "dining": "餐饮",
                "telecom": "通讯",
                "groceries": "食品杂货",
                "coffee_supplies": "咖啡",
                "general_retail": "日常零售",
                "software_subscription": "软件订阅",
                "bank_fees": "银行费用",
                "grocery_delivery": "杂货配送",
                "digital_entertainment": "数字娱乐",
                "delivery_membership": "外卖会员",
                "retail_membership": "购物会员",
                "digital_services": "数字服务",
                "transportation": "出行",
                "uncategorized": "未分类",
                "unallocated": "未分配差额",
                "kitchenware": "餐厨用品",
                "household": "家居日用",
            }

            platforms = self._group_totals(
                conn,
                selected_month,
                "platform",
                lambda value: platform_labels.get(value, value),
            )
            existing_platforms = {row["key"] for row in platforms}
            platforms.extend(
                {
                    "key": key,
                    "label": label,
                    "orders": 0,
                    "total": 0,
                }
                for key, label in platform_labels.items()
                if key not in existing_platforms
            )

            return {
                "selected_month": selected_month,
                "calendar_month_ended": (selected_month < date.today().strftime("%Y-%m") if selected_month != "all" else None),
                "currency_totals": [dict(row) for row in conn.execute("""
                    SELECT currency, COUNT(*) AS orders,ROUND(SUM(total),2) AS total FROM expense_orders
                    WHERE (?='all' OR month=?) GROUP BY currency ORDER BY currency
                """, (selected_month, selected_month))],
                "data_status": data_status(conn, self.db_path.parent.parent),
                "months": self.months(conn),
                "summary": {
                    **summary,
                    "previous_total": previous_total,
                    "change_percent": change_percent,
                    "review_count": review_count,
                },
                "trend": trend,
                "trend_series": self._trend_series(conn, trend, platform_labels),
                "platforms": platforms,
                "categories": self._group_totals(
                    conn,
                    selected_month,
                    "category",
                    lambda value: category_labels.get(value, value.replace("_", " ").title()),
                ),
                "cards": self._group_totals(
                    conn,
                    selected_month,
                    "card_label",
                    lambda value: value,
                ),
                "recent_orders": self._orders_query(
                    conn,
                    month=selected_month,
                    limit=8,
                    offset=0,
                )["rows"],
                "reconciliation_review": self.reconciliation_review(),
                "run_state": _read_json(
                    self.current_exports / "run-state.json",
                    {"status": "unknown"},
                ),
                "weekly_status": _read_json(
                    self.current_exports / "weekly-status.json",
                    {},
                ),
                "validation_status": _read_json(
                    self.current_exports / "validation-status.json",
                    {},
                ),
                "stability_status": _read_json(
                    self.current_exports / "stability-status.json",
                    {},
                ),
            }

    def reconciliation_review(self, limit=None):
        with closing(self.connect()) as conn:
            if conn.execute("SELECT 1 FROM bank_reconciliations LIMIT 1").fetchone():
                return review_queue(conn)[:limit]
        # Older imports can have a report without any structured bank records.
        if not self.reconciliation_csv.exists():
            return []
        try:
            with self.reconciliation_csv.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                rows = []
                for row in csv.DictReader(handle):
                    if row.get("match_status") != "unmatched_platform_review":
                        continue
                    try:
                        amount = round(float(row.get("amount") or 0), 2)
                    except (TypeError, ValueError):
                        amount = 0.0
                    rows.append(
                        {
                            "date": row.get("date", ""),
                            "description": row.get("description", ""),
                            "amount": amount,
                            "merchant": row.get("merchant", ""),
                            "category": row.get("category", ""),
                            "detected_platform": row.get("detected_platform", ""),
                            "match_method": row.get("match_method", ""),
                            "notes": row.get("notes", ""),
                        }
                    )
                    if limit is not None and len(rows) >= max(1, int(limit)):
                        break
                return rows
        except (OSError, csv.Error):
            return []

    def cash_withdrawals(self, month="all", limit=200):
        limit = max(1, min(int(limit), 500))
        with closing(self.connect()) as conn:
            selected_month = self.resolve_month(month, conn)
            clauses = [CASH_WITHDRAWAL_FILTER]
            params = []
            if selected_month != "all":
                clauses.append("SUBSTR(bt.date, 1, 7) = ?")
                params.append(selected_month)
            where = " AND ".join(f"({clause.strip()})" for clause in clauses)
            summary = dict(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS withdrawal_count,
                           ROUND(COALESCE(SUM(CASE WHEN bt.currency='CAD' THEN bt.amount END), 0), 2) AS total,
                           SUM(CASE WHEN COALESCE(cwn.note, '') <> '' THEN 1 ELSE 0 END)
                             AS noted_count
                    FROM bank_transactions bt
                    LEFT JOIN cash_withdrawal_notes cwn
                      ON cwn.bank_transaction_pk = bt.id
                    WHERE {where}
                    """,
                    params,
                ).fetchone()
            )
            summary["noted_count"] = int(summary.get("noted_count") or 0)
            summary["currency_totals"] = dict(conn.execute(
                f"SELECT bt.currency,ROUND(SUM(bt.amount),2) FROM bank_transactions bt WHERE {where} GROUP BY bt.currency",
                params))
            rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT bt.id AS bank_transaction_pk,
                           bt.date,
                           bt.description,
                           bt.amount,
                           bt.currency,
                           COALESCE(ba.display_name, 'Scotiabank') AS account_label,
                           COALESCE(cwn.note, '') AS note,
                           cwn.updated_at AS note_updated_at
                    FROM bank_transactions bt
                    LEFT JOIN bank_accounts ba
                      ON ba.account_id = bt.account_id
                    LEFT JOIN cash_withdrawal_notes cwn
                      ON cwn.bank_transaction_pk = bt.id
                    WHERE {where}
                    ORDER BY bt.date DESC, bt.id DESC
                    LIMIT ?
                    """,
                    [*params, limit],
                )
            ]
            return {
                "selected_month": selected_month,
                "summary": summary,
                "rows": rows,
            }

    def update_cash_withdrawal_note(self, bank_transaction_pk, note):
        bank_transaction_pk = int(bank_transaction_pk)
        # Register before waiting for SQLite: a timed-out older request must not
        # overwrite a later edit when it eventually obtains the database lock.
        with self._cash_note_lock:
            revision = self._cash_note_revisions.get(bank_transaction_pk, 0) + 1
            self._cash_note_revisions[bank_transaction_pk] = revision
        normalized = re.sub(r"\s+", " ", str(note or "")).strip()
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA foreign_keys = ON")
            create_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            # Hold this short lock through commit. New edits either supersede
            # this request before the check or register after its commit.
            with self._cash_note_lock:
                if self._cash_note_revisions[bank_transaction_pk] != revision:
                    raise CashNoteSupersededError()
                exists = conn.execute(
                    f"""
                    SELECT 1
                    FROM bank_transactions bt
                    WHERE bt.id = ?
                      AND ({CASH_WITHDRAWAL_FILTER.strip()})
                    """,
                    (bank_transaction_pk,),
                ).fetchone()
                if not exists:
                    return None
                if normalized:
                    timestamp = datetime.now(timezone.utc).replace(
                        microsecond=0
                    ).isoformat()
                    conn.execute(
                        """
                        INSERT INTO cash_withdrawal_notes (
                          bank_transaction_pk, note, updated_at
                        )
                        VALUES (?, ?, ?)
                        ON CONFLICT(bank_transaction_pk) DO UPDATE SET
                          note=excluded.note,
                          updated_at=excluded.updated_at
                        """,
                        (bank_transaction_pk, normalized, timestamp),
                    )
                else:
                    conn.execute(
                        "DELETE FROM cash_withdrawal_notes WHERE bank_transaction_pk = ?",
                        (bank_transaction_pk,),
                    )
                conn.commit()
        return {
            "bank_transaction_pk": bank_transaction_pk,
            "note": normalized,
        }

    def filters(self):
        with closing(self.connect()) as conn:
            return {
                "months": self.months(conn),
                "cash_months": [
                    row[0] for row in conn.execute(
                        f"SELECT DISTINCT SUBSTR(bt.date, 1, 7) AS month "
                        f"FROM bank_transactions bt WHERE {CASH_WITHDRAWAL_FILTER} "
                        "AND LENGTH(bt.date) >= 7 ORDER BY month DESC"
                    )
                ],
                "platforms": [
                    row[0]
                    for row in conn.execute(
                        "SELECT DISTINCT platform FROM orders ORDER BY platform"
                    )
                ],
                "categories": [
                    row[0]
                    for row in conn.execute(
                        "SELECT category FROM orders UNION SELECT category FROM order_category_amounts ORDER BY category"
                    )
                ],
                "cards": [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT card_id, display_name
                        FROM cards
                        WHERE card_id IN (
                          SELECT DISTINCT card_id FROM order_payments
                        )
                        ORDER BY display_name
                        """
                    )
                ],
            }

    def _orders_query(
        self,
        conn,
        month="all",
        platform="",
        category="",
        card_id="",
        query="",
        limit=50,
        offset=0,
    ):
        clauses = []
        params = []
        if month and month != "all":
            clauses.append("o.month = ?")
            params.append(month)
        if platform:
            clauses.append("o.platform = ?")
            params.append(platform)
        if category:
            clauses.append("(o.category=? OR EXISTS (SELECT 1 FROM order_category_amounts a WHERE a.order_pk=o.id AND a.category=?))")
            params.extend([category,category])
        if card_id:
            clauses.append("p.card_id = ?")
            params.append(card_id)
        if query:
            clauses.append(
                "(LOWER(o.merchant) LIKE ? OR LOWER(o.item_summary) LIKE ? "
                "OR LOWER(o.order_id) LIKE ? OR EXISTS ("
                "SELECT 1 FROM order_items search_item "
                "WHERE search_item.order_pk = o.id "
                "AND LOWER(search_item.product_name) LIKE ?))"
            )
            token = f"%{query.lower()}%"
            params.extend([token, token, token, token])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total_count = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM expense_orders o
            LEFT JOIN order_payments p
              ON p.order_pk = o.id AND p.payment_index = 1
            {where}
            """,
            params,
        ).fetchone()[0]
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT o.id AS order_pk, o.date, o.month, o.platform, o.order_id,
                       o.merchant, o.fulfillment, o.category, o.item_summary,
                       o.total, o.currency, o.gross_total, o.refund_total,
                       COALESCE(c.display_name, 'Needs Review') AS card_label,
                       COALESCE(p.status, 'needs_review') AS payment_status,
                       COALESCE(p.payment_channel, 'unknown') AS payment_channel
                FROM expense_orders o
                LEFT JOIN order_payments p
                  ON p.order_pk = o.id AND p.payment_index = 1
                LEFT JOIN cards c ON c.card_id = p.card_id
                {where}
                ORDER BY o.date DESC, o.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            )
        ]
        return {"rows": rows, "total_count": total_count}

    def orders(
        self,
        month="all",
        platform="",
        category="",
        card_id="",
        query="",
        limit=50,
        offset=0,
    ):
        selected_month = self.normalize_month(month) or "all"
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with closing(self.connect()) as conn:
            return self._orders_query(
                conn,
                month=selected_month,
                platform=platform,
                category=category,
                card_id=card_id,
                query=query.strip(),
                limit=limit,
                offset=offset,
            )

    def order_detail(self, order_pk):
        with closing(self.connect()) as conn:
            order = conn.execute(
                """
                SELECT o.*, COALESCE(c.display_name, 'Needs Review') AS card_label,
                       p.payment_method, p.amount AS payment_amount,
                       p.status AS payment_status, p.source AS payment_source,
                       COALESCE(p.payment_channel, 'unknown') AS payment_channel
                FROM expense_orders o
                LEFT JOIN order_payments p
                  ON p.order_pk = o.id AND p.payment_index = 1
                LEFT JOIN cards c ON c.card_id = p.card_id
                WHERE o.id = ?
                """,
                (order_pk,),
            ).fetchone()
            if not order:
                return None

            items = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id AS item_pk, effective_category AS category, category_source,is_product, product_name, quantity, price,
                           currency, delivery_status, product_link
                    FROM classified_order_items
                    WHERE order_pk = ?
                    ORDER BY id
                    """,
                    (order_pk,),
                )
            ]
            evidence = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT source, payment_method, card_network, card_last4,
                           status, evidence_snippet, captured_at
                    FROM payment_evidence
                    WHERE order_pk = ?
                    ORDER BY id
                    """,
                    (order_pk,),
                )
            ]
            history = [dict(row) for row in conn.execute(
                'SELECT * FROM order_events WHERE platform=? AND order_id=? ORDER BY id DESC LIMIT 100',
                (order['platform'], order['order_id']))]
            for event in history:
                for key in ('before_json', 'after_json'):
                    event[key.replace('_json', '')] = json.loads(event.pop(key) or 'null')
            refunds = [dict(row) for row in conn.execute('''
                SELECT r.id, r.amount, r.note, bt.date, bt.description
                FROM order_refunds r JOIN bank_transactions bt ON bt.id=r.bank_transaction_pk
                WHERE r.order_pk=? ORDER BY bt.date DESC''', (order_pk,))]
            candidates = [dict(row) for row in conn.execute('''
                SELECT bt.id, bt.date, bt.description, ABS(bt.amount) AS amount
                FROM bank_transactions bt
                WHERE bt.direction='credit' AND bt.currency=? AND bt.date>=?
                  AND ABS(bt.amount)>0 AND ABS(bt.amount)<=?
                  AND NOT EXISTS(SELECT 1 FROM order_refunds r WHERE r.bank_transaction_pk=bt.id)
                  AND NOT EXISTS(SELECT 1 FROM bank_reconciliations b
                    WHERE b.bank_transaction_pk=bt.id AND b.order_pk IS NOT NULL)
                ORDER BY bt.date DESC LIMIT 100''',
                (order['currency'], order['date'], order['total']))]
            bank_matches = [dict(row) for row in conn.execute('''
                SELECT bt.transaction_id AS match_id,bt.date, bt.amount,bt.currency,bt.description,bt.direction, br.match_status, br.match_method
                FROM bank_reconciliations br JOIN bank_transactions bt ON bt.id=br.bank_transaction_pk
                WHERE br.order_pk=? ORDER BY bt.date''', (order_pk,))]
            allocations = [dict(row) for row in conn.execute(
                'SELECT category,cents FROM order_category_amounts WHERE order_pk=? ORDER BY cents DESC', (order_pk,))]
            evidence_state = ('bank_only' if order['platform'] in ('scotia','scotiabank','bank') else
                              'bank_matched' if bank_matches else 'platform_only')
            return {'order': {**dict(order), 'evidence_status': evidence_state,
                              'source_key': [order['platform'], order['order_id']]},
                    'allocations': allocations, 'items': items, 'evidence': evidence,
                    'history': history, 'refunds': refunds, 'refund_candidates': candidates,
                    'bank_matches': bank_matches}

    def link_refund(self, order_pk, bank_transaction_pk, note=''):
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            order = conn.execute('SELECT * FROM expense_orders WHERE id=?', (order_pk,)).fetchone()
            bank = conn.execute('SELECT * FROM bank_transactions WHERE id=?', (bank_transaction_pk,)).fetchone()
            if not order or not bank:
                raise ValueError('订单或银行记录不存在。')
            existing = conn.execute('SELECT order_pk FROM order_refunds WHERE bank_transaction_pk=?',
                                    (bank_transaction_pk,)).fetchone()
            if existing and existing['order_pk'] == order_pk:
                return self.order_detail(order_pk)
            if existing:
                raise ValueError('该银行入账已经关联另一笔退款。')
            if bank['direction'] != 'credit' or bank['currency'] != order['currency']:
                raise ValueError('只能关联同币种的银行入账。')
            amount = round(abs(bank['amount']), 2)
            if amount <= 0 or amount > round(order['total'], 2) or bank['date'] < order['date']:
                raise ValueError('退款金额或日期与订单不符。')
            if conn.execute('SELECT 1 FROM bank_reconciliations WHERE bank_transaction_pk=? AND order_pk IS NOT NULL',
                            (bank_transaction_pk,)).fetchone():
                raise ValueError('该银行记录已有其他对账关联。')
            conn.execute('INSERT INTO order_refunds(order_pk,bank_transaction_pk,amount,note) VALUES(?,?,?,?)',
                         (order_pk, bank_transaction_pk, amount, str(note)[:300]))
            conn.commit()
        return self.order_detail(order_pk)

    def unlink_refund(self, order_pk, refund_id):
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('DELETE FROM order_refunds WHERE id=? AND order_pk=?', (refund_id, order_pk))
            conn.commit()
        return self.order_detail(order_pk)

    def recent_changes(self, limit=50):
        with closing(self.connect()) as conn:
            rows = [dict(row) for row in conn.execute('''
                SELECT e.id,e.platform,e.order_id,e.event_type,e.observed_at,e.before_json,e.after_json,o.id AS order_pk
                FROM order_events e LEFT JOIN orders o ON o.platform=e.platform AND o.order_id=e.order_id
                WHERE e.event_type<>'baseline' ORDER BY e.id DESC LIMIT ?''', (max(1,min(limit,200)),))]
            for row in rows:
                row['before'] = json.loads(row.pop('before_json') or 'null')
                row['after'] = json.loads(row.pop('after_json') or 'null')
            return rows
