import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "importers" / "uber_receipt_payments.py"

spec = importlib.util.spec_from_file_location("import_uber_receipts_payment", SCRIPT)
receipt_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipt_import)


class UberReceiptPaymentImportTest(unittest.TestCase):
    def setUp(self):
        self.cards = {
            "mc_1111": {
                "display_name": "Mastercard 1111",
                "network": "Mastercard",
                "last4": "1111",
            },
            "scotia_visa_2222": {
                "display_name": "Scotiabank Visa 2222",
                "network": "Visa",
                "last4": "2222",
            },
            "unassigned": {
                "display_name": "Unassigned",
                "network": "",
                "last4": "",
            },
        }

    def test_card_for_receipt_matches_known_last4(self):
        self.assertEqual(
            receipt_import.card_for_receipt(
                {"card_network": "Mastercard", "card_last4": "1111"},
                self.cards,
            ),
            "mc_1111",
        )
        self.assertEqual(
            receipt_import.card_for_receipt(
                {"card_network": "Visa", "card_last4": "2222"},
                self.cards,
            ),
            "scotia_visa_2222",
        )

    def test_update_assignments_preserves_manual_non_uber_rows(self):
        transactions = [
            {
                "date": "2026-05-20",
                "platform": "uber_eats",
                "order_id": "receipt-1",
                "merchant": "Shop",
                "total": "16.95",
                "currency": "CAD",
            },
            {
                "date": "2026-05-21",
                "platform": "walmart_ca",
                "order_id": "wm-1",
                "merchant": "Walmart",
                "total": "20.00",
                "currency": "CAD",
            },
        ]
        assignments = [
            {
                "date": "2026-05-21",
                "platform": "walmart_ca",
                "order_id": "wm-1",
                "merchant": "Walmart",
                "total": "20.00",
                "currency": "CAD",
                "card_id": "scotia_visa_2222",
                "card_label": "Scotiabank Visa 2222",
                "assignment_status": "confirmed",
                "assignment_source": "manual",
                "notes": "bank statement",
            },
        ]
        receipts = [
            {
                "platform": "uber_eats",
                "order_id": "receipt-1",
                "ok": True,
                "card_network": "Mastercard",
                "card_last4": "1111",
            },
        ]

        rows, stats = receipt_import.update_assignments(
            transactions,
            assignments,
            receipts,
            self.cards,
            "unassigned",
        )
        by_key = {receipt_import.tx_key(row): row for row in rows}

        self.assertEqual(stats["confirmed"], 1)
        self.assertEqual(by_key["uber_eats|receipt-1"]["card_id"], "mc_1111")
        self.assertEqual(by_key["uber_eats|receipt-1"]["assignment_source"], "uber_receipt_api")
        self.assertEqual(by_key["walmart_ca|wm-1"]["card_id"], "scotia_visa_2222")
        self.assertEqual(by_key["walmart_ca|wm-1"]["assignment_source"], "manual")


if __name__ == "__main__":
    unittest.main()
