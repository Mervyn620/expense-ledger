import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "importers" / "payment_evidence.py"

spec = importlib.util.spec_from_file_location("import_payment_evidence", SCRIPT)
payment_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payment_import)


class PaymentEvidenceImportTest(unittest.TestCase):
    def test_configured_credit_default_and_better_evidence(self):
        self.cards["mc_1111"].update(kind="credit_card", default_for_unspecified_credit=True)
        self.cards["wechat_pay"] = {"display_name": "WeChat Pay"}
        tx = {"platform": "fantuan", "order_id": "example"}
        evidence = {**tx, "status": "method_only", "payment_method": "信用卡/借记卡", "card_last4": ""}

        def assign(existing=(), **changes):
            rows, _ = payment_import.update_assignments(
                [tx], existing, [{**evidence, **changes}], self.cards, "unassigned")
            return rows[0]

        inferred = assign()
        self.assertEqual((inferred["card_id"], inferred["assignment_status"],
                          inferred["assignment_source"]), ("mc_1111", "inferred", "user_default_credit_card"))
        self.assertEqual(assign([inferred]), inferred)
        self.assertNotIn("1111", inferred["notes"])
        for changes in [{"payment_method": "Debit Card"}, {"card_network": "Visa"},
                        {"card_last4": "9999"}, {"payment_method": "card"}]:
            with self.subTest(changes=changes):
                self.assertEqual(assign([inferred], **changes)["card_id"], "unassigned")
        self.assertEqual(assign([inferred], payment_method="WeChat Pay")["card_id"], "wechat_pay")
        confirmed = assign([inferred], card_network="Visa", card_last4="2222", status="confirmed")
        self.assertEqual((confirmed["card_id"], confirmed["assignment_status"]), ("scotia_visa_2222", "confirmed"))
        self.assertEqual(assign([confirmed]), confirmed)
        self.cards["mc_1111"]["default_for_unspecified_credit"] = False
        self.assertEqual(assign([inferred])["card_id"], "unassigned")

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

    def test_known_walmart_last4_confirms_assignment(self):
        transactions = [
            {
                "date": "2026-05-19",
                "platform": "walmart_ca",
                "order_id": "600000095322869",
                "merchant": "Walmart",
                "total": "47.59",
                "currency": "CAD",
            }
        ]
        evidence = [
            {
                "platform": "walmart_ca",
                "order_id": "600000095322869",
                "payment_method": "card",
                "card_network": "",
                "card_last4": "1111",
                "status": "confirmed",
            }
        ]

        rows, stats = payment_import.update_assignments(
            transactions,
            [],
            evidence,
            self.cards,
            "unassigned",
        )

        self.assertEqual(stats["confirmed"], 1)
        self.assertEqual(rows[0]["card_id"], "mc_1111")
        self.assertEqual(rows[0]["assignment_source"], "walmart_ca_payment_dom")

    def test_method_only_keeps_fantuan_unassigned(self):
        transactions = [
            {
                "date": "2026-05-26",
                "platform": "fantuan",
                "order_id": "CA2175274501",
                "merchant": "Restaurant",
                "total": "18.89",
                "currency": "CAD",
            }
        ]
        evidence = [
            {
                "platform": "fantuan",
                "order_id": "CA2175274501",
                "payment_method": "WeChat Pay",
                "card_network": "",
                "card_last4": "",
                "status": "method_only",
            }
        ]

        rows, stats = payment_import.update_assignments(
            transactions,
            [],
            evidence,
            self.cards,
            "unassigned",
        )

        self.assertEqual(stats["method_only"], 1)
        self.assertEqual(rows[0]["card_id"], "unassigned")
        self.assertEqual(rows[0]["assignment_source"], "fantuan_payment_method_dom")

    def test_wechat_method_only_maps_to_independent_wallet_source(self):
        cards = {
            **self.cards,
            "wechat_pay": {
                "display_name": "WeChat Pay",
                "network": "Digital wallet",
                "last4": "",
            },
        }
        transactions = [
            {
                "date": "2026-05-26",
                "platform": "fantuan",
                "order_id": "CA2175274501",
                "merchant": "Restaurant",
                "total": "18.89",
                "currency": "CAD",
            }
        ]
        evidence = [
            {
                "platform": "fantuan",
                "order_id": "CA2175274501",
                "payment_method": "微信支付",
                "card_network": "",
                "card_last4": "",
                "status": "method_only",
            }
        ]

        rows, stats = payment_import.update_assignments(
            transactions,
            [],
            evidence,
            cards,
            "unassigned",
        )

        self.assertEqual(stats["confirmed"], 1)
        self.assertEqual(stats["method_only"], 0)
        self.assertEqual(rows[0]["card_id"], "wechat_pay")
        self.assertEqual(rows[0]["card_label"], "WeChat Pay")
        self.assertEqual(rows[0]["assignment_status"], "confirmed")
        self.assertEqual(rows[0]["assignment_source"], "fantuan_payment_method_dom")

    def test_alipay_method_only_maps_to_independent_wallet_source(self):
        cards = {
            **self.cards,
            "alipay": {
                "display_name": "支付宝",
                "network": "Digital wallet",
                "last4": "",
            },
        }
        transactions = [
            {
                "date": "2026-07-16",
                "platform": "fantuan",
                "order_id": "CA2192378201",
                "merchant": "尚海味道",
                "total": "28.70",
                "currency": "CAD",
            }
        ]
        evidence = [
            {
                "platform": "fantuan",
                "order_id": "CA2192378201",
                "payment_method": "支付宝",
                "card_network": "",
                "card_last4": "",
                "status": "method_only",
            }
        ]

        rows, stats = payment_import.update_assignments(
            transactions,
            [],
            evidence,
            cards,
            "unassigned",
        )

        self.assertEqual(stats["confirmed"], 1)
        self.assertEqual(stats["method_only"], 0)
        self.assertEqual(rows[0]["card_id"], "alipay")
        self.assertEqual(rows[0]["card_label"], "支付宝")
        self.assertEqual(rows[0]["assignment_status"], "confirmed")
        self.assertEqual(rows[0]["assignment_source"], "fantuan_payment_method_dom")


if __name__ == "__main__":
    unittest.main()
