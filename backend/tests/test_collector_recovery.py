import json
import tempfile
import unittest
from pathlib import Path

from app.collector_recovery import collector_recovery_status


class CollectorRecoveryTest(unittest.TestCase):
    def write_state(self, root, components, requested=None, status="partial"):
        run_id = "a" * 32
        bundle_path = root / ".codex" / "collector-runs" / "vps-bundle.json"
        bundle_path.parent.mkdir(parents=True)
        bundle_path.write_text(json.dumps({
            "contract": "expense-collector-bundle-status/v1",
            "status": status,
            "generated_at": "2026-08-12T16:00:00+00:00",
            "collection_run_id": run_id,
            "requested_platforms": requested or ["walmart_ca", "the_ordinary_ca"],
            "components": components,
        }), encoding="utf-8")
        current = root / "exports" / "current"
        current.mkdir(parents=True)
        (current / "run-state.json").write_text(json.dumps({
            "status": status,
            "completed_at": "2026-08-12T12:05:00-04:00",
            "collector_backend_resolved": "vps",
            "collection_run_id": run_id,
            "collect_from_browser": True,
        }), encoding="utf-8")
        (current / "weekly-status.json").write_text(json.dumps({
            "validation": "passed",
            "collection_run_id": run_id,
            "collect_from_browser": True,
        }), encoding="utf-8")

    def test_payment_failure_aggregates_to_retryable_parent_platform(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_state(root, [
                {"id": "walmart_ca", "status": "passed", "data_freshness": "live"},
                {"id": "walmart_ca_payment", "status": "failed", "data_freshness": "cache_preserved", "message": "payment timed out"},
                {"id": "the_ordinary_ca", "status": "login_required", "data_freshness": "cache_preserved", "message": "manual login required"},
            ])

            payload = collector_recovery_status(root)

            walmart, ordinary = payload["platforms"]
            self.assertEqual(payload["status"], "partial")
            self.assertTrue(walmart["retryable"])
            self.assertTrue(walmart["using_cached_data"])
            self.assertTrue(ordinary["login_required"])
            self.assertFalse(ordinary["retryable"])

    def test_local_only_platform_is_removed_from_vps_recovery_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_state(root, [{
                "id": "the_ordinary_ca",
                "status": "login_required",
                "data_freshness": "cache_preserved",
            }], requested=["the_ordinary_ca"])
            config = root / "config" / "collector_backends.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({
                "local_only_platforms": ["the_ordinary_ca"],
            }), encoding="utf-8")

            payload = collector_recovery_status(root)

            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["platforms"], [])
            self.assertIn("固定在本地", payload["message"])

    def uber_login_failure(self, root):
        self.write_state(root, [
            {"id": "uber_eats", "status": "login_required", "data_freshness": "cache_preserved", "message": "login"},
            {"id": "uber_eats_payment", "status": "skipped", "data_freshness": "cache_preserved"},
        ], requested=["uber_eats"])

    def write_collect(self, root, platform, status, completed_at):
        (root / ".codex" / "collector-runs" / f"{platform}-collect.json").write_text(json.dumps({
            "status": status, "completed_at": completed_at}), encoding="utf-8")

    def test_disabled_vps_shows_no_recovery_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.uber_login_failure(root)
            config = root / "config" / "collector_backends.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"vps": {"enabled": False}}), encoding="utf-8")

            payload = collector_recovery_status(root)

            self.assertEqual((payload["status"], payload["platforms"]), ("unavailable", []))
            self.assertIn("已停用", payload["message"])

    def test_later_successful_collection_supersedes_old_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.uber_login_failure(root)
            self.write_collect(root, "uber_eats", "passed", "2026-08-11T00:00:00+00:00")
            self.assertEqual(collector_recovery_status(root)["status"], "partial")   # older success: still failing

            self.write_collect(root, "uber_eats", "failed", "2026-08-13T00:00:00+00:00")
            self.assertEqual(collector_recovery_status(root)["status"], "partial")   # newer failure: still failing

            self.write_collect(root, "uber_eats", "passed", "2026-08-13T00:00:00+00:00")
            payload = collector_recovery_status(root)
            self.assertEqual((payload["status"], payload["platforms"]), ("unavailable", []))

    def test_stale_or_non_vps_bundle_is_not_actionable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_state(root, [], requested=["amazon_ca"])
            state_path = root / "exports" / "current" / "run-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["collector_backend_resolved"] = "local"
            state_path.write_text(json.dumps(state), encoding="utf-8")

            payload = collector_recovery_status(root)

            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["platforms"], [])

    def test_mismatched_or_legacy_run_id_is_not_actionable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_state(root, [{
                "id": "amazon_ca",
                "status": "failed",
                "data_freshness": "cache_preserved",
            }], requested=["amazon_ca"])
            state_path = root / "exports" / "current" / "run-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["collection_run_id"] = "b" * 32
            state_path.write_text(json.dumps(state), encoding="utf-8")

            self.assertEqual(
                collector_recovery_status(root)["status"],
                "unavailable",
            )

            state["collection_run_id"] = ""
            state_path.write_text(json.dumps(state), encoding="utf-8")
            self.assertEqual(
                collector_recovery_status(root)["status"],
                "unavailable",
            )

    def test_payment_login_required_is_manual_diagnosis_not_login_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_state(root, [
                {"id": "walmart_ca", "status": "passed", "data_freshness": "live"},
                {
                    "id": "walmart_ca_payment",
                    "status": "login_required",
                    "data_freshness": "cache_preserved",
                    "message": "payment login required",
                },
            ], requested=["walmart_ca"])

            row = collector_recovery_status(root)["platforms"][0]

            self.assertEqual(row["status"], "blocked")
            self.assertTrue(row["manual_diagnosis"])
            self.assertFalse(row["login_required"])
            self.assertFalse(row["retryable"])

    def test_invalid_contract_duplicates_and_expected_set_are_rejected(self):
        valid_components = [{
            "id": "amazon_ca",
            "status": "failed",
            "data_freshness": "cache_preserved",
        }]
        mutations = (
            lambda payload: payload.update({"contract": "unsupported/v9"}),
            lambda payload: payload.update({
                "requested_platforms": ["amazon_ca", "amazon_ca"],
            }),
            lambda payload: payload.update({
                "components": [valid_components[0], dict(valid_components[0])],
            }),
            lambda payload: payload.update({
                "contract": "expense-collector-bundle-status/v2",
                "expected_components": ["walmart_ca"],
            }),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.write_state(
                    root,
                    valid_components,
                    requested=["amazon_ca"],
                )
                bundle_path = root / ".codex" / "collector-runs" / "vps-bundle.json"
                payload = json.loads(bundle_path.read_text(encoding="utf-8"))
                mutate(payload)
                bundle_path.write_text(json.dumps(payload), encoding="utf-8")

                self.assertEqual(
                    collector_recovery_status(root)["status"],
                    "unavailable",
                )

    def test_unresolved_backlog_survives_a_later_unrelated_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_state(root, [{
                "id": "amazon_ca",
                "status": "passed",
                "data_freshness": "live",
            }], requested=["amazon_ca"], status="passed")
            backlog_path = root / ".codex" / "collector-runs" / "vps-recovery.json"
            backlog_path.write_text(json.dumps({
                "contract": "expense-collector-recovery-backlog/v1",
                "status": "partial",
                "generated_at": "2026-08-27T23:14:04+00:00",
                "platforms": [{
                    "id": "uber_eats",
                    "collection_run_id": "b" * 32,
                    "generated_at": "2026-08-27T23:08:17+00:00",
                    "components": [
                        {
                            "id": "uber_eats",
                            "status": "login_required",
                            "data_freshness": "cache_preserved",
                            "message": "manual login required",
                        },
                        {
                            "id": "uber_eats_payment",
                            "status": "skipped",
                            "data_freshness": "cache_preserved",
                        },
                    ],
                }],
            }), encoding="utf-8")

            payload = collector_recovery_status(root)

        self.assertEqual(payload["status"], "partial")
        self.assertEqual([row["id"] for row in payload["platforms"]], ["uber_eats"])
        self.assertTrue(payload["platforms"][0]["login_required"])


if __name__ == "__main__":
    unittest.main()
