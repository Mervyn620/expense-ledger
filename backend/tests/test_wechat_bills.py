import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app
from app.repository import LedgerRepository
from test_expense_app import create_test_ledger


DETAIL = """Fantuan Delivery
-48.88
标价
10.40加拿大元
当前状态
支付成功
支付时间
2026年11月16日 00:59:05
交易单号
00000000000000123
商户单号
WA00000000000000987"""


class WechatPreviewTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.db = create_test_ledger(root)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO cards(card_id,display_name) VALUES('wechat_pay','WeChat Pay')")
            order = conn.execute("""INSERT INTO orders(platform,order_id,date,total,currency,updated_at)
                 VALUES('fantuan','CA001234','2026-11-15',10.40,'CAD','now')""").lastrowid
            conn.execute("""INSERT INTO order_payments(order_pk,payment_index,card_id,amount,currency,status,updated_at)
                 VALUES(?,1,'wechat_pay',10.40,'CAD','confirmed','now')""", (order,))
        self.original = self.db.read_bytes()
        repo = patch("app.main.repository", LedgerRepository(self.db, root / "exports/current"))
        repo.start()
        self.addCleanup(repo.stop)
        self.client = TestClient(app, base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)

    def preview(self, detail=DETAIL, **kwargs):
        return self.client.post("/api/wechat-bills/preview", json={"detail": detail, **kwargs})

    def test_currency_and_unrelated_identifiers(self):
        data = self.preview().json()
        self.assertEqual(data["bill"]["paid"], {"amount": "48.88", "currency": "CNY"})
        self.assertEqual(data["bill"]["listed"], {"amount": "10.40", "currency": "CAD"})
        self.assertEqual(data["match_status"], "unmatched")
        self.assertEqual(self.preview(fantuan_order_id="CA001234").json()["match_status"], "missing_reference")
        self.assertEqual(self.preview(fantuan_order_id="CA001234",
                         receipt_transaction_id="WA00000000000000987").json()["match_status"], "conflict")

    def test_exact_reference_and_conflicts(self):
        for key, value in (("receipt_merchant_order_id", "WA00000000000000987"),
                           ("receipt_transaction_id", "00000000000000123")):
            kwargs = {"fantuan_order_id": "CA001234", key: value}
            self.assertEqual(self.preview(**kwargs).json()["match_status"], "consistent")
            for changed in (DETAIL.replace("10.40加拿大元", "10.40人民币"),
                            DETAIL.replace("11月16日", "9月16日"), DETAIL.replace("10.40", "20.80")):
                self.assertEqual(self.preview(changed, **kwargs).json()["match_status"], "review")
        self.assertEqual(self.db.read_bytes(), self.original)

    def test_unsupported_or_multiple_bills(self):
        for detail in (DETAIL.replace("支付成功", "已退款"), DETAIL + "\n" + DETAIL,
                       DETAIL.replace("00000000000000123", ""), DETAIL.replace("加拿大元", "美元")):
            self.assertEqual(self.preview(detail).status_code, 422)
