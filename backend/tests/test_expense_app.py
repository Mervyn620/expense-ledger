import json
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient

from app.jobs import JobManager, parse_metrics
from app.repository import LedgerRepository
from scripts.ledger.schema import create_schema


def create_test_ledger(root):
    db_path = root / "data" / "expenses.sqlite"
    db_path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db_path)) as conn:
        create_schema(conn)
        conn.execute(
            """
            INSERT INTO cards (card_id, display_name, network, issuer, last4, kind)
            VALUES ('mc_1111', 'Mastercard 1111', 'Mastercard', '', '1111', 'credit_card')
            """
        )
        orders = [
            (
                "walmart_ca", "order-1", "2026-05-20", 2026, "2026-05",
                "Walmart", "delivery", "groceries", "Milk", "1", 10, 1.3,
                0, 0, 11.3, "CAD", "", "", "2026-05-20T12:00:00+00:00",
            ),
            (
                "fantuan", "order-2", "2026-06-25", 2026, "2026-06",
                "BB.Q Chicken", "delivery", "food_delivery", "Chicken", "1",
                30, 3.9, 2, 2.91, 38.81, "CAD", "", "",
                "2026-06-25T12:00:00+00:00",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO orders (
              platform, order_id, date, year, month, merchant, fulfillment,
              category, item_summary, quantity, item_total, tax, fees, tip,
              total, currency, source_url, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            orders,
        )
        first_pk = conn.execute(
            "SELECT id FROM orders WHERE order_id = 'order-1'"
        ).fetchone()[0]
        second_pk = conn.execute(
            "SELECT id FROM orders WHERE order_id = 'order-2'"
        ).fetchone()[0]
        conn.executemany(
            """
            INSERT INTO order_items (
              order_pk, line_key, category, product_name, quantity, price,
              delivery_status, product_link, currency, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (first_pk, "milk", "groceries", "Organic Oat Beverage", "1", 10, "", "", "CAD", "now"),
                (second_pk, "chicken", "food_delivery", "Chicken", "1", 30, "", "", "CAD", "now"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO order_payments (
              order_pk, payment_index, card_id, payment_method, amount,
              currency, status, source, notes, updated_at
            ) VALUES (?, 1, 'mc_1111', 'Mastercard', ?, 'CAD', 'confirmed', 'test', '', 'now')
            """,
            [(first_pk, 11.3), (second_pk, 38.81)],
        )
        conn.commit()
    return db_path


class ExpenseAppRepositoryTest(unittest.TestCase):
    def test_overview_uses_latest_month_and_returns_breakdowns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_test_ledger(root)
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            payload = repository.overview("latest")

            self.assertEqual(payload["selected_month"], "2026-06")
            self.assertEqual(payload["summary"]["order_count"], 1)
            self.assertEqual(payload["summary"]["item_count"], 1)
            self.assertEqual(payload["summary"]["total"], 38.81)
            self.assertEqual(payload["summary"]["review_count"], 0)
            self.assertEqual(payload["platforms"][0]["label"], "饭团")
            uber = next(
                row for row in payload["platforms"] if row["key"] == "uber_eats"
            )
            self.assertEqual(uber["orders"], 0)
            self.assertEqual(uber["total"], 0)
            self.assertEqual(payload["cards"][0]["label"], "Mastercard 1111")

    def test_order_search_and_detail_include_items(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_test_ledger(root),
                current_exports=root / "exports" / "current",
            )

            rows = repository.orders(month="all", query="chicken")
            detail = repository.order_detail(rows["rows"][0]["order_pk"])

            self.assertEqual(rows["total_count"], 1)
            self.assertEqual(rows["rows"][0]["fulfillment"], "delivery")
            self.assertEqual(detail["order"]["merchant"], "BB.Q Chicken")
            self.assertEqual(detail["items"][0]["product_name"], "Chicken")

            product_rows = repository.orders(month="all", query="oat beverage")
            self.assertEqual(product_rows["total_count"], 1)
            self.assertEqual(product_rows["rows"][0]["merchant"], "Walmart")

    def test_reconciliation_review_returns_only_held_platform_charges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reconciliation_csv = root / "exports" / "reconciliation" / "scotia.csv"
            reconciliation_csv.parent.mkdir(parents=True)
            reconciliation_csv.write_text(
                "date,description,amount,direction,merchant,category,detected_platform,match_status,match_method,matched_order_id,matched_order_date,matched_order_total,notes\n"
                "2026-07-01,UBER *EATS,24.51,debit,Uber Eats,food_delivery,uber_eats,unmatched_platform_review,platform_no_candidate,,,,Needs review\n"
                "2026-07-02,WALMART,18.00,debit,Walmart,groceries,walmart_ca,matched,exact_total,order-1,2026-07-02,18.00,\n",
                encoding="utf-8",
            )
            repository = LedgerRepository(
                db_path=create_test_ledger(root),
                current_exports=root / "exports" / "current",
                reconciliation_csv=reconciliation_csv,
            )

            rows = repository.reconciliation_review()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["detected_platform"], "uber_eats")
            self.assertEqual(rows[0]["amount"], 24.51)

    def test_cash_withdrawals_exclude_e_transfers_and_do_not_change_overview(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_test_ledger(root)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO bank_accounts (
                      account_id, institution, display_name, last4, updated_at
                    )
                    VALUES (
                      'scotia_chequing', 'Scotiabank',
                      'Scotiabank Chequing', '3333', 'now'
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO bank_transactions (
                      transaction_id, account_id, date, description, amount,
                      direction, currency, source, updated_at
                    )
                    VALUES (?, 'scotia_chequing', ?, ?, ?, 'debit', 'CAD', 'test', 'now')
                    """,
                    [
                        (
                            "cash-1",
                            "2026-06-26",
                            "ATM Cash Withdrawal",
                            60,
                        ),
                        (
                            "transfer-1",
                            "2026-06-27",
                            "Withdrawal Free Interac E-Transfer",
                            910,
                        ),
                    ],
                )
                cash_pk = conn.execute(
                    "SELECT id FROM bank_transactions WHERE transaction_id = 'cash-1'"
                ).fetchone()[0]
                conn.commit()
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )

            before_total = repository.overview("2026-06")["summary"]["total"]
            payload = repository.cash_withdrawals("2026-06")
            saved = repository.update_cash_withdrawal_note(
                cash_pk,
                "  理发   现金  ",
            )
            after_save = repository.cash_withdrawals("2026-06")
            after_total = repository.overview("2026-06")["summary"]["total"]

            self.assertEqual(payload["summary"]["withdrawal_count"], 1)
            self.assertEqual(payload["summary"]["total"], 60)
            self.assertEqual(payload["rows"][0]["bank_transaction_pk"], cash_pk)
            self.assertEqual(saved["note"], "理发 现金")
            self.assertEqual(after_save["summary"]["noted_count"], 1)
            self.assertEqual(after_save["rows"][0]["note"], "理发 现金")
            self.assertEqual(before_total, 38.81)
            self.assertEqual(after_total, before_total)

            repository.update_cash_withdrawal_note(cash_pk, "")
            self.assertEqual(
                repository.cash_withdrawals("2026-06")["summary"]["noted_count"],
                0,
            )


class ExpenseAppJobsTest(unittest.TestCase):
    def test_parse_metrics_keeps_numeric_validation_fields(self):
        metrics = parse_metrics(
            "transaction_duplicates=0\nsqlite_total=1251.38\nignored line"
        )
        self.assertEqual(metrics["transaction_duplicates"], 0)
        self.assertEqual(metrics["sqlite_total"], 1251.38)

    def test_scotia_action_waits_for_login_before_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = JobManager(root=root, python_executable=sys.executable)
            captured = []

            def capture(kind, label, command, **kwargs):
                captured.append((kind, label, command))
                return {"kind": kind, "label": label}

            with patch.object(manager, "_start", side_effect=capture):
                manager.start_refresh("scotia_login")
                manager.start_refresh("scotia")

            login = captured[0]
            reconcile = captured[1]
            self.assertEqual(login[0], "refresh:scotia_login")
            self.assertTrue(login[2][login[2].index('-File') + 1].endswith("prepare-scotia-login.ps1"))
            self.assertNotIn("-ReconcileScotia", login[2])
            self.assertEqual(reconcile[0], "refresh:scotia")
            self.assertTrue(reconcile[2][reconcile[2].index('-File') + 1].endswith("login-and-reconcile-scotia.ps1"))
            self.assertNotIn("-ReconcileScotia", reconcile[2])
            self.assertNotIn("-CollectFromBrowser", reconcile[2])

    def test_store_refresh_locks_resolved_collector_backend_before_start(self):
        class BackendManager:
            def resolve(self, mode, force=True):
                self.requested = mode
                self.force = force
                return {
                    "requested_mode": mode or "auto",
                    "resolved_backend": "vps",
                }

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backend = BackendManager()
            manager = JobManager(
                root=root,
                python_executable=sys.executable,
                collector_backend_manager=backend,
            )
            captured = []

            def capture(kind, label, command, **kwargs):
                captured.append((kind, label, command, kwargs))
                return {"kind": kind, "label": label}

            with patch.object(manager, "_start", side_effect=capture):
                manager.start_refresh("stores", collector_backend="auto")

            kind, label, command, options = captured[0]
            self.assertEqual(kind, "refresh:stores")
            self.assertIn("VPS", label)
            self.assertEqual(
                command[-3:],
                ["-CollectFromBrowser", "-CollectorBackend", "vps"],
            )
            self.assertEqual(backend.requested, "auto")
            self.assertTrue(backend.force)
            self.assertEqual(manager._vps_refresh_timeout(), 660)
            self.assertEqual(options["timeout_seconds"], 660)

    def test_store_refresh_can_target_only_selected_failed_platforms(self):
        class BackendManager:
            def resolve(self, mode, force=True):
                return {"resolved_backend": "vps"}

            def status(self, force=False, selected_mode=None):
                return {
                    "backends": {
                        "vps": {"capabilities": ["collect_bundle_v2"]},
                    },
                }

        with tempfile.TemporaryDirectory() as temp:
            manager = JobManager(
                root=Path(temp),
                python_executable=sys.executable,
                collector_backend_manager=BackendManager(),
            )
            with patch.object(manager, "_start") as start:
                manager.start_refresh(
                    "stores",
                    collector_backend="vps",
                    collector_platforms=["the_ordinary_ca"],
                )

            command = start.call_args.args[2]
            self.assertEqual(
                command[-4:],
                [
                    "-CollectorBackend",
                    "vps",
                    "-CollectorPlatforms",
                    "the_ordinary_ca",
                ],
            )

    def test_store_recovery_marks_the_vps_attempt_as_continuation(self):
        class BackendManager:
            def resolve(self, mode, force=True):
                return {"resolved_backend": "vps"}

            def status(self, force=False, selected_mode=None):
                return {
                    "backends": {
                        "vps": {"capabilities": ["collect_bundle_v2"]},
                    },
                }

        with tempfile.TemporaryDirectory() as temp:
            manager = JobManager(
                root=Path(temp),
                python_executable=sys.executable,
                collector_backend_manager=BackendManager(),
            )
            with patch.object(manager, "_start") as start:
                manager.start_refresh(
                    "stores",
                    collector_backend="vps",
                    collector_platforms=["amazon_ca"],
                    collector_recovery=True,
                )

            command = start.call_args.args[2]
            self.assertIn("-ContinueCollectorRecovery", command)

    def test_local_only_platform_ignores_selected_vps_for_ordinary(self):
        class BackendManager:
            def __init__(self, config_path):
                self.config_path = config_path

            def resolve(self, mode, force=True):
                return {"resolved_backend": mode or "vps"}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "config" / "collector_backends.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({
                "local_only_platforms": ["the_ordinary_ca"],
            }), encoding="utf-8")
            manager = JobManager(
                root=root,
                python_executable=sys.executable,
                collector_backend_manager=BackendManager(config_path),
            )
            with patch.object(manager, "_start") as start:
                manager.start_refresh(
                    "stores",
                    collector_backend="vps",
                    collector_platforms=["the_ordinary_ca"],
                )

            command = start.call_args.args[2]
            self.assertIn("-CollectorBackend", command)
            self.assertEqual(command[command.index("-CollectorBackend") + 1], "local")
            self.assertIn("采集 The Ordinary · 本地", start.call_args.args[1])

    def test_store_refresh_rejects_selective_retry_on_legacy_vps(self):
        class BackendManager:
            def resolve(self, mode, force=True):
                return {"resolved_backend": "vps"}

            def status(self, force=False, selected_mode=None):
                return {
                    "backends": {
                        "vps": {"capabilities": ["collect_bundle_v1"]},
                    },
                }

        with tempfile.TemporaryDirectory() as temp:
            manager = JobManager(
                root=Path(temp),
                python_executable=sys.executable,
                collector_backend_manager=BackendManager(),
            )

            with self.assertRaisesRegex(RuntimeError, "collect_bundle_v2"):
                manager.start_refresh(
                    "stores",
                    collector_backend="vps",
                    collector_platforms=["amazon_ca"],
                )

    def test_store_refresh_rejects_unknown_or_duplicate_platforms(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = JobManager(root=Path(temp), python_executable=sys.executable)
            with self.assertRaises(ValueError):
                manager.start_refresh(
                    "stores",
                    collector_platforms=["unknown"],
                )
            with self.assertRaises(ValueError):
                manager.start_refresh(
                    "stores",
                    collector_platforms=["amazon_ca", "amazon_ca"],
                )

    def test_login_operation_blocks_refresh_and_other_login_operations(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = JobManager(root=Path(temp), python_executable=sys.executable)
            manager._login_operation_active = True
            with self.assertRaises(RuntimeError):
                manager._start("validate", "validate", [sys.executable, "-V"])
            with self.assertRaises(RuntimeError):
                manager._begin_login_operation()

    def test_login_operation_flag_is_released_after_subprocess_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = JobManager(root=Path(temp), python_executable=sys.executable)
            with (
                patch.object(manager, "_run_vps_login_action", side_effect=RuntimeError("failed")),
                self.assertRaises(RuntimeError),
            ):
                manager.start_vps_login_session("amazon_ca")
            self.assertFalse(manager._login_operation_active)

    def test_verify_and_retry_reserves_job_before_releasing_login_lock(self):
        class BackendManager:
            def resolve(self, mode, force=True):
                return {"resolved_backend": "vps"}

            def status(self, force=False, selected_mode=None):
                return {
                    "backends": {
                        "vps": {"capabilities": ["collect_bundle_v2"]},
                    },
                }

        with tempfile.TemporaryDirectory() as temp:
            manager = JobManager(
                root=Path(temp),
                python_executable=sys.executable,
                collector_backend_manager=BackendManager(),
            )
            with (
                patch.object(manager, "_run_vps_login_action", return_value={
                    "remote": {"status": "authenticated"},
                }),
                patch.object(manager, "_start", return_value={
                    "id": "retry",
                    "status": "queued",
                }) as start,
            ):
                result = manager.verify_vps_login_and_start_refresh("amazon_ca")

            self.assertEqual(result["id"], "retry")
            self.assertTrue(start.call_args.kwargs["_allow_login_operation"])
            self.assertFalse(manager._login_operation_active)

    def test_scotia_login_messages_stop_for_manual_verification(self):
        self.assertEqual(
            JobManager._scotia_login_message(
                {"scotia_login_status": "verification_required"}
            ),
            "Scotia 等待你手动完成二次验证",
        )

    def test_price_watch_job_uses_bounded_on_demand_runner(self):
        class BackendManager:
            def resolve(self, mode, force=True):
                self.requested = mode
                self.force = force
                return {"resolved_backend": "local"}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "scripts" / "price_watch" / "on_demand.py"
            script.parent.mkdir(parents=True)
            script.write_text("raise SystemExit(0)\n", encoding="utf-8")
            backend = BackendManager()
            manager = JobManager(
                root=root,
                python_executable=sys.executable,
                collector_backend_manager=backend,
            )

            with patch.object(manager, "_start") as start:
                manager.start_price_watch()

            kind, label, command = start.call_args.args
            self.assertEqual(kind, "price_watch")
            self.assertEqual(label, "检查 Walmart 折扣 · 本地")
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(Path(command[1]), script)
            self.assertEqual(
                command[2:],
                ["--root", str(root), "--backend", "local"],
            )
            self.assertFalse(hasattr(backend, "requested"))
            self.assertEqual(
                start.call_args.kwargs["timeout_seconds"],
                180,
            )

    def test_price_watch_messages_distinguish_block_and_success(self):
        self.assertEqual(
            JobManager._price_watch_message(
                {"price_watch_collector_status": "blocked"},
                "failed",
            ),
            "Walmart 阻止了本次检查，请稍后再试",
        )
        self.assertEqual(
            JobManager._price_watch_message(
                {
                    "price_watch_collector_status": "completed",
                    "price_watch_checks": 74,
                    "price_watch_deals": 3,
                },
                "passed",
            ),
            "Walmart 检查完成：74 个商品，3 个折扣",
        )

    def test_validation_job_persists_sanitized_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "scripts" / "reporting" / "validate.py"
            script.parent.mkdir(parents=True)
            script.write_text(
                "print('transaction_duplicates=0')\n"
                "print('sqlite_orphan_items=0')\n",
                encoding="utf-8",
            )
            status_path = root / "exports" / "current" / "validation-status.json"
            manager = JobManager(
                root=root,
                python_executable=sys.executable,
                validation_status=status_path,
            )
            original_write = manager._write_validation_status

            def delayed_write(job):
                time.sleep(0.2)
                original_write(job)

            manager._write_validation_status = delayed_write

            started = manager.start_validation()
            deadline = time.time() + 5
            job = manager.get(started["id"])
            while job["status"] in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.05)
                job = manager.get(started["id"])

            self.assertEqual(job["status"], "passed")
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["metrics"]["transaction_duplicates"], 0)

    def test_timed_out_job_is_failed_and_process_is_reaped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "scripts" / "reporting" / "validate.py"
            script.parent.mkdir(parents=True)
            script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            manager = JobManager(
                root=root,
                python_executable=sys.executable,
                validation_status=root / "validation-status.json",
                timeout_seconds=0.1,
            )

            started = manager.start_validation()
            deadline = time.time() + 5
            job = manager.get(started["id"])
            while job["status"] in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.05)
                job = manager.get(started["id"])

            self.assertEqual(job["status"], "failed")
            self.assertIn("safety timeout", job["message"])
            self.assertEqual(manager._processes, {})

    def test_refresh_job_preserves_partial_workflow_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "partial_refresh.py"
            script.write_text(
                "print('workflow_status=partial')\n"
                "print('collection_status=partial')\n",
                encoding="utf-8",
            )
            manager = JobManager(root=root, python_executable=sys.executable)

            started = manager._start(
                "refresh:stores",
                "VPS refresh",
                [sys.executable, str(script)],
            )
            deadline = time.time() + 5
            job = manager.get(started["id"])
            while job["status"] in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.05)
                job = manager.get(started["id"])

            self.assertEqual(job["status"], "partial")
            self.assertEqual(job["exit_code"], 0)
            self.assertIn("上次有效数据", job["message"])

    def test_refresh_job_distinguishes_non_collection_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "partial_refresh.py"
            script.write_text(
                "print('workflow_status=partial')\n"
                "print('collection_status=passed')\n",
                encoding="utf-8",
            )
            manager = JobManager(root=root, python_executable=sys.executable)

            started = manager._start(
                "refresh:stores",
                "VPS refresh",
                [sys.executable, str(script)],
            )
            deadline = time.time() + 5
            job = manager.get(started["id"])
            while job["status"] in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.05)
                job = manager.get(started["id"])

            self.assertEqual(job["status"], "partial")
            self.assertIn("非采集警告", job["message"])
            self.assertNotIn("上次有效数据", job["message"])

    def test_timed_out_refresh_replaces_stale_running_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "slow_refresh.py"
            script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            state_path = root / "exports" / "current" / "run-state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "status": "running",
                "started_at": "2026-08-08T13:01:41-04:00",
                "completed_at": None,
                "duration_seconds": 0,
                "collector_backend_requested": "vps",
                "collector_backend_resolved": "",
            }), encoding="utf-8")
            manager = JobManager(
                root=root,
                python_executable=sys.executable,
                timeout_seconds=0.1,
            )

            started = manager._start(
                "refresh:stores",
                "VPS refresh",
                [sys.executable, str(script)],
            )
            deadline = time.time() + 5
            job = manager.get(started["id"])
            while job["status"] in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.05)
                job = manager.get(started["id"])

            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(job["status"], "failed")
            self.assertEqual(payload["status"], "failed")
            self.assertIsNotNone(payload["completed_at"])
            self.assertGreaterEqual(payload["duration_seconds"], 0)
            self.assertEqual(payload["started_at"], job["started_at"])
            self.assertIn("safety timeout", payload["message"])

    def test_failed_refresh_replaces_previous_success_without_old_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_path = root / 'exports/current/run-state.json'
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                'status': 'passed', 'started_at': '2026-09-11T00:00:00+00:00',
                'collection_run_id': 'old-run',
            }), encoding='utf-8')
            manager = JobManager(root=root)
            manager._finalize_failed_refresh_state('2026-09-12T00:00:00+00:00', {
                'completed_at': '2026-09-12T00:00:45+00:00', 'message': 'Preparation timed out',
            })
            payload = json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(payload['status'], 'failed')
            self.assertEqual(payload['duration_seconds'], 45)
            self.assertEqual(payload['started_at'], '2026-09-12T00:00:00+00:00')
            self.assertNotIn('collection_run_id', payload)

    def test_failed_refresh_preserves_current_script_failure_and_newer_run(self):
        for status in ('failed', 'passed'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                state_path = root / 'exports/current/run-state.json'
                state_path.parent.mkdir(parents=True)
                expected = {'status': status, 'started_at': '2026-09-12T00:00:01+00:00',
                            'message': 'Specific script result'}
                state_path.write_text(json.dumps(expected), encoding='utf-8')
                JobManager(root=root)._finalize_failed_refresh_state(
                    '2026-09-12T00:00:00+00:00', {'message': 'Generic failure'})
                self.assertEqual(json.loads(state_path.read_text(encoding='utf-8')), expected)

    def test_late_safety_failure_overrides_intermediate_green_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "scripts" / "maintenance" / "validate_all.py"
            script.parent.mkdir(parents=True)
            status_path = root / "validation-status.json"
            script.write_text(
                "import json\n"
                "from pathlib import Path\n"
                f"path = Path({str(status_path)!r})\n"
                "path.write_text(json.dumps({"
                "'status': 'passed', 'completed_at': 'now', 'exit_code': 0, "
                "'metrics': {'transaction_duplicates': 0}, "
                "'message': 'intermediate pass'}), encoding='utf-8')\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            manager = JobManager(
                root=root,
                python_executable=sys.executable,
                validation_status=status_path,
            )

            started = manager.start_validation()
            deadline = time.time() + 5
            job = manager.get(started["id"])
            while job["status"] in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.05)
                job = manager.get(started["id"])

            self.assertEqual(job["status"], "failed")
            self.assertNotEqual(job["message"], "intermediate pass")

    def test_aggregate_validation_keeps_safety_and_ledger_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "scripts" / "maintenance" / "validate_all.py"
            script.parent.mkdir(parents=True)
            status_path = root / "validation-status.json"
            script.write_text(
                "import json\n"
                "from pathlib import Path\n"
                f"path = Path({str(status_path)!r})\n"
                "path.write_text(json.dumps({"
                "'status': 'passed', 'completed_at': 'now', 'exit_code': 0, "
                "'metrics': {'transaction_duplicates': 0}, "
                "'message': 'passed'}), encoding='utf-8')\n"
                "print('safety_backup_valid=1')\n",
                encoding="utf-8",
            )
            manager = JobManager(
                root=root,
                python_executable=sys.executable,
                validation_status=status_path,
            )

            started = manager.start_validation()
            deadline = time.time() + 5
            job = manager.get(started["id"])
            while job["status"] in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.05)
                job = manager.get(started["id"])

            self.assertEqual(job["status"], "passed")
            self.assertEqual(job["metrics"]["safety_backup_valid"], 1)
            self.assertEqual(job["metrics"]["transaction_duplicates"], 0)


class ExpenseAppApiTest(unittest.TestCase):
    def test_collector_recovery_retries_failed_platform_and_routes_login(self):
        from app import main

        recovery = {
            "status": "partial",
            "platforms": [
                {
                    "id": "walmart_ca",
                    "status": "failed",
                    "retryable": True,
                    "login_required": False,
                },
                {
                    "id": "the_ordinary_ca",
                    "status": "login_required",
                    "retryable": False,
                    "login_required": True,
                },
            ],
        }
        with (
            patch.object(main, "collector_recovery_status", return_value=recovery),
            patch.object(
                main.jobs,
                "start_refresh",
                return_value={"id": "retry-1", "status": "queued"},
            ) as refresh,
            patch.object(
                main.jobs,
                "start_vps_login_session",
                return_value={
                    "status": "ready",
                    "platform": "the_ordinary_ca",
                    "url": "http://127.0.0.1:6088/vnc.html",
                },
            ) as login,
            patch.object(
                main.jobs,
                "verify_vps_login_and_start_refresh",
                return_value={"id": "retry-2", "status": "queued"},
            ) as verify,
        ):
            client = TestClient(main.app, base_url="http://127.0.0.1")
            retried = client.post(
                "/api/jobs/retry-collectors",
                json={"platform": "walmart_ca"},
            )
            login_started = client.post(
                "/api/vps-login-session",
                json={"platform": "the_ordinary_ca"},
            )
            login_retry = client.post(
                "/api/jobs/retry-collectors",
                json={"platform": "the_ordinary_ca"},
            )
            verified = client.post(
                "/api/vps-login-session/verify",
                json={"platform": "the_ordinary_ca"},
            )

        self.assertEqual(retried.status_code, 202)
        self.assertEqual(refresh.call_count, 1)
        refresh.assert_any_call(
            "stores",
            collector_backend="vps",
            collector_platforms=["walmart_ca"],
            collector_recovery=True,
        )
        self.assertEqual(login_started.status_code, 200)
        login.assert_called_once_with("the_ordinary_ca")
        self.assertEqual(login_retry.status_code, 409)
        self.assertEqual(verified.status_code, 202)
        verify.assert_called_once_with("the_ordinary_ca")

    def test_api_serves_overview_orders_and_blocks_foreign_actions(self):
        from app import main

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = LedgerRepository(
                db_path=create_test_ledger(root),
                current_exports=root / "exports" / "current",
            )
            with patch.object(main, "repository", repository):
                client = TestClient(main.app, base_url="http://127.0.0.1")
                index = client.get("/")
                health = client.get("/api/health")
                overview = client.get("/api/overview?month=2026-06")
                orders = client.get("/api/orders?month=2026-06")
                blocked = client.post(
                    "/api/jobs/validate",
                    headers={"Origin": "https://example.com"},
                    json={},
                )
                bad_host = client.get(
                    "/api/health",
                    headers={"Host": "attacker.example"},
                )

            self.assertEqual(index.headers["cache-control"], "no-cache")
            self.assertEqual(index.headers["x-frame-options"], "DENY")
            self.assertIn(
                "default-src 'self'",
                index.headers["content-security-policy"],
            )
            self.assertNotIn('id="refreshStoresButton"', index.text)
            self.assertNotIn('id="refreshMenuButton"', index.text)
            self.assertEqual(index.text.count("data-refresh-mode="), 1)
            self.assertEqual(index.text.count("data-collector-platform="), 0)
            self.assertIn('id="billRefreshButton"', index.text)
            self.assertIn('id="billRefreshDialog"', index.text)
            self.assertEqual(index.text.count('name="platform"'), 6)
            self.assertNotIn("更新 Amazon", index.text)
            self.assertNotIn("更新 The Ordinary", index.text)
            self.assertIn("不访问浏览器", index.text)
            self.assertIn("登录并对账 Scotia", index.text)
            self.assertIn("完成登录后自动采集并对账，无需再次点击", index.text)
            self.assertEqual(index.text.count("data-scotia-action"), 1)
            self.assertEqual(health.json()["app"], "expense-ledger")
            self.assertEqual(health.json()["version"], "1.21.0")
            self.assertEqual(Path(health.json()["workspace"]).resolve(), Path(__file__).resolve().parents[1])
            self.assertEqual(len(health.json()["workspace_id"]), 12)
            self.assertIsInstance(health.json()["pid"], int)
            self.assertEqual(overview.status_code, 200)
            self.assertEqual(overview.json()["summary"]["total"], 38.81)
            self.assertEqual(overview.json()["reconciliation_review"], [])
            self.assertEqual(orders.json()["total_count"], 1)
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(bad_host.status_code, 400)

    def test_cash_note_api_only_updates_detected_cash_withdrawals(self):
        from app import main

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_test_ledger(root)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO bank_accounts (
                      account_id, institution, display_name, last4, updated_at
                    )
                    VALUES ('account', 'Scotiabank', 'Chequing', '3333', 'now')
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO bank_transactions (
                      transaction_id, account_id, date, description, amount,
                      direction, currency, source, updated_at
                    )
                    VALUES (?, 'account', '2026-06-26', ?, ?, 'debit', 'CAD', 'test', 'now')
                    """,
                    [
                        ("cash", "ATM Withdrawal", 60),
                        ("transfer", "Withdrawal Free Interac E-Transfer", 100),
                    ],
                )
                ids = dict(
                    conn.execute(
                        "SELECT transaction_id, id FROM bank_transactions"
                    ).fetchall()
                )
                conn.commit()
            repository = LedgerRepository(
                db_path=db_path,
                current_exports=root / "exports" / "current",
            )
            with patch.object(main, "repository", repository):
                client = TestClient(main.app, base_url="http://127.0.0.1")
                listed = client.get("/api/cash-withdrawals?month=2026-06")
                saved = client.patch(
                    f"/api/cash-withdrawals/{ids['cash']}",
                    json={"note": "理发"},
                )
                rejected = client.patch(
                    f"/api/cash-withdrawals/{ids['transfer']}",
                    json={"note": "不应保存"},
                )
                overview = client.get("/api/overview?month=2026-06")

            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()["summary"]["withdrawal_count"], 1)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["note"], "理发")
            self.assertEqual(rejected.status_code, 404)
            self.assertEqual(overview.json()["summary"]["total"], 38.81)

    def test_price_watch_opens_separate_page_and_starts_local_job(self):
        from app import main

        with patch.object(
            main.jobs,
            "start_price_watch",
            return_value={
                "id": "price-job-1",
                "kind": "price_watch",
                "status": "queued",
            },
        ):
            client = TestClient(main.app, base_url="http://127.0.0.1")
            page = client.get("/price-watch")
            started = client.post("/api/jobs/price-watch", json={})
            blocked = client.post(
                "/api/jobs/price-watch",
                headers={"Origin": "https://example.com"},
                json={},
            )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["cache-control"], "no-cache")
        self.assertIn("查折扣固定使用本机专用浏览器", page.text)
        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json()["kind"], "price_watch")
        self.assertEqual(blocked.status_code, 403)

    def test_price_watch_status_exposes_counts_not_product_records(self):
        from app import main

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            status_path = root / ".codex" / "price-watch-on-demand-status.json"
            deals_path = root / "data" / "walmart_deals_local.json"
            status_path.parent.mkdir(parents=True)
            deals_path.parent.mkdir(parents=True)
            status_path.write_text(
                json.dumps({
                    "status": "passed",
                    "phase": "complete",
                    "message": "done",
                    "backend": "local",
                    "checked": 2,
                    "collector_status": "completed",
                }),
                encoding="utf-8",
            )
            deals_path.write_text(
                json.dumps({
                    "checked": 50,
                    "deals": [{"name": "private product"}],
                    "checks": [{}, {}],
                }),
                encoding="utf-8",
            )

            with patch.object(main, "ROOT", root):
                payload = main.public_price_watch_status()

            self.assertEqual(payload["deals"], 1)
            self.assertEqual(payload["checks"], 2)
            self.assertEqual(payload["checked"], 50)
            self.assertEqual(payload["backend"], "local")
            self.assertEqual(payload["message"], "本机检查完成：50 个商品，1 个折扣")
            self.assertNotIn("name", json.dumps(payload))

    def test_price_watch_status_uses_snapshot_collector_when_job_is_not_current(self):
        from app import main

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            status_path = root / ".codex" / "price-watch-on-demand-status.json"
            deals_path = root / "data" / "walmart_deals_local.json"
            status_path.parent.mkdir(parents=True)
            deals_path.parent.mkdir(parents=True)
            status_path.write_text(
                json.dumps({"backend": "vps", "status": "passed"}),
                encoding="utf-8",
            )
            deals_path.write_text(
                json.dumps({
                    "checked": 50,
                    "deals": [],
                    "checks": [],
                    "collector": {
                        "status": "wrong_store",
                        "route": "direct",
                    },
                    "schedule": {
                        "status": "idle",
                        "mode": "manual_only",
                        "strategy": "my_items",
                    },
                }),
                encoding="utf-8",
            )

            with patch.object(main, "ROOT", root):
                payload = main.public_price_watch_status()

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["collector_status"], "wrong_store")
            self.assertEqual(payload["checked"], 50)
            self.assertIn("Square One", payload["message"])

    def test_price_watch_ignores_retired_vps_snapshot(self):
        from app import main

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            status_path = root / ".codex" / "price-watch-on-demand-status.json"
            local_path = root / "data" / "walmart_deals_local.json"
            vps_path = root / "data" / "walmart_deals.json"
            status_path.parent.mkdir(parents=True)
            local_path.parent.mkdir(parents=True)
            status_path.write_text(
                json.dumps({
                    "backend": "vps",
                    "status": "passed",
                    "phase": "complete",
                    "collector_status": "queued",
                    "message": "queued",
                    "updated_at": "2026-07-26T00:16:33+00:00",
                }),
                encoding="utf-8",
            )
            local_path.write_text(
                json.dumps({
                    "received_at": "2026-07-25T23:56:57+00:00",
                    "checked": 50,
                    "collector": {"status": "completed", "route": "local"},
                    "deals": [{
                        "product_key": "walmart-ca:1",
                        "name": "Purchased Item",
                        "url": "https://www.walmart.ca/en/ip/purchased-item/1",
                        "regular_price": 5.0,
                        "current_price": 4.0,
                        "checked_at": "2026-07-25T23:56:57+00:00",
                    }],
                    "checks": [],
                }),
                encoding="utf-8",
            )
            vps_path.write_text(
                json.dumps({
                    "received_at": "2026-07-26T00:17:45+00:00",
                    "checked": 0,
                    "collector": {
                        "status": "wrong_store",
                        "route": "direct",
                    },
                    "deals": [],
                    "checks": [],
                    "schedule": {
                        "status": "attention",
                        "last_result_status": "wrong_store",
                    },
                }),
                encoding="utf-8",
            )

            with (
                patch.object(main, "ROOT", root),
            ):
                status = main.public_price_watch_status()
                deals = main.public_price_watch_deals()

            self.assertEqual(status["status"], "passed")
            self.assertEqual(status["collector_status"], "completed")
            self.assertEqual(status["result_source"], "local")
            self.assertFalse(status["synced"])
            self.assertEqual(status["checked"], 50)
            self.assertEqual(status["deals"], 1)
            self.assertIn("本机检查完成", status["message"])
            self.assertEqual(deals["result_source"], "local")
            self.assertFalse(deals["synced"])
            self.assertEqual(len(deals["deals"]), 1)
            self.assertEqual(deals["deals"][0]["name"], "Purchased Item")

    def test_collector_backend_api_exposes_safe_status_and_changes_selection(self):
        from app import main

        class Backends:
            def status(self, force=False):
                return {
                    "contract": "expense-collector-backends/v1",
                    "selected_mode": "auto",
                    "resolved_backend": "local",
                    "message": "using local",
                    "backends": {
                        "local": {
                            "status": "ready",
                            "message": "ready",
                            "selectable": True,
                        },
                        "vps": {
                            "status": "setup_required",
                            "message": "setup",
                            "selectable": False,
                        },
                    },
                }

            def set_mode(self, mode):
                payload = self.status()
                payload["selected_mode"] = mode
                payload["resolved_backend"] = mode
                return payload

        with (
            patch.object(main, "collector_backends", Backends()),
            patch.object(main.jobs, "snapshot", return_value={
                "active_job_id": None,
                "jobs": [],
            }),
        ):
            client = TestClient(main.app, base_url="http://127.0.0.1")
            status = client.get("/api/collector-backends")
            selected = client.patch(
                "/api/collector-backends",
                json={"mode": "local"},
            )
            blocked = client.patch(
                "/api/collector-backends",
                headers={"Origin": "https://example.com"},
                json={"mode": "local"},
            )

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["resolved_backend"], "local")
        self.assertNotIn("ssh_host", status.text)
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["selected_mode"], "local")
        self.assertEqual(blocked.status_code, 403)


