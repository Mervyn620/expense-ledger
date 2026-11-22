import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reconciliation" / "scotia.py"

spec = importlib.util.spec_from_file_location("reconcile_scotia", SCRIPT)
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)


class ScotiaReconciliationTest(unittest.TestCase):
    def test_unknown_purchase_channel_remains_needs_review(self):
        account = reconcile.account_details("scotia_chequing_3333")
        payment = reconcile.inferred_payment({"description": "Pos Purchase Example Merchant"}, account)
        self.assertEqual(payment["channel"], "unknown")
        self.assertEqual(payment["status"], "needs_review")
        self.assertEqual(payment["card_id"], account["default_payment_instrument_id"])

    def test_in_app_purchase_code_is_online(self):
        account = reconcile.account_details("scotia_chequing_3333")
        payment = reconcile.inferred_payment(
            {"description": "Pos Purchase Aips Presto Mobi Toron"}, account)
        self.assertEqual(payment["channel"], "online")
        self.assertEqual(payment["status"], "inferred")

    def test_missing_card_mapping_is_not_claimed_as_inferred(self):
        account = reconcile.account_details("scotia_chequing_3333")
        account["default_payment_instrument_id"] = "unassigned"
        payment = reconcile.inferred_payment({"description": "Pos Purchase Opos Example"}, account)
        self.assertEqual(payment["status"], "needs_review")


    def test_purchase_and_correction_are_reversed(self):
        rows = [
            {
                "transaction_id": "debit",
                "date": "2026-05-05",
                "description": "Pos Purchase Opos Uber Canada/Ubereatstoron",
                "amount": "32.29",
                "direction": "debit",
            },
            {
                "transaction_id": "credit",
                "date": "2026-05-05",
                "description": "Correction Opos Uber Canada/Ubereatstoron",
                "amount": "32.29",
                "direction": "credit",
            },
        ]

        reversed_ids, pairs = reconcile.find_reversals(rows)

        self.assertEqual(reversed_ids, {"debit", "credit"})
        self.assertEqual(pairs["debit"], "credit")
        self.assertEqual(pairs["credit"], "debit")

    def test_unique_next_day_correction_is_reversed(self):
        rows = [
            {"transaction_id": "debit", "date": "2026-11-05", "account_id": "account-a",
             "description": "Pos Purchase Opos Uber Canada/Ubereatstoron", "amount": "8.22", "direction": "debit"},
            {"transaction_id": "credit", "date": "2026-11-06", "account_id": "account-a",
             "description": "Correction Opos Uber Canada/Ubereatstoron", "amount": "8.22", "direction": "credit"},
        ]
        reversed_ids, pairs = reconcile.find_reversals(rows)
        self.assertEqual(reversed_ids, {"debit", "credit"})
        self.assertEqual(pairs, {"debit": "credit", "credit": "debit"})

    def test_next_day_correction_requires_unique_same_account_debit(self):
        debit = {"transaction_id": "debit", "date": "2026-11-05", "account_id": "account-a",
                 "description": "Pos Purchase Opos Uber Canada/Ubereatstoron", "amount": "8.22", "direction": "debit"}
        credit = {"transaction_id": "credit", "date": "2026-11-06", "account_id": "account-a",
                  "description": "Correction Opos Uber Canada/Ubereatstoron", "amount": "8.22", "direction": "credit"}
        other_account = {**debit, "transaction_id": "other-account", "account_id": "account-b"}
        same_account = {**debit, "transaction_id": "duplicate"}
        ordinary_credit = {**credit, "description": "Refund Opos Uber Canada/Ubereatstoron"}
        self.assertEqual(reconcile.find_reversals([other_account, credit])[0], set())
        self.assertEqual(reconcile.find_reversals([debit, same_account, credit])[0], set())
        self.assertEqual(reconcile.find_reversals([debit, ordinary_credit])[0], set())

    def test_exact_amount_matches_with_posting_delay(self):
        bank_row = {
            "date": "2026-05-11",
            "amount": "17.47",
        }
        orders = [
            {
                "date": "2026-05-10",
                "platform": "uber_eats",
                "order_id": "order-1",
                "total": "17.47",
            }
        ]

        match = reconcile.match_existing_order(
            bank_row,
            "uber_eats",
            orders,
            set(),
        )

        self.assertEqual(match["order_id"], "order-1")

    def test_nearest_unique_order_wins_over_more_distant_candidate(self):
        bank_row = {
            "date": "2026-05-11",
            "amount": "17.47",
        }
        orders = [
            {
                "date": "2026-05-08",
                "platform": "uber_eats",
                "order_id": "older-order",
                "total": "17.47",
            },
            {
                "date": "2026-05-10",
                "platform": "uber_eats",
                "order_id": "nearest-order",
                "total": "17.47",
            },
        ]

        match, reason, candidate_count = reconcile.select_existing_order_match(
            bank_row,
            "uber_eats",
            orders,
            set(),
        )

        self.assertEqual(match["order_id"], "nearest-order")
        self.assertEqual(reason, "unique_nearest_candidate")
        self.assertEqual(candidate_count, 1)

    def test_equally_ranked_orders_require_manual_review(self):
        bank_row = {
            "date": "2026-05-11",
            "amount": "17.47",
        }
        orders = [
            {
                "date": "2026-05-10",
                "platform": "uber_eats",
                "order_id": "order-before",
                "total": "17.47",
            },
            {
                "date": "2026-05-12",
                "platform": "uber_eats",
                "order_id": "order-after",
                "total": "17.47",
            },
        ]
        used_keys = set()

        match, reason, candidate_count = reconcile.select_existing_order_match(
            bank_row,
            "uber_eats",
            orders,
            used_keys,
        )

        self.assertIsNone(match)
        self.assertEqual(reason, "ambiguous_candidates")
        self.assertEqual(candidate_count, 2)
        self.assertEqual(used_keys, set())

    def test_same_day_split_charges_match_one_adjusted_order(self):
        bank_rows = [
            {
                "transaction_id": "first",
                "date": "2026-06-30",
                "description": "Pos Purchase Opos Walmart.Ca Missi",
                "amount": "33.87",
                "direction": "debit",
                "account_id": "scotia_chequing_3333",
            },
            {
                "transaction_id": "second",
                "date": "2026-06-30",
                "description": "Pos Purchase Opos Walmart.Ca Missi",
                "amount": "41.74",
                "direction": "debit",
                "account_id": "scotia_chequing_3333",
            },
        ]
        orders = [
            {
                "date": "2026-06-30",
                "platform": "walmart_ca",
                "order_id": "600000097338826",
                "total": "75.03",
            }
        ]

        plans, _ = reconcile.plan_existing_order_matches(bank_rows, orders)

        self.assertEqual(plans["first"]["order"]["order_id"], "600000097338826")
        self.assertEqual(plans["second"]["order"]["order_id"], "600000097338826")
        self.assertEqual(
            plans["first"]["method"],
            "platform_split_amount_adjusted_same_day_unique",
        )
        self.assertIn("0.58 difference", plans["first"]["notes"])

    def test_split_charge_match_is_rejected_when_order_candidate_is_ambiguous(self):
        bank_rows = [
            {
                "transaction_id": "first",
                "date": "2026-06-30",
                "description": "Pos Purchase Opos Walmart.Ca Missi",
                "amount": "33.87",
                "direction": "debit",
            },
            {
                "transaction_id": "second",
                "date": "2026-06-30",
                "description": "Pos Purchase Opos Walmart.Ca Missi",
                "amount": "41.74",
                "direction": "debit",
            },
        ]
        orders = [
            {
                "date": "2026-06-30",
                "platform": "walmart_ca",
                "order_id": "order-a",
                "total": "75.03",
            },
            {
                "date": "2026-06-30",
                "platform": "walmart_ca",
                "order_id": "order-b",
                "total": "75.20",
            },
        ]

        plans, _ = reconcile.plan_existing_order_matches(bank_rows, orders)

        self.assertEqual(plans, {})

    def test_split_charge_match_is_rejected_outside_adjustment_tolerance(self):
        bank_rows = [
            {
                "transaction_id": "first",
                "date": "2026-06-30",
                "description": "Pos Purchase Opos Walmart.Ca Missi",
                "amount": "30.00",
                "direction": "debit",
            },
            {
                "transaction_id": "second",
                "date": "2026-06-30",
                "description": "Pos Purchase Opos Walmart.Ca Missi",
                "amount": "40.00",
                "direction": "debit",
            },
        ]
        orders = [
            {
                "date": "2026-06-30",
                "platform": "walmart_ca",
                "order_id": "order-a",
                "total": "75.03",
            }
        ]

        plans, _ = reconcile.plan_existing_order_matches(bank_rows, orders)

        self.assertEqual(plans, {})

    def test_uber_pass_becomes_membership_order(self):
        bank_row = {
            "transaction_id": "abc123",
            "date": "2026-05-22",
            "description": "Pos Purchase Opos Uberdirectca_pass Toron",
            "amount": "11.29",
            "currency": "CAD",
        }

        profile = reconcile.merchant_profile(bank_row["description"])
        account = reconcile.account_details("scotia_chequing_3333")
        transaction, item, assignment = reconcile.bank_only_rows(bank_row, profile, account)

        self.assertEqual(transaction["order_id"], "scotia-abc123")
        self.assertEqual(transaction["platform"], "scotiabank")
        self.assertEqual(item["platform"], "scotiabank")
        self.assertEqual(assignment["platform"], "scotiabank")
        self.assertEqual(transaction["category"], "delivery_membership")
        self.assertEqual(item["price"], "11.29")
        self.assertEqual(assignment["card_id"], "scotia_visa_2222")
        self.assertEqual(assignment["assignment_status"], "inferred")
        self.assertEqual(assignment["payment_channel"], "online")
        self.assertEqual(
            assignment["assignment_source"],
            "scotia_account_instrument_mapping",
        )

    def test_unmatched_walmart_charge_is_a_scotiabank_source(self):
        bank_row = {
            "transaction_id": "walmart123",
            "date": "2026-06-30",
            "description": "Pos Purchase Opos Walmart.Ca Missi",
            "amount": "41.74",
            "currency": "CAD",
        }

        profile = reconcile.merchant_profile(bank_row["description"])
        account = reconcile.account_details("scotia_chequing_3333")
        transaction, item, assignment = reconcile.bank_only_rows(
            bank_row,
            profile,
            account,
        )

        self.assertEqual(profile["platform"], "walmart_ca")
        self.assertEqual(transaction["platform"], "scotiabank")
        self.assertEqual(transaction["merchant"], "Walmart")
        self.assertEqual(item["platform"], "scotiabank")
        self.assertEqual(assignment["platform"], "scotiabank")
        self.assertIn("detected merchant platform=walmart_ca", transaction["notes"])

    def test_service_charge_uses_account_debit_not_chequing_as_a_card(self):
        bank_row = {
            "transaction_id": "fee123",
            "date": "2026-06-29",
            "description": "Service Charge",
            "amount": "15.00",
            "currency": "CAD",
        }

        profile = reconcile.merchant_profile(bank_row["description"])
        account = reconcile.account_details("scotia_chequing_3333")
        _, _, assignment = reconcile.bank_only_rows(bank_row, profile, account)

        self.assertEqual(assignment["card_id"], "account_debit")
        self.assertEqual(assignment["assignment_status"], "confirmed")
        self.assertEqual(assignment["payment_channel"], "account_debit")

    def test_openai_is_classified_as_software_subscription(self):
        profile = reconcile.merchant_profile(
            "Pos Purchase Opos Openai *Chatgpt Subs+1415"
        )

        self.assertEqual(profile["merchant"], "OpenAI ChatGPT")
        self.assertEqual(profile["category"], "software_subscription")
        self.assertEqual(profile["platform"], "")

    def test_interac_withdrawal_is_excluded_transfer(self):
        self.assertTrue(
            reconcile.is_transfer("Withdrawal Free Interac E-Transfer")
        )

    def test_walmart_can_match_existing_platform_order(self):
        self.assertEqual(
            reconcile.detected_platform("Pos Purchase Opos Walmart.Ca Missi"),
            "walmart_ca",
        )
        profile = reconcile.merchant_profile("Pos Purchase Opos Walmart.Ca Missi")
        self.assertTrue(profile["requires_order_match"])

    def test_walmart_store_purchase_is_not_an_online_collector_charge(self):
        profile = reconcile.merchant_profile(
            "Pos Purchase Fpos Walmart #1234 Missi"
        )

        self.assertEqual(profile["platform"], "")
        self.assertEqual(profile["merchant"], "Walmart")
        self.assertEqual(profile["fulfillment"], "in_store")

    def test_amazon_can_match_existing_platform_order(self):
        self.assertEqual(
            reconcile.detected_platform("POS Purchase AMZN Mktp CA"),
            "amazon_ca",
        )
        profile = reconcile.merchant_profile("POS Purchase AMZN Mktp CA")
        self.assertTrue(profile["requires_order_match"])

    def test_amazon_prime_is_classified_as_retail_membership(self):
        profile = reconcile.merchant_profile(
            "POS Purchase AMAZON.CA PRIME MEMBAMAZO"
        )

        self.assertEqual(profile["merchant"], "Amazon Prime")
        self.assertEqual(profile["category"], "retail_membership")
        self.assertEqual(profile["fulfillment"], "subscription")
        self.assertFalse(profile["requires_order_match"])

    def test_the_ordinary_and_deciem_match_collected_orders(self):
        for description in (
            "POS Purchase DECIEM TORONTO",
            "POS Purchase THE ORDINARY ONLINE",
        ):
            with self.subTest(description=description):
                profile = reconcile.merchant_profile(description)
                self.assertEqual(profile["platform"], "the_ordinary_ca")
                self.assertEqual(profile["merchant"], "The Ordinary")
                self.assertEqual(profile["category"], "personal_care")
                self.assertTrue(profile["requires_order_match"])

    def test_uber_eats_and_fantuan_require_platform_orders(self):
        uber = reconcile.merchant_profile(
            "Pos Purchase Opos Uber Canada/Ubereatstoron"
        )
        fantuan = reconcile.merchant_profile("Pos Purchase Opos Fantuan Toron")

        self.assertEqual(uber["platform"], "uber_eats")
        self.assertTrue(uber["requires_order_match"])
        self.assertEqual(fantuan["platform"], "fantuan")
        self.assertTrue(fantuan["requires_order_match"])

    def test_bank_only_order_does_not_match_itself(self):
        bank_row = {
            "date": "2026-05-22",
            "amount": "11.29",
        }
        orders = [
            {
                "date": "2026-05-22",
                "platform": "uber_eats",
                "order_id": "scotia-abc123",
                "total": "11.29",
            }
        ]

        match = reconcile.match_existing_order(
            bank_row,
            "uber_eats",
            orders,
            set(),
        )

        self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
