import importlib.util
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "price_watch" / "client.py"
spec = importlib.util.spec_from_file_location("price_watch_client", SCRIPT)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def create_workspace(root, enabled=True):
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    (root / "config" / "price_watch.json").write_text(
        json.dumps({
            "enabled": enabled,
            "base_url": "http://127.0.0.1:9876" if enabled else "",
            "store_id": "store-123" if enabled else "",
            "store_name": "Test Store" if enabled else "",
            "store_query": "A1A 1A1" if enabled else "",
            "sync_path": "/api/v1/walmart/watchlist",
            "deals_path": "/api/v1/walmart/deals",
            "request_timeout_seconds": 5,
        }),
        encoding="utf-8",
    )
    with closing(sqlite3.connect(root / "data" / "expenses.sqlite")) as conn:
        conn.executescript(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                platform TEXT NOT NULL,
                order_id TEXT NOT NULL,
                date TEXT NOT NULL
            );
            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY,
                order_pk INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                product_link TEXT,
                price REAL,
                currency TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO orders VALUES (1, 'walmart_ca', 'private-order-1', '2026-07-01')"
        )
        conn.execute(
            "INSERT INTO orders VALUES (2, 'walmart_ca', 'private-order-2', '2026-07-20')"
        )
        conn.execute(
            """
            INSERT INTO order_items
            VALUES (
                1, 1, 'Great Value Eggs',
                'https://www.walmart.ca/en/ip/great-value-eggs/6000207000101?selectedSellerId=0',
                4.25, 'CAD'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO order_items
            VALUES (
                2, 2, 'Great Value Eggs',
                'https://www.walmart.ca/en/ip/great-value-eggs/6000207000101',
                3.95, 'CAD'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO order_items
            VALUES (
                3, 2, 'Unlinked Item', '',
                9.99, 'CAD'
            )
            """
        )
        conn.commit()


