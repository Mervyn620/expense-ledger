import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collectors" / "runner.py"

spec = importlib.util.spec_from_file_location("collector_runner", SCRIPT)
collector_runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector_runner)


class CollectorManifestTest(unittest.TestCase):
    def real_manifest(self):
        return collector_runner.read_manifest(ROOT / "config" / "collectors.json")

    def test_real_manifest_defines_six_standard_order_collectors(self):
        payload = self.real_manifest()
        collectors = collector_runner.validate_manifest(payload, ROOT)

        self.assertEqual(payload["component_contract"], "expense-collector/v1")
        self.assertEqual(
            [collector["id"] for collector in collectors],
            [
                "walmart_ca",
                "amazon_ca",
                "uber_eats",
                "the_ordinary_ca",
                "fantuan",
                "tnt_ca",
            ],
        )
        self.assertTrue(all(collector["enabled"] for collector in collectors))

    def test_build_command_renders_standard_context(self):
        payload = collector_runner.load_validated_manifest(
            ROOT / "config" / "collectors.json",
            ROOT,
        )
        amazon = collector_runner.find_collector(payload, "amazon_ca")
        command = collector_runner.build_command(
            amazon,
            "collect",
            {
                "port": 9223,
                "min_date": "2026-06-01",
                "max_orders": 12,
                "root": str(ROOT),
            },
            root=ROOT,
            node="node-test.exe",
        )

        self.assertEqual(command[0], "node-test.exe")
        self.assertTrue(command[1].endswith("export-amazon-orders.mjs"))
        self.assertEqual(command[-6:], [
            "--port",
            "9223",
            "--min-date",
            "2026-06-01",
            "--max-orders",
            "12",
        ])

        ordinary = collector_runner.find_collector(payload, "the_ordinary_ca")
        ordinary_command = collector_runner.build_command(
            ordinary,
            "collect",
            {
                "port": 9223,
                "min_date": "2026-06-01",
                "max_orders": 12,
                "root": str(ROOT),
            },
            root=ROOT,
            node="node-test.exe",
        )
        self.assertTrue(
            ordinary_command[1].endswith("export-the-ordinary-orders.mjs")
        )

    def test_duplicate_ids_and_unknown_placeholders_are_rejected(self):
        duplicate = self.real_manifest()
        duplicate["collectors"].append(copy.deepcopy(duplicate["collectors"][0]))
        with self.assertRaisesRegex(collector_runner.ManifestError, "Duplicate collector id"):
            collector_runner.validate_manifest(duplicate, ROOT)

        unknown = self.real_manifest()
        unknown["collectors"][0]["collect"]["arguments"].append("{cookie}")
        with self.assertRaisesRegex(collector_runner.ManifestError, "unknown placeholders"):
            collector_runner.validate_manifest(unknown, ROOT)

    def test_entrypoints_cannot_escape_workspace(self):
        payload = self.real_manifest()
        payload["collectors"][0]["collect"]["entrypoint"] = "../outside.mjs"

        with self.assertRaisesRegex(collector_runner.ManifestError, "workspace-relative"):
            collector_runner.validate_manifest(payload, ROOT)

    def test_workspace_environment_rejects_relative_and_symlink_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(
                collector_runner.ManifestError,
                "absolute non-symlink",
            ):
                collector_runner.workspace_root(
                    ROOT,
                    {"EXPENSE_COLLECTOR_WORKSPACE_ROOT": "relative/workspace"},
                )
            target = root / "workspace"
            target.mkdir()
            link = root / "workspace-link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Directory links are unavailable: {exc}")
            with self.assertRaisesRegex(
                collector_runner.ManifestError,
                "absolute non-symlink",
            ):
                collector_runner.workspace_root(
                    ROOT,
                    {"EXPENSE_COLLECTOR_WORKSPACE_ROOT": str(link)},
                )

    def test_symlinked_runner_keeps_runtime_writes_out_of_release(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            code = base / "releases" / "release-id" / "app"
            collector_dir = code / "scripts" / "collectors"
            config = code / "config"
            collector_dir.mkdir(parents=True)
            config.mkdir()
            (collector_dir / "runner.py").write_bytes(SCRIPT.read_bytes())
            (collector_dir / "probe.py").write_text(
                "from pathlib import Path\n"
                "Path('data').mkdir(exist_ok=True)\n"
                "Path('data/probe.txt').write_text('workspace', encoding='utf-8')\n"
                "Path('.codex').mkdir(exist_ok=True)\n"
                "Path('.codex/probe.txt').write_text('workspace', encoding='utf-8')\n",
                encoding="utf-8",
            )
            manifest = {
                "manifest_version": 1,
                "component_contract": "expense-collector/v1",
                "collectors": [{
                    "id": "probe",
                    "label": "Probe",
                    "enabled": True,
                    "role": "orders",
                    "ledger_platform": "probe",
                    "tab_platform": "probe",
                    "capabilities": ["orders", "items"],
                    "window": {"overlap_days_setting": "overlap_days"},
                    "collect": {
                        "runtime": "python",
                        "entrypoint": "scripts/collectors/probe.py",
                        "arguments": [],
                    },
                    "import": {
                        "runtime": "python",
                        "entrypoint": "scripts/collectors/probe.py",
                        "arguments": [],
                    },
                    "outputs": {
                        "transactions": "data/transactions.csv",
                        "item_staging": "data/items.csv",
                    },
                }],
            }
            (config / "collectors.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (config / "platforms.json").write_text(
                json.dumps({"probe": {}}), encoding="utf-8"
            )
            (config / "workflow.json").write_text(
                json.dumps({"overlap_days": 1}), encoding="utf-8"
            )
            workspace = base / "workspace"
            workspace.mkdir(parents=True)
            try:
                (workspace / "scripts").symlink_to(
                    code / "scripts", target_is_directory=True
                )
                (workspace / "config").symlink_to(
                    code / "config", target_is_directory=True
                )
            except OSError as exc:
                self.skipTest(f"Directory links are unavailable: {exc}")

            before = {
                path.relative_to(code).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in code.rglob("*")
                if path.is_file()
            }
            environment = os.environ.copy()
            environment["EXPENSE_COLLECTOR_WORKSPACE_ROOT"] = str(workspace)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(workspace / "scripts" / "collectors" / "runner.py"),
                    "--manifest",
                    str(workspace / "config" / "collectors.json"),
                    "run",
                    "--id",
                    "probe",
                    "--phase",
                    "collect",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((workspace / "data" / "probe.txt").read_text(), "workspace")
            self.assertTrue(
                (workspace / ".codex" / "collector-runs" / "probe-collect.json").is_file()
            )
            after = {
                path.relative_to(code).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in code.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_runner_writes_sanitized_component_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "collector.py"
            script.write_text(
                "print('Imported 2 rows from https://private.invalid/order')\n",
                encoding="utf-8",
            )
            collector = {
                "id": "sample_store",
                "label": "Sample Store",
                "enabled": True,
                "role": "orders",
                "ledger_platform": "sample_store",
                "tab_platform": "sample_store",
                "capabilities": ["orders", "items"],
                "window": {"overlap_days_setting": "overlap_days"},
                "collect": {
                    "runtime": "python",
                    "entrypoint": "collector.py",
                    "arguments": [],
                },
                "import": {
                    "runtime": "python",
                    "entrypoint": "collector.py",
                    "arguments": [],
                },
                "outputs": {
                    "transactions": "data/transactions.csv",
                    "item_staging": "data/sample_items.csv",
                },
            }
            status_dir = root / "status"

            exit_code = collector_runner.run_component(
                collector,
                "collect",
                {
                    "port": 9223,
                    "min_date": "2026-06-01",
                    "max_orders": None,
                    "root": str(root),
                },
                root=root,
                status_dir=status_dir,
            )

            self.assertEqual(exit_code, 0)
            status = json.loads(
                (status_dir / "sample_store-collect.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "passed")
            self.assertEqual(status["contract"], "expense-collector/v1")
            self.assertNotIn("private.invalid", status["message"])
            self.assertIn("[url]", status["message"])

    def test_runner_preserves_login_required_component_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "collector.py"
            script.write_text("raise SystemExit(1)\n", encoding="utf-8")
            health = root / ".codex" / "collector-health.json"
            health.parent.mkdir(parents=True)
            health.write_text(
                json.dumps({
                    "platforms": {
                        "sample_store": {"status": "login_required"},
                    },
                }),
                encoding="utf-8",
            )
            collector = {
                "id": "sample_store",
                "label": "Sample Store",
                "enabled": True,
                "role": "orders",
                "ledger_platform": "sample_store",
                "tab_platform": "sample_store",
                "capabilities": ["orders", "items"],
                "window": {"overlap_days_setting": "overlap_days"},
                "collect": {
                    "runtime": "python",
                    "entrypoint": "collector.py",
                    "arguments": [],
                },
                "import": {
                    "runtime": "python",
                    "entrypoint": "collector.py",
                    "arguments": [],
                },
                "outputs": {
                    "transactions": "data/transactions.csv",
                    "item_staging": "data/sample_items.csv",
                },
            }
            status_dir = root / "status"

            exit_code = collector_runner.run_component(
                collector,
                "collect",
                {"port": 9223, "min_date": "", "max_orders": None},
                root=root,
                status_dir=status_dir,
            )

            self.assertEqual(exit_code, 1)
            status = json.loads(
                (status_dir / "sample_store-collect.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "login_required")

    def test_status_message_redacts_platform_order_ids(self):
        message = collector_runner.clean_message(
            "",
            (
                "Walmart order 600000097939727 failed; "
                "Amazon 702-0000000-0000001; Fantuan CA1234567890"
            ),
        )

        self.assertNotIn("600000097939727", message)
        self.assertNotIn("702-0000000-0000001", message)
        self.assertNotIn("CA1234567890", message)
        self.assertEqual(message.count("[order-id]"), 3)

    def test_pretty_json_status_message_is_compacted_instead_of_becoming_brace(self):
        message = collector_runner.clean_message(
            json.dumps(
                {
                    "platform": "walmart_ca",
                    "candidates": 5,
                    "exported": [],
                },
                indent=2,
            ),
            "",
        )

        self.assertEqual(
            message,
            '{"platform":"walmart_ca","candidates":5,"exported":[]}',
        )

    def test_weekly_workflow_uses_runner_instead_of_platform_entrypoints(self):
        workflow = (ROOT / "scripts" / "weekly-expense-refresh.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn(r"scripts\collectors\runner.py", workflow)
        self.assertNotIn("collect-walmart-orders.mjs", workflow)
        self.assertNotIn("export-amazon-orders.mjs", workflow)
        self.assertNotIn("export-fantuan-orders.mjs", workflow)

    def test_weekly_workflow_does_not_invoke_retired_vps_price_watch(self):
        workflow = (ROOT / "scripts" / "weekly-expense-refresh.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertNotIn("scripts\\price_watch\\client.py", workflow)
        self.assertNotIn("price_watch_sync=", workflow)

    def test_weekly_workflow_overlays_current_runs_on_cross_run_health(self):
        workflow = (ROOT / "scripts" / "weekly-expense-refresh.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn("$CurrentCollectRuns", workflow)
        self.assertIn('$HealthRow.status = [string]$Run.status', workflow)
        self.assertIn('$HealthRow.last_failure = [string]$Run.completed_at', workflow)

    def test_weekly_workflow_preserves_partial_and_selected_platform_semantics(self):
        workflow = (ROOT / "scripts" / "weekly-expense-refresh.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn('[string]$CollectorPlatforms = ""', workflow)
        self.assertIn('if ($RequestedCollectorIds.Count -gt 0)', workflow)
        self.assertIn('"--platform", [string]$Collector.id', workflow)
        self.assertIn('collector_bundle_failed_components=', workflow)
        self.assertIn('Write-RunState -Status $WorkflowStatus', workflow)
        self.assertIn('Write-Host "workflow_status=$WorkflowStatus"', workflow)
        self.assertIn('Write-Host "collection_status=$CollectionStatus"', workflow)
        self.assertIn('collection_status = $CollectionStatus', workflow)
        self.assertIn('$LocalOrderCollectors', workflow)
        self.assertIn('$VpsOrderCollectors', workflow)
        self.assertIn('$LocalOrderCollectors = @(', workflow)
        self.assertIn('$VpsOrderCollectors = @(', workflow)
        self.assertIn('[switch]$ContinueCollectorRecovery', workflow)
        self.assertIn('$BundleArguments += "--continue-recovery"', workflow)
        self.assertIn('collector_local_only_platforms', workflow)
        self.assertIn('$OnDemandCollectorIds', workflow)
        self.assertIn('collector_on_demand_platforms', workflow)

    def test_scotia_fallback_does_not_mislabel_every_export_error_as_login(self):
        workflow = (ROOT / "scripts" / "weekly-expense-refresh.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn("$CollectorHealth.platforms.scotia.reason", workflow)
        self.assertIn("Latest Scotiabank transaction export failed", workflow)
        self.assertIn("Scotiabank login or verification is required", workflow)
        self.assertIn("log(?:ged)?.?in", workflow)
        self.assertNotIn(
            "Scotiabank login is required in the dedicated browser; cached",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
