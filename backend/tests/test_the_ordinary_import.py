import csv
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "importers" / "the_ordinary.py"

spec = importlib.util.spec_from_file_location("import_the_ordinary", SCRIPT)
ordinary_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ordinary_import)


class TheOrdinaryImportTest(unittest.TestCase):
    def payload(self, line_total="26.30"):
        return {
            "contract": "expense-the-ordinary-orders/v1",
            "platform": "the_ordinary_ca",
            "orders": [{
                "date": "2026-08-07",
                "order_id": "12345678",
                "fulfillment": "delivered",
                "category": "personal_care",
                "item_total": "26.30",
                "tax": "3.42",
                "fees": "0.00",
                "total": "29.72",
                "currency": "CAD",
                "source_url": "https://theordinary.com/en-ca/orders",
                "items": [{
                    "title": "Example Serum",
                    "quantity": 1,
                    "line_total": line_total,
                    "product_url": "https://theordinary.com/en-ca/product/example",
                }],
            }],
        }

    def test_normalize_payload_preserves_order_and_item_contract(self):
        transactions, items = ordinary_import.normalize_payload(self.payload())

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["platform"], "the_ordinary_ca")
        self.assertEqual(transactions[0]["category"], "personal_care")
        self.assertEqual(transactions[0]["total"], "29.72")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["product_name"], "Example Serum")
        self.assertEqual(items[0]["price"], "26.30")

    def test_repeated_import_replaces_items_without_duplicates(self):
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
            source = root / "data" / "the_ordinary_orders.json"
            source.write_text(json.dumps(self.payload()), encoding="utf-8")

            first = ordinary_import.import_file(root)
            source.write_text(
                json.dumps(self.payload(line_total="25.30")),
                encoding="utf-8",
            )
            second = ordinary_import.import_file(root)

            self.assertEqual(first["orders"], 1)
            self.assertEqual(second["orders"], 1)
            with (root / "data" / "the_ordinary_items.csv").open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                item_rows = list(csv.DictReader(handle))
            self.assertEqual(len(item_rows), 1)
            self.assertEqual(item_rows[0]["price"], "25.30")

            with closing(sqlite3.connect(root / "data" / "expenses.sqlite")) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0],
                    1,
                )

    def test_invalid_contract_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            (root / "data" / "the_ordinary_orders.json").write_text(
                json.dumps({"contract": "unexpected", "orders": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "contract is invalid"):
                ordinary_import.import_file(root)


if __name__ == "__main__":
    unittest.main()
