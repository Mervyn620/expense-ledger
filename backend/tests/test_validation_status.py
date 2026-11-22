import csv
import io
import json
import sqlite3
import tempfile
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
import unittest

from scripts.ledger.schema import create_schema
from scripts.reporting import validate


TRANSACTION_FIELDS = [
    "date", "platform", "order_id", "merchant", "fulfillment", "category",
    "item", "quantity", "item_total", "tax", "fees", "tip", "total",
    "currency", "source_url", "notes",
]
ITEM_FIELDS = [
    "date", "platform", "order_id", "category", "product_name", "quantity",
    "price", "delivery_status", "product_link", "currency",
]


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_validation_fixture(root, duplicate_transaction=False):
    transaction = {
        "date": "2026-06-25",
        "platform": "walmart_ca",
        "order_id": "order-1",
        "merchant": "Walmart",
        "fulfillment": "delivery",
        "category": "groceries",
        "item": "Milk",
        "quantity": "1",
        "item_total": "10.00",
        "tax": "1.30",
        "fees": "0.00",
        "tip": "0.00",
        "total": "11.30",
        "currency": "CAD",
        "source_url": "",
        "notes": "",
    }
    rows = [transaction, dict(transaction)] if duplicate_transaction else [transaction]
    write_csv(root / "data" / "transactions.csv", TRANSACTION_FIELDS, rows)
    write_csv(root / "data" / "walmart_items.csv", ITEM_FIELDS, [{
        "date": "2026-06-25",
        "platform": "walmart_ca",
        "order_id": "order-1",
        "category": "groceries",
        "product_name": "Milk",
        "quantity": "1",
        "price": "10.00",
        "delivery_status": "",
        "product_link": "",
        "currency": "CAD",
    }])
    write_csv(
        root / "data" / "payment_assignments.csv",
        ["date", "platform", "order_id", "card_id", "assignment_status"],
        [{
            "date": "2026-06-25",
            "platform": "walmart_ca",
            "order_id": "order-1",
            "card_id": "mc_1111",
            "assignment_status": "confirmed",
        }],
    )

    db_path = root / "data" / "expenses.sqlite"
    with closing(sqlite3.connect(db_path)) as conn:
        create_schema(conn)
        conn.execute(
            """
            INSERT INTO cards (card_id, display_name, network, issuer, last4, kind)
            VALUES ('mc_1111', 'Mastercard 1111', 'Mastercard', '', '1111', 'credit_card')
            """
        )
        conn.execute(
            """
            INSERT INTO orders (
              platform, order_id, date, year, month, merchant, fulfillment,
              category, item_summary, quantity, item_total, tax, fees, tip,
              total, currency, source_url, notes, updated_at
            ) VALUES (
              'walmart_ca', 'order-1', '2026-06-25', 2026, '2026-06',
              'Walmart', 'delivery', 'groceries', 'Milk', '1', 10, 1.3, 0, 0,
              11.3, 'CAD', '', '', 'now'
            )
            """
        )
        order_pk = conn.execute(
            "SELECT id FROM orders WHERE order_id = 'order-1'"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO order_items (
              order_pk, line_key, category, product_name, quantity, price,
              delivery_status, product_link, currency, updated_at
            ) VALUES (?, 'milk', 'groceries', 'Milk', '1', 10, '', '', 'CAD', 'now')
            """,
            (order_pk,),
        )
        conn.execute(
            """
            INSERT INTO order_payments (
              order_pk, payment_index, card_id, payment_method, amount,
              currency, status, source, payment_channel, notes, updated_at
            ) VALUES (?, 1, 'mc_1111', 'Mastercard', 11.3, 'CAD',
                      'confirmed', 'test', 'online', '', 'now')
            """,
            (order_pk,),
        )
        conn.commit()


class ValidationStatusTest(unittest.TestCase):
    def run_validation(self, root):
        output = root / "exports" / "current" / "validation-status.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            exit_code = validate.main(root=root, status_output=output)
        return exit_code, json.loads(output.read_text(encoding="utf-8"))

    def test_success_writes_complete_structured_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_validation_fixture(root)

            exit_code, payload = self.run_validation(root)

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["metrics"]["transaction_duplicates"], 0)
            self.assertEqual(payload["metrics"]["item_file_duplicates"], 0)
            self.assertEqual(payload["metrics"]["sqlite_orphan_items"], 0)
            self.assertEqual(payload["metrics"]["bank_source_misclassifications"], 0)
            self.assertEqual(payload["metrics"]["walmart_bank_only_orders"], 0)
            self.assertEqual(payload["metrics"]["collector_bank_only_orders"], 0)
            self.assertEqual(payload["metrics"]["chequing_payment_rows"], 0)
            self.assertEqual(payload["metrics"]["missing_payment_channels"], 0)
            self.assertEqual(payload["metrics"]["invalid_inferred_payments"], 0)
            self.assertEqual(payload["failures"], [])

    def test_bank_only_order_cannot_use_merchant_platform(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_validation_fixture(root)
            with closing(sqlite3.connect(root / "data" / "expenses.sqlite")) as conn:
                conn.execute(
                    "UPDATE orders SET order_id = 'scotia-order-1' WHERE order_id = 'order-1'"
                )
                conn.commit()

            exit_code, payload = self.run_validation(root)

            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["metrics"]["bank_source_misclassifications"], 1)
            self.assertTrue(
                any(
                    "bank-only orders assigned to merchant platforms" in failure
                    for failure in payload["failures"]
                )
            )

    def test_unmatched_walmart_bank_charge_cannot_become_an_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_validation_fixture(root)
            with closing(sqlite3.connect(root / "data" / "expenses.sqlite")) as conn:
                conn.execute(
                    """
                    UPDATE orders
                    SET platform = 'scotiabank', order_id = 'scotia-order-1'
                    WHERE order_id = 'order-1'
                    """
                )
                conn.commit()

            exit_code, payload = self.run_validation(root)

            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["metrics"]["bank_source_misclassifications"], 0)
            self.assertEqual(payload["metrics"]["walmart_bank_only_orders"], 1)
            self.assertTrue(
                any(
                    "unmatched Walmart bank charges imported as orders" in failure
                    for failure in payload["failures"]
                )
            )

    def test_chequing_account_cannot_be_used_as_payment_instrument(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_validation_fixture(root)
            with closing(sqlite3.connect(root / "data" / "expenses.sqlite")) as conn:
                conn.execute(
                    """
                    INSERT INTO cards (
                      card_id, display_name, network, issuer, last4, kind
                    ) VALUES (
                      'scotia_chequing_3333', 'Scotiabank Chequing 3333',
                      'Bank account', 'Scotiabank', '3333', 'bank_account'
                    )
                    """
                )
                conn.execute(
                    """
                    UPDATE order_payments
                    SET card_id = 'scotia_chequing_3333'
                    """
                )
                conn.commit()

            exit_code, payload = self.run_validation(root)

            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["metrics"]["chequing_payment_rows"], 1)

    def test_failure_replaces_previous_green_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_validation_fixture(root, duplicate_transaction=True)
            output = root / "exports" / "current" / "validation-status.json"
            output.parent.mkdir(parents=True)
            output.write_text(
                json.dumps({"status": "passed", "metrics": {}}),
                encoding="utf-8",
            )

            exit_code, payload = self.run_validation(root)

            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["status"], "failed")
            self.assertGreater(payload["metrics"]["transaction_duplicates"], 0)
            self.assertTrue(payload["failures"])


if __name__ == "__main__":
    unittest.main()
