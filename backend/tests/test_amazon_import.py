import csv
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "importers" / "amazon.py"

spec = importlib.util.spec_from_file_location("import_amazon", SCRIPT)
amazon_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(amazon_import)


class AmazonImportTest(unittest.TestCase):
    def payload(self, line_total="19.99"):
        return {
            "platform": "amazon_ca",
            "orders": [{
                "date": "2026-06-28",
                "order_id": "701-1234567-1234567",
                "merchant": "Amazon",
                "fulfillment": "delivered",
                "category": "general_retail",
                "quantity": "1",
                "item_total": "19.99",
                "tax": "2.60",
                "fees": "0.00",
                "tip": "0.00",
                "total": "22.59",
                "currency": "CAD",
                "source_url": (
                    "https://www.amazon.ca/gp/your-account/order-details"
                    "?orderID=701-1234567-1234567"
                ),
                "notes": "Sanitized Amazon order.",
                "items": [{
                    "asin": "B012345678",
                    "title": "USB-C Cable",
                    "quantity": 1,
                    "line_total": line_total,
                    "product_url": "https://www.amazon.ca/dp/B012345678",
                }],
            }],
        }

    def test_normalize_payload_preserves_order_and_item_contract(self):
        transactions, items = amazon_import.normalize_payload(self.payload())

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["platform"], "amazon_ca")
        self.assertEqual(transactions[0]["total"], "22.59")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["product_name"], "USB-C Cable")
        self.assertEqual(items[0]["price"], "19.99")

    def test_pending_order_is_preserved_for_pre_delivery_accounting(self):
        payload = self.payload()
        payload["orders"][0]["fulfillment"] = "pending"

        transactions, items = amazon_import.normalize_payload(payload)

        self.assertEqual(transactions[0]["fulfillment"], "pending")
        self.assertEqual(items[0]["delivery_status"], "pending")

    def test_repeated_import_replaces_item_rows_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            (root / "config").mkdir()
            (root / "config" / "payment_cards.json").write_text(
                json.dumps({
                    "default_card_id": "unassigned",
                    "cards": {
                        "unassigned": {
                            "display_name": "Needs Review",
                            "network": "",
                            "issuer": "",
                            "last4": "",
                            "kind": "unknown",
                        },
                    },
                }),
                encoding="utf-8",
            )
            source = root / "data" / "amazon_orders.json"
            source.write_text(json.dumps(self.payload()), encoding="utf-8")

            first = amazon_import.import_file(root)
            source.write_text(json.dumps(self.payload(line_total="18.99")), encoding="utf-8")
            second = amazon_import.import_file(root)

            self.assertEqual(first["orders"], 1)
            self.assertEqual(second["orders"], 1)
            with (root / "data" / "amazon_items.csv").open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                item_rows = list(csv.DictReader(handle))
            self.assertEqual(len(item_rows), 1)
            self.assertEqual(item_rows[0]["price"], "18.99")

            with closing(sqlite3.connect(root / "data" / "expenses.sqlite")) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT price FROM order_items").fetchone()[0],
                    18.99,
                )


if __name__ == "__main__":
    unittest.main()