class ExpenseAppStaticAssetsTest(unittest.TestCase):
    def test_overview_has_bounded_scrollable_platform_and_payment_breakdowns(self):
        static = Path(__file__).resolve().parents[1] / "app" / "static"
        html = (static / "index.html").read_text(encoding="utf-8")
        css = (static / "styles.css").read_text(encoding="utf-8")
        javascript = (static / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="platformBreakdown"', html)
        self.assertIn('id="paymentBreakdown"', html)
        self.assertEqual(html.count("breakdown-scroll"), 2)
        self.assertIn("height: 126px;", css)
        self.assertIn("overflow-y: auto;", css)
        self.assertIn('renderBars("paymentBreakdown", payload.cards);', javascript)
        self.assertIn('isBankOnly ? "银行账单记录" : "商品明细"', javascript)
        self.assertIn('id="ordersPrevious"', html)
        self.assertIn('id="reconciliationReviewRows"', html)
        self.assertIn('role="dialog"', html)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn('id="trendZoomOut"', html)
        self.assertIn('id="trendZoomReset"', html)
        self.assertIn('id="trendZoomIn"', html)
        self.assertIn('id="trendFullscreenButton"', html)
        trend_javascript = (static / "trend.js").read_text(encoding="utf-8")
        self.assertIn('/static/trend.js?', html)
        self.assertIn('class="trend-svg"', trend_javascript)
        self.assertIn('class="trend-selected-guide"', trend_javascript)
        self.assertIn("niceAxisMaximum(maxValue)", javascript)
        self.assertIn('tabindex="0"', javascript)
        self.assertIn('class="bar-progress"', javascript)
        self.assertIn("surface.requestFullscreen()", javascript)
        self.assertIn('document.addEventListener("fullscreenchange"', javascript)
        self.assertNotIn('style="height:', javascript)
        self.assertNotIn('style="width:', javascript)
        self.assertIn(".trend-zoom-controls", css)
        self.assertIn(".trend-surface:fullscreen", css)
        self.assertIn(".trend-selected-guide", css)
        self.assertIn("flex: 1 1 0;", css)
        self.assertIn(".bar-progress::-webkit-progress-value", css)
        self.assertIn('id="backendToggleButton"', html)
        self.assertIn('id="backendToggleLabel"', html)
        self.assertNotIn("data-collector-backend-mode=", html)
        self.assertNotIn('id="collectorBackendBadge"', html)
        self.assertIn('api("/api/collector-backends"', javascript)
        self.assertIn(".backend-toggle", css)
        self.assertNotIn(".backend-mode-control", css)
        self.assertIn("state.orderLimit", javascript)
        self.assertIn("部分完成", javascript)
        self.assertIn('id="collectorRecovery"', html)
        self.assertIn('/api/jobs/retry-collectors', javascript)
        self.assertIn('/api/vps-login-session', javascript)
        self.assertIn('/api/vps-login-session/verify', javascript)
        self.assertIn('.collector-recovery', css)
        self.assertIn('status === "partial"', javascript)
        self.assertIn('.toast.warning', css)
        self.assertIn("weeklyStatus?.collector_runs", javascript)
        self.assertIn('row.phase === "collect"', javascript)
        self.assertNotIn("collectorHealth.filter", javascript)
        self.assertEqual(html.count("<th>状态</th>"), 2)
        self.assertIn("function fulfillmentLabel(value)", javascript)
        self.assertIn('pending: "未送达"', javascript)
        self.assertIn(".fulfillment-pill.warning", css)
        self.assertIn("可能是拆分扣款或金额调整", javascript)
        self.assertIn('data-view="cash"', html)
        self.assertIn('data-panel="cash"', html)
        self.assertIn('id="cashWithdrawalRows"', html)
        self.assertIn("不会创建订单，也不会计入总览支出", html)
        self.assertIn("/api/cash-withdrawals?month=", javascript)
        self.assertIn(".cash-note-form", css)
        self.assertIn(".cash-table-wrap.empty .cash-table", css)
        self.assertIn(".cash-table-wrap.empty .cash-date-column", css)
        self.assertIn('href="/price-watch"', html)
        self.assertIn('target="_blank"', html)

        watch_html = (static / "price-watch.html").read_text(encoding="utf-8")
        watch_js = (static / "price-watch.js").read_text(encoding="utf-8")
        self.assertIn('id="runPriceWatch"', watch_html)
        self.assertIn("/api/jobs/price-watch", watch_js)
        self.assertIn("/api/price-watch/status", watch_js)
        self.assertIn("/api/price-watch/deals", watch_js)
        self.assertNotIn("/api/collector-backends", watch_js)
        self.assertNotIn("/api/jobs/price-watch-pull", watch_js)
        self.assertNotIn('id="syncVpsResults"', watch_html)
        self.assertIn("本机最近结果", watch_js)
        self.assertNotIn('id="runLocalPriceWatch"', watch_html)
        self.assertIn('id="watchBackend"', watch_html)
        self.assertIn(
            'status === "failed"\n    ? "检查未完成"',
            watch_js,
        )


if __name__ == "__main__":
    unittest.main()
