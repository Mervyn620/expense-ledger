import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.repository import LedgerRepository
from tests.test_expense_app import create_test_ledger


class TrendSeriesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = create_test_ledger(root)
        self.repo = LedgerRepository(self.db, root / "exports/current")

    def assert_reconciles(self, payload):
        for dimension in ("platform", "payment"):
            for index, month in enumerate(payload["trend"]):
                self.assertAlmostEqual(
                    sum(item["values"][index] for item in payload["trend_series"][dimension]),
                    month["total"], places=2,
                )

    def test_history_is_independent_of_selected_month_and_fills_gaps(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE orders SET month='2026-08', date='2026-08-25' WHERE id=2")
            conn.commit()
        may = self.repo.overview("2026-05")
        august = self.repo.overview("2026-08")
        self.assertEqual(may["trend_series"], august["trend_series"])
        self.assertEqual([row["month"] for row in may["trend"]], ["2026-05", "2026-06", "2026-07", "2026-08"])
        self.assertEqual(may["trend_series"]["payment"][0]["values"], [11.3, 0, 0, 38.81])
        self.assert_reconciles(may)

    def test_same_name_cards_stay_distinct_and_secondary_payment_is_not_duplicated(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT INTO cards(card_id,display_name) VALUES('other', 'Mastercard 1111')")
            conn.execute("UPDATE order_payments SET card_id='other' WHERE order_pk=2")
            conn.execute("INSERT INTO order_payments(order_pk,payment_index,card_id,payment_method,amount,currency,status,updated_at) VALUES(2,2,'mc_1111','Mastercard',5,'CAD','confirmed','now')")
            conn.commit()
        payload = self.repo.overview("all")
        cards = payload["trend_series"]["payment"]
        self.assertEqual({card["id"] for card in cards}, {"mc_1111", "other"})
        self.assertEqual(len({card["label"] for card in cards}), 1)
        self.assert_reconciles(payload)

    def test_unknown_payment_and_negative_total_are_retained(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("DELETE FROM order_payments WHERE order_pk=1")
            conn.execute("UPDATE orders SET total=-5 WHERE id=1")
            conn.commit()
        payload = self.repo.overview("all")
        unknown = next(card for card in payload["trend_series"]["payment"] if card["id"] == "__unknown__")
        self.assertEqual(unknown["label"], "待确认")
        self.assertEqual(unknown["values"], [-5, 0])
        self.assert_reconciles(payload)

    def test_refund_uses_purchase_month_and_unmatched_bank_charge_is_excluded(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT INTO bank_accounts VALUES('test','Example','Example','0000','now')")
            bank = conn.execute("INSERT INTO bank_transactions(transaction_id,account_id,date,description,amount,direction,currency,updated_at) VALUES('refund','test','2026-07-02','refund',5,'credit','CAD','now')").lastrowid
            conn.execute("INSERT INTO bank_transactions(transaction_id,account_id,date,description,amount,direction,currency,updated_at) VALUES('unmatched','test','2026-07-02','platform debit',90,'debit','CAD','now')")
            conn.commit()
        self.repo.link_refund(2, bank)
        payload = self.repo.overview("all")
        self.assertEqual(payload["trend"][-1]["total"], 33.81)
        self.assertEqual(len(payload["trend"]), 2)
        self.assert_reconciles(payload)

    def test_empty_ledger(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("DELETE FROM orders")
            conn.commit()
        payload = self.repo.overview("all")
        self.assertEqual(payload["trend"], [])
        self.assertEqual(payload["trend_series"], {"platform": [], "payment": []})


if __name__ == "__main__":
    unittest.main()
