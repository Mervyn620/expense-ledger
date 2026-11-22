import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "importers" / "uber_eats.py"

spec = importlib.util.spec_from_file_location("import_uber_eats_json", SCRIPT)
uber_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uber_import)


class UberEatsImportTest(unittest.TestCase):
    def test_parse_order_blocks_without_cross_order_items(self):
        snapshot = {
            "capturedAt": "2026-05-30T19:05:49.797Z",
            "lines": [
                "Past Orders",
                "View store",
                "Walmart",
                "2 items for CA$16.95",
                "May 20 at 10:54 AM",
                "View receipt",
                "Request Invoice",
                "Delivered (2)",
                "Basil",
                "1 count",
                "Eggs",
                "1 count",
                "Rate your order",
                "View store",
                "Noodle Shop",
                "2 items for CA$17.26",
                "May 20 at 10:54 AM",
                "View receipt",
                "Request Invoice",
                "2",
                "Rice noodle roll",
                "Reorder",
                "View store",
                "Old Shop",
                "1 item for CA$21.45",
                "Apr 30 at 12:14 PM",
                "View receipt",
                "1",
                "Old bowl",
            ],
            "links": [
                {
                    "text": "View receipt",
                    "href": "https://www.ubereats.com/orders?mod=orderReceipt&modctx=first-order&ps=1",
                },
                {
                    "text": "View receipt",
                    "href": "https://www.ubereats.com/orders?mod=orderReceipt&modctx=second-order&ps=1",
                },
                {
                    "text": "View receipt",
                    "href": "https://www.ubereats.com/orders?mod=orderReceipt&modctx=old-order&ps=1",
                },
            ],
        }

        orders = uber_import.parse_orders(snapshot)

        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0]["transaction"]["order_id"], "first-order")
        self.assertEqual(orders[1]["transaction"]["order_id"], "second-order")
        self.assertEqual(orders[0]["transaction"]["category"], "grocery_delivery")
        self.assertEqual(orders[1]["transaction"]["category"], "food_delivery")
        self.assertEqual(
            orders[0]["transaction"]["item"],
            "Basil; Eggs",
        )
        self.assertNotIn("Noodle Shop", orders[0]["transaction"]["item"])

    def test_parse_items_skips_modifier_lines(self):
        snapshot = {
            "capturedAt": "2026-05-30T19:05:49.797Z",
            "lines": [
                "Lazeez Shawarma",
                "2 items for CA$35.88",
                "May 02 at 1:58 PM",
                "View receipt",
                "Request Invoice",
                "1",
                "Yalla Chicken Bowl",
                "Plain Rice - Lettuce - No Spice",
                "1",
                "Yalla Falafel Bowl",
                "Plain Rice - Tomato - Less Than Medium",
            ],
            "links": [
                {
                    "text": "View receipt",
                    "href": "https://www.ubereats.com/orders?mod=orderReceipt&modctx=bowl-order&ps=1",
                },
            ],
        }

        orders = uber_import.parse_orders(snapshot)

        self.assertEqual(len(orders), 1)
        self.assertEqual(
            [item["product_name"] for item in orders[0]["items"]],
            ["Yalla Chicken Bowl", "Yalla Falafel Bowl"],
        )

    def test_rating_line_is_not_used_as_merchant(self):
        snapshot = {
            "capturedAt": "2026-11-16T01:16:58.000Z",
            "lines": [
                "Example Curry House",
                "2 items for CA$35.24",
                "Nov 06 at 1:22 PM",
                "View receipt",
                "1",
                "LAMB ROGANJOSH",
                "1",
                "CHENNAI CHICKEN 65",
                "View store",
                "Example Noodle Bar",
                "5 out of 5 stars",
                "2 items for CA$2.33",
                "Nov 02 at 1:14 PM",
                "View receipt",
                "2",
                "Taiwanese Sausage Rice",
            ],
            "links": [
                {
                    "text": "View receipt",
                    "href": "https://www.ubereats.com/orders?mod=orderReceipt&modctx=tamarind&ps=1",
                },
                {
                    "text": "View receipt",
                    "href": "https://www.ubereats.com/orders?mod=orderReceipt&modctx=rice&ps=1",
                },
            ],
        }

        orders = uber_import.parse_orders(snapshot)

        self.assertEqual(
            [order["transaction"]["merchant"] for order in orders],
            ["Example Curry House", "Example Noodle Bar"],
        )
        self.assertEqual(orders[1]["transaction"]["order_id"], "rice")
        self.assertEqual(orders[1]["transaction"]["total"], "2.33")
        self.assertEqual(orders[1]["transaction"]["date"], "2026-11-02")
        self.assertEqual(
            [item["product_name"] for item in orders[0]["items"]],
            ["LAMB ROGANJOSH", "CHENNAI CHICKEN 65"],
        )
        self.assertEqual(
            [item["product_name"] for item in orders[1]["items"]],
            ["Taiwanese Sausage Rice"],
        )

    def test_parse_orders_respects_incremental_min_date(self):
        snapshot = {
            "capturedAt": "2026-06-06T19:05:49.797Z",
            "lines": [
                "Recent Shop",
                "1 item for CA$12.00",
                "Jun 05 at 1:00 PM",
                "View receipt",
                "1",
                "Recent item",
                "Older Shop",
                "1 item for CA$10.00",
                "May 20 at 1:00 PM",
                "View receipt",
                "1",
                "Older item",
            ],
            "links": [
                {
                    "text": "View receipt",
                    "href": "https://www.ubereats.com/orders?mod=orderReceipt&modctx=recent-order&ps=1",
                },
                {
                    "text": "View receipt",
                    "href": "https://www.ubereats.com/orders?mod=orderReceipt&modctx=older-order&ps=1",
                },
            ],
        }

        orders = uber_import.parse_orders(snapshot, min_date="2026-06-01")

        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["transaction"]["order_id"], "recent-order")


if __name__ == "__main__":
    unittest.main()
