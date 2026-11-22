import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ledger" / "payment_assignments.py"

spec = importlib.util.spec_from_file_location("payment_assignments", SCRIPT)
assignments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assignments)


class PaymentAssignmentsTest(unittest.TestCase):
    def setUp(self):
        self.cards = {
            "mc_1111": {
                "display_name": "Mastercard 1111",
                "network": "Mastercard",
                "last4": "1111",
            },
            "unassigned": {
                "display_name": "Unassigned",
                "network": "",
                "last4": "",
            },
        }

    def test_card_for_last4_matches_network_and_last4(self):
        card_id = assignments.card_for_last4("1111", "Mastercard", self.cards)
        self.assertEqual(card_id, "mc_1111")

    def test_refresh_preserves_manual_assignment_and_adds_new_order(self):
        transactions = [
            {
                "date": "2026-05-01",
                "platform": "walmart_ca",
                "order_id": "one",
                "merchant": "Walmart",
                "total": "10.00",
                "currency": "CAD",
            },
            {
                "date": "2026-05-02",
                "platform": "fantuan",
                "order_id": "two",
                "merchant": "Fantuan",
                "total": "20.00",
                "currency": "CAD",
            },
        ]
        existing = [{
            "platform": "walmart_ca",
            "order_id": "one",
            "card_id": "mc_1111",
            "assignment_status": "confirmed",
            "assignment_source": "manual",
        }]

        rows = assignments.refresh_assignment_rows(
            transactions,
            existing,
            self.cards,
            "unassigned",
        )

        self.assertEqual(rows[0]["card_id"], "mc_1111")
        self.assertEqual(rows[0]["assignment_source"], "manual")
        self.assertEqual(rows[0]["payment_channel"], "online")
        self.assertEqual(rows[1]["card_id"], "unassigned")
        self.assertEqual(rows[1]["assignment_status"], "needs_review")
        self.assertEqual(rows[1]["payment_channel"], "online")


if __name__ == "__main__":
    unittest.main()
