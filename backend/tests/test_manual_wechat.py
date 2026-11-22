"""Manual WeChat entries stay exact, editable and outside imported CAD totals."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.manual_wechat import ManualWechatStore
from app.repository import LedgerRepository
from scripts.ledger.schema import create_schema


class ManualWechatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "expenses.sqlite"
        with closing(sqlite3.connect(self.db)) as conn:
            create_schema(conn)
            conn.commit()
        self.store = ManualWechatStore(self.db)
        self.repository = LedgerRepository(self.db, root / "exports")
        self.patches = [patch("app.main.manual_wechat", self.store),
                        patch("app.main.repository", self.repository)]
        for item in self.patches:
            item.start()
        self.client = TestClient(app, base_url="http://127.0.0.1")

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    @staticmethod
    def draft(amount="88.25", currency="CNY", paid_on="2026-10-28"):
        return {"request_id": str(uuid4()), "paid_on": paid_on,
                "merchant": "  微信 商户  ", "amount": amount,
                "currency": currency, "note": "  手填  "}

    def test_currencies_are_separate_and_not_in_cad_order_total(self):
        cny = self.draft()
        cad = self.draft("12.30", "CAD", "2026-09-15")
        created = self.client.post("/api/manual-wechat-payments", json=cny)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["amount_minor"], 8825)
        self.assertEqual(created.json()["merchant"], "微信 商户")
        self.assertEqual(self.client.post("/api/manual-wechat-payments", json=cad).status_code, 201)
        all_rows = self.client.get("/api/manual-wechat-payments?month=all").json()
        self.assertEqual(all_rows["total_count"], 2)
        self.assertEqual(all_rows["by_currency"]["CNY"], {"count": 1, "amount_minor": 8825})
        self.assertEqual(all_rows["by_currency"]["CAD"], {"count": 1, "amount_minor": 1230})
        self.assertEqual(self.client.get("/api/manual-wechat-payments?month=2026-10").json()["total_count"], 1)
        self.assertEqual(self.repository.overview("all")["summary"]["total"], 0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 9)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_retry_update_and_delete_are_safe(self):
        draft = self.draft()
        first = self.client.post("/api/manual-wechat-payments", json=draft).json()
        retry = self.client.post("/api/manual-wechat-payments", json=draft).json()
        self.assertEqual(first["id"], retry["id"])
        self.assertEqual(self.client.post("/api/manual-wechat-payments",
                          json=draft | {"amount": "99.99"}).status_code, 409)
        edited = {key: value for key, value in draft.items() if key != "request_id"}
        edited.update(amount="70.01", currency="CAD", merchant="午餐")
        updated = self.client.put(f"/api/manual-wechat-payments/{first['id']}", json=edited)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["amount_minor"], 7001)
        self.assertEqual(self.client.get("/api/manual-wechat-payments").json()["by_currency"]["CAD"]["amount_minor"], 7001)
        self.assertEqual(self.client.delete(f"/api/manual-wechat-payments/{first['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/manual-wechat-payments/{first['id']}").status_code, 404)
        self.assertEqual(self.client.get("/api/manual-wechat-payments").json()["total_count"], 0)

    def test_rejects_ambiguous_money_or_currency(self):
        for change, status in (({"amount": "1.001"}, 400),
                               ({"amount": "0"}, 422),
                               ({"currency": "USD"}, 422),
                               ({"merchant": "   "}, 400),
                               ({"paid_on": "2026-02-30"}, 422)):
            with self.subTest(change=change):
                response = self.client.post("/api/manual-wechat-payments", json=self.draft() | change)
                self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.client.get("/api/manual-wechat-payments?month=2026-13").status_code, 400)
        self.assertEqual(self.client.get("/api/manual-wechat-payments").json()["total_count"], 0)


if __name__ == "__main__":
    unittest.main()
