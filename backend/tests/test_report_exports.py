import importlib.util
import sqlite3
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DB_SCRIPT = ROOT / "scripts" / "ledger" / "db.py"
REPORT_SCRIPT = ROOT / "scripts" / "reporting" / "export_reports.py"

db_spec = importlib.util.spec_from_file_location("ledger_db_for_reports", DB_SCRIPT)
ledger_db = importlib.util.module_from_spec(db_spec)
db_spec.loader.exec_module(ledger_db)

report_spec = importlib.util.spec_from_file_location("export_reports", REPORT_SCRIPT)
reports = importlib.util.module_from_spec(report_spec)
report_spec.loader.exec_module(reports)


class ReportExportsTest(unittest.TestCase):
    def test_multiple_payment_evidence_rows_do_not_duplicate_order(self):
        conn = sqlite3.connect(":memory:")
        ledger_db.create_schema(conn)
        conn.execute(
            """
            INSERT INTO cards (card_id, display_name)
            VALUES ('unassigned', 'Unassigned')
            """
        )
        conn.execute(
            """
            INSERT INTO orders (
              platform, order_id, date, year, month, total, currency, updated_at
            )
            VALUES ('test', 'order-1', '2026-05-01', 2026, '2026-05', 12.34, 'CAD', 'now')
            """
        )
        order_pk = conn.execute("SELECT id FROM orders").fetchone()[0]
        conn.execute(
            """
            INSERT INTO order_payments (
              order_pk, payment_index, card_id, amount, currency, status, updated_at
            )
            VALUES (?, 1, 'unassigned', 12.34, 'CAD', 'needs_review', 'now')
            """,
            (order_pk,),
        )
        conn.executemany(
            """
            INSERT INTO payment_evidence (
              order_pk, source, status, updated_at
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (order_pk, "source-one", "method_only", "2026-05-01"),
                (order_pk, "source-two", "confirmed", "2026-05-02"),
            ],
        )

        rows = reports.ledger_rows(conn)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total"], "12.34")
        self.assertEqual(rows[0]["payment_status"], "confirmed")
        conn.close()

    def test_month_export_uses_separate_period_directory(self):
        current, summaries = reports.output_dirs(month="2026-05")
        self.assertEqual(current, ROOT / "exports" / "periods" / "2026-05" / "current")
        self.assertEqual(summaries, ROOT / "exports" / "periods" / "2026-05" / "summaries")

    def test_card_summary_omits_inactive_unassigned_card(self):
        cards = [
            {
                "card_id": "mc_1111",
                "display_name": "Mastercard 1111",
                "network": "Mastercard",
                "issuer": "",
                "last4": "1111",
                "kind": "credit_card",
            },
            {
                "card_id": "unassigned",
                "display_name": "Unassigned",
                "network": "",
                "issuer": "",
                "last4": "",
                "kind": "needs_review",
            },
        ]
        source_rows = [{
            "month": "2026-05",
            "card_id": "mc_1111",
            "card_label": "Mastercard 1111",
            "platform": "walmart_ca",
            "transactions": "1",
            "total": "10.00",
        }]
        with self.subTest("active cards only"):
            import tempfile
            with tempfile.TemporaryDirectory() as temp:
                output_dir = Path(temp)
                reports.write_card_summary(cards, source_rows, [], [], output_dir)
                text = (output_dir / "card-spending-summary.md").read_text(encoding="utf-8-sig")
                self.assertIn("Mastercard 1111", text)
                self.assertNotIn("Unassigned", text)

    def test_review_rows_keep_card_identity_for_card_summary(self):
        ledger = [{
            "date": "2026-07-16",
            "platform": "fantuan",
            "order_id": "order-review",
            "merchant": "Example",
            "category": "food_delivery",
            "card_id": "unassigned",
            "card_label": "Needs Review",
            "total": "28.70",
            "currency": "CAD",
            "payment_method": "Alipay",
            "payment_status": "needs_review",
            "assignment_status": "needs_review",
            "assignment_source": "payment_evidence",
            "notes": "method only",
        }]

        review = reports.review_rows(ledger)

        self.assertEqual(review[0]["card_id"], "unassigned")
        self.assertEqual(review[0]["card_label"], "Needs Review")


if __name__ == "__main__":
    unittest.main()
