import csv
import importlib.util
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ledger" / "sync.py"
DB_SCRIPT = ROOT / "scripts" / "ledger" / "db.py"

spec = importlib.util.spec_from_file_location("sync_sqlite", SCRIPT)
sync_sqlite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_sqlite)

db_spec = importlib.util.spec_from_file_location("ledger_db", DB_SCRIPT)
ledger_db = importlib.util.module_from_spec(db_spec)
db_spec.loader.exec_module(ledger_db)


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class SqliteSyncTest(unittest.TestCase):
    def test_item_staging_paths_are_manifest_driven_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "config").mkdir()
            (root / "config" / "collectors.json").write_text(
                json.dumps({
                    "collectors": [
                        {"outputs": {"item_staging": "data/delivery_items.csv"}},
                        {"outputs": {"item_staging": "data/delivery_items.csv"}},
                        {"outputs": {"item_staging": "data/the_ordinary_items.csv"}},
                    ],
                }),
                encoding="utf-8",
            )

            paths = ledger_db.item_staging_paths(root)

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in paths],
                [
                    "data/delivery_items.csv",
                    "data/the_ordinary_items.csv",
                    "data/bank_items.csv",
                ],
            )

    def test_atomic_csv_write_preserves_previous_file_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "transactions.csv"
            path.write_text("old-content\n", encoding="utf-8")

            def broken_rows():
                yield {"platform": "test"}
                raise RuntimeError("simulated write interruption")

            with self.assertRaises(RuntimeError):
                ledger_db.write_csv(path, ["platform"], broken_rows())

            self.assertEqual(path.read_text(encoding="utf-8"), "old-content\n")
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_schema_upgrade_replaces_stale_powerbi_views(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE VIEW powerbi_orders AS SELECT 1 AS stale")
            conn.execute("PRAGMA user_version = 0")

            ledger_db.create_schema(conn)

            view_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'view' AND name = 'powerbi_orders'"
            ).fetchone()[0]
            self.assertIn("FROM orders o", view_sql)
            self.assertIn("payment_channel", view_sql)
            self.assertNotIn("AS stale", view_sql)
            self.assertGreaterEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
            payment_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(order_payments)")
            }
            self.assertIn("payment_channel", payment_columns)

    def write_cards(self, root):
        (root / "config").mkdir()
        (root / "data").mkdir()
        (root / "config" / "payment_cards.json").write_text(json.dumps({
            "cards": {
                "mc_1111": {
                    "display_name": "Mastercard 1111",
                    "network": "Mastercard",
                    "issuer": "",
                    "last4": "1111",
                    "kind": "credit_card",
                },
                "unassigned": {
                    "display_name": "Unassigned",
                    "network": "",
                    "issuer": "",
                    "last4": "",
                    "kind": "needs_review",
                },
            },
            "default_card_id": "unassigned",
        }), encoding="utf-8")

    def test_sync_is_idempotent_and_keeps_one_to_many_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_cards(root)

            write_csv(root / "data" / "transactions.csv", [
                "date", "platform", "order_id", "merchant", "fulfillment",
                "category", "item", "quantity", "item_total", "tax", "fees",
                "tip", "total", "currency", "source_url", "notes",
            ], [{
                "date": "2026-05-01",
                "platform": "walmart_ca",
                "order_id": "order-1",
                "merchant": "Walmart",
                "fulfillment": "delivered",
                "category": "groceries",
                "item": "Milk; Eggs",
                "quantity": "2",
                "item_total": "10.00",
                "tax": "1.30",
                "fees": "0.00",
                "tip": "0.00",
                "total": "11.30",
                "currency": "CAD",
                "source_url": "",
                "notes": "",
            }])
            write_csv(root / "data" / "walmart_items.csv", [
                "date", "platform", "order_id", "category", "product_name",
                "quantity", "price", "delivery_status", "product_link", "currency",
            ], [
                {
                    "date": "2026-05-01",
                    "platform": "walmart_ca",
                    "order_id": "order-1",
                    "category": "groceries",
                    "product_name": "Milk",
                    "quantity": "1",
                    "price": "6.00",
                    "delivery_status": "delivered",
                    "product_link": "",
                    "currency": "CAD",
                },
                {
                    "date": "2026-05-01",
                    "platform": "walmart_ca",
                    "order_id": "order-1",
                    "category": "groceries",
                    "product_name": "Eggs",
                    "quantity": "1",
                    "price": "4.00",
                    "delivery_status": "delivered",
                    "product_link": "",
                    "currency": "CAD",
                },
            ])
            write_csv(root / "data" / "delivery_items.csv", [
                "date", "platform", "order_id", "category", "product_name",
                "quantity", "price", "delivery_status", "product_link", "currency",
            ], [])
            write_csv(root / "data" / "payment_assignments.csv", [
                "date", "platform", "order_id", "merchant", "total", "currency",
                "card_id", "card_label", "assignment_status", "assignment_source", "notes",
            ], [{
                "date": "2026-05-01",
                "platform": "walmart_ca",
                "order_id": "order-1",
                "merchant": "Walmart",
                "total": "11.30",
                "currency": "CAD",
                "card_id": "mc_1111",
                "card_label": "Mastercard 1111",
                "assignment_status": "confirmed",
                "assignment_source": "test",
                "notes": "receipt_payment=card ending 1111",
            }])
            (root / "data" / "payment_evidence.json").write_text(json.dumps({
                "evidence": [{
                    "platform": "walmart_ca",
                    "order_id": "order-1",
                    "source": "test_dom",
                    "payment_method": "card",
                    "card_last4": "1111",
                    "status": "confirmed",
                    "evidence_snippet": "Payment method Ending in 1111",
                }]
            }), encoding="utf-8")

            db_path = root / "data" / "expenses.sqlite"
            first = sync_sqlite.sync_all(root, db_path)
            second = sync_sqlite.sync_all(root, db_path)

            self.assertEqual(first["orders"], 1)
            self.assertEqual(second["orders"], 1)
            self.assertEqual(second["order_items"], 2)
            self.assertEqual(second["order_payments"], 1)
            self.assertEqual(second["payment_evidence"], 1)

            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT o.platform, o.order_id, COUNT(i.id)
                    FROM orders o
                    LEFT JOIN order_items i ON i.order_pk = o.id
                    GROUP BY o.id
                    """
                ).fetchall()
            self.assertEqual(rows, [("walmart_ca", "order-1", 2)])

    def test_direct_import_replaces_only_affected_order_items(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_cards(root)
            db_path = root / "data" / "expenses.sqlite"
            orders = [
                {
                    "date": "2026-05-01",
                    "platform": "walmart_ca",
                    "order_id": "order-1",
                    "merchant": "Walmart",
                    "category": "groceries",
                    "total": "11.30",
                    "currency": "CAD",
                },
                {
                    "date": "2026-05-02",
                    "platform": "uber_eats",
                    "order_id": "order-2",
                    "merchant": "Shop",
                    "category": "food_delivery",
                    "total": "20.00",
                    "currency": "CAD",
                },
            ]
            items = [
                {
                    "platform": "walmart_ca",
                    "order_id": "order-1",
                    "category": "groceries",
                    "product_name": "Milk",
                    "quantity": "1",
                    "price": "6.00",
                    "currency": "CAD",
                },
                {
                    "platform": "uber_eats",
                    "order_id": "order-2",
                    "category": "food_delivery",
                    "product_name": "Bowl",
                    "quantity": "1",
                    "price": "",
                    "currency": "CAD",
                },
            ]
            ledger_db.upsert_orders_and_items_from_rows(orders, items, root, db_path)

            updated_walmart_item = {
                "platform": "walmart_ca",
                "order_id": "order-1",
                "category": "groceries",
                "product_name": "Eggs",
                "quantity": "1",
                "price": "4.00",
                "currency": "CAD",
            }
            ledger_db.upsert_orders_and_items_from_rows(
                [orders[0]],
                [updated_walmart_item],
                root,
                db_path,
                order_keys={("walmart_ca", "order-1")},
            )

            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT o.platform, o.order_id, GROUP_CONCAT(i.product_name, '|')
                    FROM orders o
                    LEFT JOIN order_items i ON i.order_pk = o.id
                    GROUP BY o.id
                    ORDER BY o.platform, o.order_id
                    """
                ).fetchall()
                payments = conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0]

            self.assertEqual(rows, [
                ("uber_eats", "order-2", "Bowl"),
                ("walmart_ca", "order-1", "Eggs"),
            ])
            self.assertEqual(payments, 2)


if __name__ == "__main__":
    unittest.main()
