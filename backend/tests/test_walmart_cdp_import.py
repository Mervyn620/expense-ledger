import json
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "importers" / "walmart_cdp.py"

spec = importlib.util.spec_from_file_location("import_walmart_cdp", SCRIPT)
walmart_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(walmart_import)


class WalmartCdpImportTest(unittest.TestCase):
    def test_discover_snapshots_accepts_newer_walmart_order_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp)
            old_order = raw / "2026-06-21T23-46-50Z_walmart_ca_www-walmart-ca_600000098138166.json"
            newer_order = raw / "2026-06-21T23-46-52Z_walmart_ca_www-walmart-ca_600000102347560.json"
            not_walmart = raw / "2026-06-21T23-46-52Z_uber_eats_www-ubereats-com_600000199999999.json"
            malformed = raw / "2026-06-21T23-46-52Z_walmart_ca_www-walmart-ca_not-an-order.json"

            for path in [old_order, newer_order, not_walmart, malformed]:
                path.write_text("{}\n", encoding="utf-8")

            snapshots = walmart_import.discover_snapshots(raw)

            self.assertEqual(set(snapshots), {"600000098138166", "600000102347560"})
            self.assertEqual(snapshots["600000102347560"], newer_order)

    def test_delivery_milestone_does_not_mark_unfinished_order_as_delivered(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "fixture.json"
            data = {"url":"https://www.walmart.ca/default/orders/600000100000001",
                    "lines":["Oct 2, 2026", "Preparing", "Delivered", "Subtotal", "$10.00", "Total", "$10.00"],
                    "itemCandidates":["Fixture grocery Qty 1 $10.00"]}
            snapshot.write_text(json.dumps(data), encoding="utf-8")
            order, _ = walmart_import.parse_snapshot(snapshot)
            self.assertEqual(order["fulfillment"], "pending")
            self.assertEqual(order["total"], "10.00")
            data["lines"].insert(1, "Delivered on Oct 2")
            snapshot.write_text(json.dumps(data), encoding="utf-8")
            order, _ = walmart_import.parse_snapshot(snapshot)
            self.assertEqual(order["fulfillment"], "delivered")

    def test_variant_duplicate_candidates_reconcile_to_receipt_subtotal(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "2026-07-12T03-28-02Z_walmart_ca_www-walmart-ca_600000101025124.json"
            snapshot.write_text(
                """
{
  "url": "https://www.walmart.ca/default/orders/600000101025124",
  "lines": [
    "Jul 10, 2026",
    "1 item",
    "9 items received",
    "Subtotal",
    "$47.90",
    "Taxes $1.56",
    "Total",
    "$49.46"
  ],
  "itemCandidates": [
    "George Men's Beach SlidesColour: GreySize: 8Qty 1$12.00",
    "George Men's Beach Slides",
    "George Men's Beach SlidesQty 1$12.00Qty 1",
    "Great Value Spring Water 500ml, pack of 24Qty 1$2.98",
    "Quaker Large Flake OatsQty 1$3.97",
    "Liberté Greek 0% Yogurt, Plain, High Protein, 750gQty 2$13.94",
    "Clover Leaf Wild Flaked Salmon - Olive Oil 80gQty 4$11.08",
    "Great Value Large 12 EggsQty 1$3.93"
  ],
  "productLinks": [
    {
      "productName": "George Men's Beach Slides",
      "productUrl": "https://www.walmart.ca/en/ip/george-men-s-beach-slides/6000207000001?selectedSellerId=0#details",
      "itemCandidate": "George Men's Beach SlidesColour: GreySize: 8Qty 1$12.00"
    },
    {
      "productName": "Great Value Large 12 Eggs",
      "productUrl": "https://www.walmart.ca/en/ip/great-value-large-12-eggs/6000207000002",
      "itemCandidate": "Great Value Large 12 EggsQty 1$3.93"
    },
    {
      "productName": "Not a purchased item",
      "productUrl": "https://example.com/en/ip/not-walmart/6000207000999",
      "itemCandidate": "Recommended for you"
    }
  ]
}
""",
                encoding="utf-8",
            )

            transaction, item_rows = walmart_import.parse_snapshot(snapshot)

            self.assertEqual(transaction["item_total"], "47.90")
            self.assertEqual(transaction["tax"], "1.56")
            self.assertEqual(transaction["total"], "49.46")
            self.assertEqual(transaction["quantity"], "10")
            self.assertEqual(len(item_rows), 6)
            product_names = [row["product_name"] for row in item_rows]
            self.assertEqual(product_names.count("George Men's Beach Slides"), 1)
            self.assertFalse(any("Colour:" in name or "Size:" in name for name in product_names))
            links_by_name = {
                row["product_name"]: row["product_link"] for row in item_rows
            }
            self.assertEqual(
                links_by_name["George Men's Beach Slides"],
                "https://www.walmart.ca/en/ip/george-men-s-beach-slides/6000207000001",
            )
            self.assertEqual(
                links_by_name["Great Value Large 12 Eggs"],
                "https://www.walmart.ca/en/ip/great-value-large-12-eggs/6000207000002",
            )

    def test_weight_adjusted_duplicate_uses_net_subtotal_after_savings(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "2026-09-03T02-31-52Z_walmart_ca_www-walmart-ca_600000112017311.json"
            snapshot.write_text(
                """
{
  "url": "https://www.walmart.ca/default/orders/600000112017311",
  "lines": [
    "Aug 31, 2026",
    "Delivered",
    "Subtotal",
    "$33.74",
    "Savings",
    "-$1.18",
    "$32.56",
    "Taxes $0.71",
    "Total",
    "$33.27"
  ],
  "itemCandidates": [
    "Maple Leaf Pork Belly, 0.92 - 1.21 KG0.605ea x $18.67/ea SubstitutionsQty 1$11.30",
    "Maple Leaf Pork Belly, 0.92 - 1.21 KG",
    "Ginger, Sold in singles, 0.12 - 0.15 KG0.122kg x $4.34/kg Weight-adjustedQty 1$0.52",
    "Green Onion, Sold in bunches ShoppedQty 1$1.27",
    "OREO Frozen Dessert Sandwiches ShoppedQty 1$5.48",
    "Mutti Whole Peeled Tomatoes ShoppedQty 1$5.07",
    "Pork Sausages ShoppedQty 1$1.18 from savingsDiscount price $6.48$6.48 Was $7.66$7.66",
    "Barilla Spaghetti Pasta ShoppedQty 1$2.44",
    "Maple Leaf Pork Belly, 0.92 - 1.21 KGSubstitutionsQty 1$11.30Qty 1"
  ]
}
""",
                encoding="utf-8",
            )

            transaction, item_rows = walmart_import.parse_snapshot(snapshot)

            self.assertEqual(transaction["item_total"], "32.56")
            self.assertEqual(transaction["total"], "33.27")
            self.assertEqual(transaction["quantity"], "7")
            self.assertEqual(len(item_rows), 7)
            pork_rows = [row for row in item_rows if "Pork Belly" in row["product_name"]]
            self.assertEqual(len(pork_rows), 1)
            self.assertEqual(pork_rows[0]["product_name"], "Maple Leaf Pork Belly, 0.92 - 1.21 KG")
            self.assertEqual(pork_rows[0]["delivery_status"], "substituted")
            self.assertFalse(any("x $" in row["product_name"] for row in item_rows))

    def test_ambiguous_product_links_are_not_guessed(self):
        items = [{
            "product_name": "Great Value Milk",
            "product_link": "",
        }]
        candidates = [
            {
                "productName": "Great Value Milk",
                "productUrl": "https://www.walmart.ca/en/ip/milk-one/6000207000011",
                "itemCandidate": "Great Value MilkQty 1$5.00",
            },
            {
                "productName": "Great Value Milk",
                "productUrl": "https://www.walmart.ca/en/ip/milk-two/6000207000012",
                "itemCandidate": "Great Value MilkQty 1$5.00",
            },
        ]

        walmart_import.associate_product_links(items, candidates)

        self.assertEqual(items[0]["product_link"], "")

    def test_cancelled_zero_total_order_is_not_imported(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "2026-07-18T15-20-54Z_walmart_ca_www-walmart-ca_600000097939727.json"
            snapshot.write_text(
                """
{
  "url": "https://www.walmart.ca/default/orders/600000097939727",
  "lines": [
    "Jun 30, 2026",
    "Cancelled",
    "Subtotal",
    "$0.00",
    "Taxes $0.00",
    "Total",
    "$0.00"
  ],
  "itemCandidates": [
    "Protein Powder CancelledQty 1$27.74 Cancelled| The order was created by mistake"
  ]
}
""",
                encoding="utf-8",
            )

            self.assertIsNone(walmart_import.parse_snapshot(snapshot))

    def test_categorize_food_words_without_false_household_or_personal_care(self):
        cases = {
            "Lipton Yellow Label Green Tea 100 Bags": "groceries",
            "Our FinestTM 0% M.F. Plain Greek Yogurt": "groceries",
            "Quaker Quick Oats": "groceries",
            "Great Value Small Kitchen Garbage Bags, 25L , 48 Bags": "household",
            "Dove Men+Care 2-in-1 Shampoo + Conditioner Strengthening Tea Tree Oil": "uncategorized",
            "Pure Protein 100% Whey Protein Powder - Vanilla, 2 g Sugar": "uncategorized",
            "Club House, Black Peppercorns Grinder Moulin 35 G": "uncategorized",
            "Mainstays Brynn Ceramic Black 9 in Serve Bowl": "kitchenware",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(walmart_import.categorize(name), expected)


if __name__ == "__main__":
    unittest.main()
