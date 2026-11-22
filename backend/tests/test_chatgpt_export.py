"""The browser handoff only prepares local files; sending stays with the user."""

import sqlite3
import json
import hashlib
import os
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.analysis import AnalysisDataBuilder, AnalysisError
from app.chatgpt_export import clean_temporary_exports, prepare_export
from app.main import app
from app.repository import LedgerRepository
from test_expense_analysis import create_analysis_ledger


class ChatGptExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = create_analysis_ledger(self.root)
        self.repository = LedgerRepository(self.db, self.root / "exports" / "current")

    def tearDown(self):
        self.temp.cleanup()

    def facts(self, result):
        return json.loads(result["content"].split("```json\n", 1)[1].split("\n```", 1)[0])

    def test_complete_purchase_frequency_is_not_limited_to_top_ten_or_detail_cap(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT INTO orders (platform, order_id, date, year, month, merchant, total, updated_at) VALUES ('amazon_ca', 'second', '2026-06-21', 2026, '2026-06', 'Amazon', 20, 'now')")
            second = conn.execute("SELECT id FROM orders WHERE order_id='second'").fetchone()[0]
            conn.executemany("INSERT INTO order_items (order_pk,line_key,product_name,quantity,price,currency,updated_at) VALUES (?,?,?,?,?,?, 'now')", [
                (1, 'milk-again', ' MILK  2% ', '2', 6, 'CAD'),
                (second, 'milk', 'Milk 2%', '', None, 'CAD'),
                (second, 'milk-cny', 'Milk 2%', '1', 30, 'CNY'),
                *[(second, f'extra-{i}', f'Other product {i}', '1', 1, 'CAD') for i in range(12)],
                (second, 'placeholder', 'Bank-recorded purchase', '1', 900, 'CAD'),
            ])
            conn.commit()
        original_init = AnalysisDataBuilder.__init__
        def limited_init(builder, *args, **kwargs):
            original_init(builder, *args, **kwargs)
            builder.max_orders = 1
        with patch.object(AnalysisDataBuilder, "__init__", limited_init):
            result = prepare_export(self.repository, "month", export_dir=self.root / "gpt", today=date(2026, 6, 25))
        facts = self.facts(result)
        self.assertTrue(facts["scope"]["truncated"])
        items = facts["computed_facts"]["all_items"]
        self.assertEqual(len(items), 14)
        milk = next(row for row in items if row["currency"] == 'CAD' and 'milk' in row["name"].lower())
        self.assertEqual(milk["order_count"], 2)  # Two lines in the first order still count once.
        self.assertEqual(milk["known_spend"], 16)  # Already a line amount; must not multiply by quantity.
        self.assertEqual(milk["total_quantity"], 3)
        self.assertIsNone(milk["total_spend"])
        self.assertEqual(milk["unknown_price_rows"], 1)
        self.assertEqual(next(row for row in items if row["currency"] == 'CNY')["total_spend"], 30)
        table = result["content"].split('## 买了什么与购买次数')[1].split('## 币种与记录来源')[0]
        self.assertIn('Other product 11', table)
        self.assertNotIn('Bank-recorded purchase', table)
        self.assertIn('已知 16.00；缺 1 行', table)
        self.assertNotIn('all_items', AnalysisDataBuilder(self.repository).build('month')['computed_facts'])

    def test_distributions_include_split_payments_unknowns_and_separate_manual_currencies(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE order_payments SET amount=8, payment_channel='online'")
            conn.execute("INSERT INTO order_payments (order_pk,payment_index,payment_method,amount,currency,status,payment_channel,updated_at) VALUES (1,2,'Gift card',5.3,'CAD','inferred','online','now')")
            conn.execute("INSERT INTO orders (platform,order_id,date,year,month,merchant,total,updated_at) VALUES ('amazon_ca','unpaid-evidence','2026-06-23',2026,'2026-06','Amazon',7,'now')")
            conn.executemany("INSERT INTO manual_wechat_payments (request_id,paid_on,merchant,amount_minor,currency,created_at,updated_at) VALUES (?, '2026-06-22', '书店', ?, ?, 'now', 'now')",
                             [('a',1000,'CNY'),('b',2000,'CNY'),('c',500,'CAD')])
            conn.commit()
        for mode in ('month', 'year', 'trend'):
            result = prepare_export(self.repository, mode, export_dir=self.root / "gpt", today=date(2026, 6, 25))
            facts = self.facts(result)
            payments = facts['export_summaries']['payment_totals']
            self.assertEqual(next(row for row in payments if row['method'] == 'Gift card')['recorded_amount'], 5.3)
            unknown = next(row for row in payments if row['payment_records'] == 0)
            self.assertIsNone(unknown['recorded_amount'])
            self.assertEqual(unknown['orders'], 1)
            self.assertEqual(sum(row['recorded_amount'] or 0 for row in payments), 13.3)
            self.assertEqual(facts['export_summaries']['currency_totals'][0]['total'], 20.3)
            self.assertEqual(facts['manual_wechat_payments']['totals']['CNY']['amount_minor'], 3000)
            self.assertEqual(facts['manual_wechat_payments']['by_merchant'][0]['count'], 2)
            self.assertIn('| 手填微信付款 | CNY | 2 | 30.00 |', result['content'])
            self.assertIn('## 购物平台分布', result['content'])
            self.assertIn('## 付款方式与渠道分布', result['content'])

    def test_manual_only_current_month_does_not_invent_products_or_use_previous_month(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT INTO manual_wechat_payments (request_id,paid_on,merchant,amount_minor,currency,created_at,updated_at) VALUES ('only','2026-07-01','午饭',2000,'CNY','now','now')")
            conn.commit()
        result = prepare_export(self.repository, 'month', export_dir=self.root / 'gpt', today=date(2026, 7, 2))
        facts = self.facts(result)
        self.assertEqual(result['meta']['period'], '2026-07')
        self.assertEqual(facts['computed_facts']['all_items'], [])
        self.assertEqual(facts['manual_wechat_payments']['totals']['CNY']['amount_minor'], 2000)
        self.assertNotIn('Milk 2%', result['content'])

    def test_overview_refreshes_product_and_payment_summaries_in_place(self):
        first = prepare_export(self.repository, 'trend', export_dir=self.root / 'gpt')
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE order_items SET product_name='Updated product'")
            conn.execute("UPDATE order_payments SET payment_channel='in_store'")
            conn.commit()
        second = prepare_export(self.repository, 'trend', export_dir=self.root / 'gpt')
        self.assertEqual(first['file_path'], second['file_path'])
        self.assertNotEqual(first['file_sha256'], second['file_sha256'])
        self.assertIn('Updated product', second['content'])
        self.assertIn('| 店内 |', second['content'])
        self.assertEqual(len(list((self.root / 'gpt').glob('*.md'))), 1)

    def test_year_and_trend_include_manual_only_newer_period(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT INTO manual_wechat_payments (request_id,paid_on,merchant,amount_minor,currency,created_at,updated_at) VALUES ('new-year','2027-01-01','书店',2000,'CNY','now','now')")
            conn.commit()
        result = prepare_export(self.repository, 'year', export_dir=self.root / 'gpt', today=date(2027, 1, 2))
        self.assertEqual(result['meta']['period'], '2027')
        self.assertEqual(result['meta']['orders'], 0)
        self.assertIn('| 手填微信付款 | CNY | 1 | 20.00 |', result['content'])
        trend = prepare_export(self.repository, 'trend', export_dir=self.root / 'gpt', today=date(2027, 1, 2))
        self.assertEqual(trend['meta']['period'], '2026-06 至 2027-01')

    def test_export_reuses_analysis_facts_and_separates_manual_currencies(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executemany(
                """INSERT INTO manual_wechat_payments
                (request_id, paid_on, merchant, amount_minor, currency, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, '', 'now', 'now')""",
                [("cny", "2026-06-10", "书店", 8800, "CNY"),
                 ("cad", "2026-06-21", "咖啡", 1230, "CAD"),
                 ("old", "2026-05-01", "旧月份", 999, "CNY")],
            )
            conn.commit()
        result = prepare_export(self.repository, "month", "六月花销在哪里？",
                                export_dir=self.root / "gpt", today=date(2026, 6, 25))
        content = Path(result["file_path"]).read_text(encoding="utf-8")
        self.assertEqual(content, result["content"])
        self.assertEqual(hashlib.sha256(Path(result["file_path"]).read_bytes()).hexdigest(), result["file_sha256"])
        self.assertEqual(result["meta"]["total_cad"], 13.3)
        self.assertEqual(result["meta"]["period"], "2026-06")
        self.assertFalse(result["persistent"])
        self.assertEqual(result["meta"]["manual_by_currency"]["CNY"]["amount_minor"], 8800)
        self.assertEqual(result["meta"]["manual_by_currency"]["CAD"]["amount_minor"], 1230)
        self.assertIn("CNY 与 CAD 不能相加或换算", content)
        self.assertIn("CAD 和 CNY 不换算、不合计", result["message"])
        self.assertNotIn("旧月份", content)
        self.assertNotIn("private-order-id", content)
        self.assertNotIn("example.invalid", content)
        self.assertNotIn("private note", content)

    def test_current_month_does_not_fall_back_to_previous_month(self):
        with self.assertRaisesRegex(AnalysisError, "本月尚无账本记录"):
            prepare_export(self.repository, "month", export_dir=self.root / "gpt",
                           today=date(2026, 7, 2))
        self.assertFalse((self.root / "gpt" / "temporary").exists())

    def test_overview_is_one_persistent_file_and_refreshes(self):
        first = prepare_export(self.repository, "trend", export_dir=self.root / "gpt",
                               today=date(2026, 6, 25))
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE orders SET total = 15.3 WHERE order_id = 'private-order-id'")
            conn.commit()
        second = prepare_export(self.repository, "trend", export_dir=self.root / "gpt",
                                today=date(2026, 6, 25))
        self.assertEqual(first["file_path"], second["file_path"])
        self.assertTrue(second["persistent"])
        self.assertNotEqual(first["file_sha256"], second["file_sha256"])
        self.assertEqual(len(list((self.root / "gpt").glob("*.md"))), 1)

    def test_expiry_cleans_only_owned_temporary_files(self):
        first = prepare_export(self.repository, "month", export_dir=self.root / "gpt",
                               today=date(2026, 6, 25))
        owned = Path(first["file_path"])
        other = owned.parent / "my-notes.md"
        other.write_text("keep", encoding="utf-8")
        old = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
        os.utime(owned, (old, old))
        os.utime(other, (old, old))
        removed = clean_temporary_exports(self.repository, export_dir=self.root / "gpt")
        self.assertEqual(removed, 1)
        self.assertFalse(owned.exists())
        self.assertTrue(other.exists())

    def test_custom_question_resolves_year_without_remote_api(self):
        result = prepare_export(self.repository, "question", "2026 年的年度花销如何？",
                                export_dir=self.root / "gpt", today=date(2026, 11, 16))
        self.assertEqual(result["meta"]["period"], "2026")
        self.assertEqual(result["question"], "2026 年的年度花销如何？")
        with self.assertRaisesRegex(ValueError, "请先填写"):
            prepare_export(self.repository, "question", "", export_dir=self.root / "gpt")

    def test_local_endpoint_prepares_reviewable_file(self):
        with patch("app.main.repository", self.repository), TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post("/api/analysis/chatgpt/prepare", json={"mode": "trend"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["meta"]["orders"], 1)
        self.assertTrue(Path(body["file_path"]).is_file())
        self.assertIn("总体趋势", body["content"])
        self.assertEqual(body["question"], "请分析整个账本的总体趋势、月度变化和数据限制。")


if __name__ == "__main__":
    unittest.main()
