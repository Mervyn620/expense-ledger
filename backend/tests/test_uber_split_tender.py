import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ledger"))
import uber_receipt_parts as parts  # noqa: E402

spec = importlib.util.spec_from_file_location("reconcile_scotia_split", ROOT / "scripts" / "reconciliation" / "scotia.py")
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)

SPLIT = ("服务费 CA$6.45 派送费 CA$1.49 优惠 -CA$15.00 会员福利 -CA$2.71 付款 Uber Cash CA$27.02 "
         "2026/11/6 下午1:22 Visa ••••2222 CA$7.88 2026/11/6 下午11:20 订单详情 Uber Delivery")
SINGLE = "派送优惠 -CA$6.49 付款 Mastercard ••••1111 CA$19.76 2026/11/8 下午10:59 订单详情 Uber Delivery"
REFUND = ("退款调整 -CA$23.77 新总计 CA$2.00 付款 Mastercard ••••1111 CA$26.50 2026/11/2 下午1:14 "
          "Uber Cash -CA$26.50 2026/11/2 下午1:14 退款 Uber Cash CA$2.00 订单详情")
ENGLISH = "Payments Uber Cash CA$10.00 Nov 6 Visa ••••2222 CA$5.00 Nov 6 Order details"


def receipt(snippet, order_id="split-1"):
    return {"order_id": order_id, "payment_snippets": [snippet]}


class SplitTenderParsingTest(unittest.TestCase):
    def test_uber_cash_plus_card_returns_card_portion(self):
        portion = parts.card_portion(receipt(SPLIT), "35.24")
        self.assertEqual((portion["amount"], portion["last4"]), (7.88, "2222"))
        self.assertIn("Uber Cash 27.02", portion["summary"])

    def test_english_receipt_is_supported(self):
        self.assertEqual(parts.card_portion(receipt(ENGLISH), "15.00")["amount"], 5.00)

    def test_single_card_payment_is_not_a_split(self):
        self.assertIsNone(parts.card_portion(receipt(SINGLE), "19.96"))

    def test_refunded_or_negative_tenders_are_rejected(self):
        self.assertIsNone(parts.card_portion(receipt(REFUND), "2.33"))

    def test_tenders_far_from_order_total_are_rejected(self):
        self.assertIsNone(parts.card_portion(receipt(SPLIT), "60.00"))


class SplitTenderReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "uber_eats_receipts.json"
        path.write_text(json.dumps({"receipts": [receipt(SPLIT, "tamarind")]}, ensure_ascii=False), encoding="utf-8")
        self.original = reconcile.UBER_RECEIPTS_PATH
        reconcile.UBER_RECEIPTS_PATH = path
        self.order = {"platform": "uber_eats", "order_id": "tamarind", "date": "2026-11-06",
                      "merchant": "Example Curry", "total": "35.24", "currency": "CAD"}
        self.bank = {"transaction_id": "b788", "date": "2026-11-06", "account_id": "scotia_chequing_3333",
                     "description": "Pos Purchase Opos Uber Canada/Ubereatstoron", "amount": "7.88",
                     "direction": "debit", "currency": "CAD"}

    def tearDown(self):
        reconcile.UBER_RECEIPTS_PATH = self.original
        self.temp.cleanup()

    def test_bank_charge_matches_card_portion_of_split_order(self):
        plans, _ = reconcile.plan_existing_order_matches([self.bank], [self.order])
        self.assertEqual(plans["b788"]["order"]["order_id"], "tamarind")
        self.assertEqual(plans["b788"]["method"], "platform_split_tender_card_portion")
        self.assertIn("Uber Cash 27.02", plans["b788"]["notes"])

    def test_manual_review_lists_the_split_order(self):
        self.assertEqual([o["order_id"] for o in reconcile.review_candidates(self.bank, [self.order])], ["tamarind"])

    def test_card_from_another_account_does_not_match(self):
        other = {**self.bank, "account_id": "account_without_card_mapping_9999"}
        self.order["order_id"] = "tamarind"
        original = reconcile.account_card_last4
        reconcile.account_card_last4 = lambda account_id: "1234"
        try:
            plans, _ = reconcile.plan_existing_order_matches([other], [self.order])
        finally:
            reconcile.account_card_last4 = original
        self.assertNotIn("b788", plans)

    def test_full_total_charge_still_matches_normally(self):
        bank = {**self.bank, "amount": "35.24"}
        plans, _ = reconcile.plan_existing_order_matches([bank], [self.order])
        self.assertEqual(plans["b788"]["method"], "platform_amount_unique_nearest_date_3d")


if __name__ == "__main__":
    unittest.main()
