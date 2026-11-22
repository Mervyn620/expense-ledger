import csv
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "importers" / "fantuan.py"

spec = importlib.util.spec_from_file_location("import_fantuan", SCRIPT)
fantuan_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fantuan_import)


class FantuanImportTest(unittest.TestCase):
    @staticmethod
    def order(order_id, status, merchant, total):
        return {
            "date": "2026-08-19",
            "datetime": "2026-08-19 11:14",
            "platform": "fantuan",
            "order_id": order_id,
            "merchant": merchant,
            "status": status,
            "category": "food_delivery",
            "item": f"{merchant} item",
            "quantity": "1",
            "item_total": f"{total:.2f}",
            "tax": "0.00",
            "fees": "0.00",
            "tip": "0.00",
            "total": f"{total:.2f}",
            "currency": "CAD",
            "source_url": "https://www.fantuanorder.com/zh-CN/order-detail/example",
            "notes": "Sanitized fixture.",
            "goods": [{
                "name": f"{merchant} item",
                "quantity": "1",
                "price": f"{total:.2f}",
                "attributes": "",
            }],
        }

    @staticmethod
    def prepare_root(root):
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

    def test_active_scheduled_and_unknown_statuses_are_not_marked_delivered(self):
        for status,expected in [("等待商家接单","pending_acceptance"),("预约订单","scheduled"),("配送中","in_transit"),("备餐中","preparing"),("进行中","pending"),("2","pending"),("已完成","delivered")]:
            with self.subTest(status=status):
                transactions,items,_,_=fantuan_import.normalize_payload({"orders":[self.order("fixture",status,"Fixture",30.35)]})
                self.assertEqual(transactions[0]["fulfillment"],expected)
                self.assertEqual(items[0]["delivery_status"],expected)
                self.assertEqual(transactions[0]["total"],"30.35")

    def test_unpaid_active_order_does_not_enter_spending(self):
        transactions,items,_,_=fantuan_import.normalize_payload({"orders":[self.order("unpaid","待支付","Fixture",30.35)]})
        self.assertEqual(transactions,[])
        self.assertEqual(items,[])

    def test_active_order_updates_in_place_when_delivered(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.prepare_root(root)
            source=root/"data"/"fantuan_orders.json"
            order=self.order("same-order","等待商家接单","Fixture",30.35)
            source.write_text(json.dumps({"orders":[order]}),encoding="utf-8")
            fantuan_import.import_file(root)
            order["status"]="已完成"
            source.write_text(json.dumps({"orders":[order]}),encoding="utf-8")
            fantuan_import.import_file(root)
            with closing(sqlite3.connect(root/"data"/"expenses.sqlite")) as conn:
                self.assertEqual(conn.execute("SELECT order_id,fulfillment,total FROM orders").fetchall(),[("same-order","delivered",30.35)])
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0],1)

    def test_cancelled_statuses_are_terminal(self):
        for value in ("已取消", "订单取消", "Cancelled", "CANCELED"):
            with self.subTest(value=value):
                self.assertTrue(fantuan_import.is_cancelled_status(value))
        self.assertFalse(fantuan_import.is_cancelled_status("已完成"))
        self.assertFalse(fantuan_import.is_cancelled_status("配送中"))

    def test_cancelled_order_is_pruned_from_csv_sqlite_items_and_payment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.prepare_root(root)
            source = root / "data" / "fantuan_orders.json"
            completed = self.order("keep-order", "已完成", "Kept Shop", 19.97)
            later_cancelled = self.order(
                "cancel-order",
                "已完成",
                "Cancelled Shop",
                26.54,
            )
            source.write_text(
                json.dumps({"orders": [completed, later_cancelled]}),
                encoding="utf-8",
            )

            first = fantuan_import.import_file(root)
            later_cancelled["status"] = "已取消"
            source.write_text(
                json.dumps({"orders": [completed, later_cancelled]}),
                encoding="utf-8",
            )
            second = fantuan_import.import_file(root)

            self.assertEqual(first["orders"], 2)
            self.assertEqual(second["orders"], 1)
            self.assertEqual(second["excluded_cancelled"], 1)

            with (root / "data" / "transactions.csv").open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                transactions = list(csv.DictReader(handle))
            with (root / "data" / "delivery_items.csv").open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                items = list(csv.DictReader(handle))

            self.assertEqual(
                [(row["order_id"], row["total"]) for row in transactions],
                [("keep-order", "19.97")],
            )
            self.assertEqual(
                [row["order_id"] for row in items],
                ["keep-order"],
            )

            with closing(sqlite3.connect(root / "data" / "expenses.sqlite")) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT order_id FROM orders ORDER BY order_id"
                    ).fetchall(),
                    [("keep-order",)],
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