class PriceWatchClientTest(unittest.TestCase):
    def test_sync_sends_only_product_facts_and_is_incremental(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            calls = []

            def transport(method, url, token, payload, timeout):
                calls.append((method, url, token, payload, timeout))
                return {
                    "contract": client.CONTRACT,
                    "accepted": len(payload["products"]),
                }

            result = client.sync_products(
                root,
                environ={client.TOKEN_ENVIRONMENT_VARIABLE: "test-token"},
                transport=transport,
            )
            second = client.sync_products(
                root,
                environ={client.TOKEN_ENVIRONMENT_VARIABLE: "test-token"},
                transport=transport,
            )

            self.assertEqual(result["status"], "synced")
            self.assertEqual(result["sent"], 1)
            self.assertEqual(second["status"], "no_changes")
            self.assertEqual(len(calls), 1)
            product = calls[0][3]["products"][0]
            self.assertEqual(product["name"], "Great Value Eggs")
            self.assertEqual(product["first_purchased_on"], "2026-07-01")
            self.assertEqual(product["last_purchased_on"], "2026-07-20")
            self.assertEqual(product["last_purchase_price"], 3.95)
            self.assertNotIn("order_id", json.dumps(calls[0][3]))
            self.assertNotIn("private-order", json.dumps(calls[0][3]))

    def test_pull_accepts_only_local_purchased_products_on_sale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            products = client.read_local_products(root)
            product = products[0]

            def transport(method, url, token, payload, timeout):
                self.assertEqual(method, "GET")
                self.assertIn("store_id=store-123", url)
                return {
                    "contract": client.CONTRACT,
                    "generated_at": "2026-07-24T12:00:00+00:00",
                    "cursor": "next-1",
                    "deals": [
                        {
                            "product_key": product["product_key"],
                            "url": product["url"],
                            "regular_price": 4.25,
                            "current_price": 3.25,
                            "on_sale": True,
                            "checked_at": "2026-07-24T11:55:00+00:00",
                        },
                        {
                            "product_key": "walmart-ca:unknown",
                            "url": "https://www.walmart.ca/en/ip/unknown/6000207999999",
                            "regular_price": 10.0,
                            "current_price": 5.0,
                            "on_sale": True,
                        },
                        {
                            "product_key": product["product_key"],
                            "url": product["url"],
                            "regular_price": 4.25,
                            "current_price": 4.25,
                            "on_sale": False,
                        },
                    ],
                }

            result = client.pull_deals(
                root,
                environ={client.TOKEN_ENVIRONMENT_VARIABLE: "test-token"},
                transport=transport,
            )
            saved = json.loads(
                (root / "data" / "walmart_deals.json").read_text(encoding="utf-8")
            )

            self.assertEqual(result["status"], "received")
            self.assertEqual(result["deals"], 1)
            self.assertEqual(result["ignored"], 1)
            self.assertEqual(saved["deals"][0]["name"], "Great Value Eggs")
            self.assertEqual(saved["deals"][0]["discount_percent"], 23.5)
            self.assertEqual(saved["store_id"], "store-123")

    def test_local_result_merge_preserves_last_valid_deal_on_block(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            product = client.read_local_products(root)[0]
            completed = {
                "contract": client.CONTRACT,
                "generated_at": "2026-07-25T12:00:00+00:00",
                "cursor": "",
                "store_id": "store-123",
                "route": "local",
                "collector_status": "completed",
                "deals": [{
                    "product_key": product["product_key"],
                    "url": product["url"],
                    "regular_price": 4.25,
                    "current_price": 3.25,
                    "on_sale": True,
                    "checked_at": "2026-07-25T11:55:00+00:00",
                }],
                "checks": [{
                    "product_key": product["product_key"],
                    "url": product["url"],
                    "status": "ok",
                    "regular_price": 4.25,
                    "current_price": 3.25,
                    "on_sale": True,
                    "checked_at": "2026-07-25T11:55:00+00:00",
                }],
            }
            client.persist_deals_payload(
                root,
                completed,
                merge_existing=True,
            )
            blocked = {
                "contract": client.CONTRACT,
                "generated_at": "2026-07-25T12:05:00+00:00",
                "cursor": "",
                "store_id": "store-123",
                "route": "local",
                "collector_status": "blocked",
                "deals": [],
                "checks": [],
            }

            result = client.persist_deals_payload(
                root,
                blocked,
                merge_existing=True,
            )
            saved = json.loads(
                (root / "data" / "walmart_deals.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(result["deals"], 1)
            self.assertEqual(saved["collector"]["status"], "blocked")
            self.assertEqual(saved["collector"]["route"], "local")
            self.assertEqual(len(saved["deals"]), 1)

    def test_vps_pull_preserves_last_valid_deal_on_wrong_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            product = client.read_local_products(root)[0]
            completed = {
                "contract": client.CONTRACT,
                "generated_at": "2026-07-25T12:00:00+00:00",
                "cursor": "",
                "store_id": "store-123",
                "route": "direct",
                "collector_status": "completed",
                "checked": 50,
                "deals": [{
                    "product_key": product["product_key"],
                    "url": product["url"],
                    "regular_price": 4.25,
                    "current_price": 3.25,
                    "on_sale": True,
                    "checked_at": "2026-07-25T11:55:00+00:00",
                }],
                "checks": [],
            }
            client.persist_deals_payload(root, completed)

            def transport(method, url, token, payload, timeout):
                return {
                    "contract": client.CONTRACT,
                    "generated_at": "2026-07-25T12:05:00+00:00",
                    "cursor": "",
                    "store_id": "store-123",
                    "route": "direct",
                    "collector_status": "wrong_store",
                    "checked": 0,
                    "deals": [],
                    "checks": [],
                }

            result = client.pull_deals(
                root,
                environ={client.TOKEN_ENVIRONMENT_VARIABLE: "test-token"},
                transport=transport,
            )
            saved = json.loads(
                (root / "data" / "walmart_deals.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(result["collector_status"], "wrong_store")
            self.assertEqual(result["deals"], 1)
            self.assertEqual(saved["collector"]["status"], "wrong_store")
            self.assertEqual(saved["checked"], 0)
            self.assertEqual(len(saved["deals"]), 1)
            self.assertEqual(saved["deals"][0]["url"], product["url"])

    def test_sale_flag_without_price_comparison_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            product = client.read_local_products(root)[0]
            payload = {
                "contract": client.CONTRACT,
                "generated_at": "2026-07-25T12:00:00+00:00",
                "cursor": "",
                "store_id": "store-123",
                "route": "local",
                "collector_status": "partial",
                "deals": [{
                    "product_key": product["product_key"],
                    "url": product["url"],
                    "regular_price": None,
                    "current_price": 0.94,
                    "on_sale": True,
                    "checked_at": "2026-07-25T11:55:00+00:00",
                }],
                "checks": [],
            }

            result = client.persist_deals_payload(root, payload)

            self.assertEqual(result["deals"], 0)

    def test_my_items_name_is_matched_locally_and_reuses_saved_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            product = client.read_local_products(root)[0]
            payload = {
                "contract": client.CONTRACT,
                "generated_at": "2026-07-25T12:00:00+00:00",
                "cursor": "",
                "store_id": "store-123",
                "route": "direct",
                "collector_status": "completed",
                "checked": 50,
                "deals": [{
                    "source_name": product["name"],
                    "regular_price": 4.25,
                    "current_price": 3.25,
                    "on_sale": True,
                    "checked_at": "2026-07-25T11:55:00+00:00",
                }],
                "checks": [],
            }

            result = client.persist_deals_payload(root, payload)
            saved = json.loads(
                (root / "data" / "walmart_deals.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(result["deals"], 1)
            self.assertEqual(result["checked"], 50)
            self.assertEqual(saved["checked"], 50)
            self.assertEqual(saved["deals"][0]["name"], product["name"])
            self.assertEqual(saved["deals"][0]["url"], product["url"])

    def test_disabled_client_is_a_clean_no_op_without_a_token(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root, enabled=False)

            sync = client.sync_products(root, environ={})
            pull = client.pull_deals(root, environ={})

            self.assertEqual(sync["status"], "disabled")
            self.assertEqual(pull["status"], "disabled")

    def test_ssh_transport_needs_no_bearer_token_and_uses_argument_list(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            (root / "config" / "price_watch.json").write_text(
                json.dumps({
                    "enabled": True,
                    "transport": "ssh",
                    "ssh_host": "ledger_vps",
                    "remote_python": "/home/ledger/apps/expense-price-watch/venv/bin/python",
                    "remote_script": "/home/ledger/apps/expense-price-watch/vps_service.py",
                    "store_id": "3055",
                    "store_name": "MISSISSAUGA (SQUARE ONE), ON",
                    "store_query": "A1A 1A1",
                    "request_timeout_seconds": 30,
                }),
                encoding="utf-8",
            )
            response = json.dumps({
                "contract": client.CONTRACT,
                "accepted": 1,
                "created": 1,
            })
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=response, stderr=""
            )

            with patch.object(client.subprocess, "run", return_value=completed) as run:
                result = client.sync_products(root, environ={})

            self.assertEqual(result["status"], "synced")
            command = run.call_args.args[0]
            self.assertIsInstance(command, list)
            self.assertIn("BatchMode=yes", command)
            self.assertEqual(command[-1], "ingest")
            self.assertNotIn("Authorization", run.call_args.kwargs["input"])

    def test_ssh_transport_rejects_tampered_cursor_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            (root / "config" / "price_watch.json").write_text(
                json.dumps({
                    "enabled": True,
                    "transport": "ssh",
                    "ssh_host": "ledger_vps",
                    "remote_python": "/home/ledger/apps/expense-price-watch/venv/bin/python",
                    "remote_script": "/home/ledger/apps/expense-price-watch/vps_service.py",
                    "store_id": "3055",
                    "store_name": "MISSISSAUGA (SQUARE ONE), ON",
                    "store_query": "A1A 1A1",
                    "request_timeout_seconds": 30,
                }),
                encoding="utf-8",
            )
            state = root / client.RECEIVE_STATE_RELATIVE_PATH
            state.parent.mkdir(parents=True)
            state.write_text(
                json.dumps({"cursor": "ok; touch /tmp/not-allowed"}),
                encoding="utf-8",
            )

            with patch.object(client.subprocess, "run") as run:
                with self.assertRaises(client.PriceWatchError):
                    client.pull_deals(root, environ={})

            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
