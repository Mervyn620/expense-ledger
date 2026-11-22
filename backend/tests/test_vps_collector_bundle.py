import base64
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LOGIN = load_module(
    "vps_login_session",
    ROOT / "scripts" / "collectors" / "vps_login_session.py",
)
SERVICE = load_module(
    "vps_backend_service_test",
    ROOT / "scripts" / "collectors" / "vps_backend_service.py",
)
CLIENT = load_module(
    "vps_bundle_client_test",
    ROOT / "scripts" / "collectors" / "vps_bundle_client.py",
)


class VpsLoginSessionTest(unittest.TestCase):
    def test_local_login_manager_uses_current_release_and_explicit_verify(self):
        source = (
            ROOT / "scripts" / "manage-vps-collector-login.ps1"
        ).read_text(encoding="utf-8-sig")
        self.assertIn('"Start", "Status", "Stop", "Verify"', source)
        self.assertIn(
            '/home/ledger/apps/expense-collector/current/vps_login_session.py',
            source,
        )
        self.assertIn('"verify"', source)

    def test_login_logs_rotate_exact_allowlisted_files(self):
        with tempfile.TemporaryDirectory() as temp:
            log_dir = Path(temp) / "logs"
            log_dir.mkdir()
            current = log_dir / "walmart_ca-chromium.log"
            current.write_bytes(b"current")
            (log_dir / "walmart_ca-chromium.log.1").write_bytes(b"one")
            (log_dir / "walmart_ca-chromium.log.2").write_bytes(b"two")
            (log_dir / "walmart_ca-chromium.log.3").write_bytes(b"three")

            with (
                patch.object(LOGIN, "LOG_DIR", log_dir),
                patch.object(LOGIN, "LOG_MAX_BYTES", len(b"current")),
            ):
                path = LOGIN.rotate_log("walmart_ca-chromium.log")

            self.assertEqual(path, current)
            self.assertFalse(current.exists())
            self.assertEqual((log_dir / "walmart_ca-chromium.log.1").read_bytes(), b"current")
            self.assertEqual((log_dir / "walmart_ca-chromium.log.2").read_bytes(), b"one")
            self.assertEqual((log_dir / "walmart_ca-chromium.log.3").read_bytes(), b"two")

    def test_login_logs_reject_unknown_names_and_symbolic_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log_dir = root / "logs"
            log_dir.mkdir()
            target = root / "outside.log"
            target.write_bytes(b"outside")
            link = log_dir / "xvfb.log"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("Symbolic links are unavailable in this environment.")

            with patch.object(LOGIN, "LOG_DIR", log_dir):
                with self.assertRaises(LOGIN.SessionError):
                    LOGIN.rotate_log("../outside.log")
                with self.assertRaises(LOGIN.SessionError):
                    LOGIN.rotate_log("xvfb.log")
            self.assertEqual(target.read_bytes(), b"outside")

    def test_prepare_profile_removes_only_restored_tabs(self):
        with tempfile.TemporaryDirectory() as temp:
            profile_root = Path(temp) / "profiles"
            default = profile_root / "the_ordinary_ca" / "Default"
            sessions = default / "Sessions"
            sessions.mkdir(parents=True)
            (default / "Current Session").write_bytes(b"session")
            (default / "Cookies").write_bytes(b"keep")
            (sessions / "Session_1").write_bytes(b"session")
            (sessions / "Tabs_1").write_bytes(b"tabs")
            (sessions / "unrelated").write_bytes(b"keep")

            with patch.object(LOGIN, "PROFILE_ROOT", profile_root):
                profile = LOGIN.prepare_profile("the_ordinary_ca")

            self.assertEqual(profile, profile_root / "the_ordinary_ca")
            self.assertFalse((default / "Current Session").exists())
            self.assertFalse((sessions / "Session_1").exists())
            self.assertFalse((sessions / "Tabs_1").exists())
            self.assertEqual((default / "Cookies").read_bytes(), b"keep")
            self.assertEqual((sessions / "unrelated").read_bytes(), b"keep")
            preferences = json.loads(
                (default / "Preferences").read_text(encoding="utf-8")
            )
            self.assertFalse(preferences["credentials_enable_service"])
            self.assertFalse(preferences["profile"]["password_manager_enabled"])
            self.assertEqual(preferences["profile"]["exit_type"], "Normal")
            self.assertTrue(preferences["profile"]["exited_cleanly"])

    def test_the_ordinary_profile_page_proves_authenticated_session(self):
        targets = [{
            "type": "page",
            "url": (
                "https://theordinary.com/on/demandware.store/"
                "Sites-deciem-Site/en_CA/Account-ProfileSettings"
            ),
            "title": "Profile | The Ordinary",
        }]
        with patch.object(LOGIN, "cdp_targets", return_value=targets):
            state = LOGIN.safe_page_state("the_ordinary_ca")

        self.assertTrue(state["candidate_authenticated"])

    def test_manual_login_session_changes_stop_during_collection(self):
        with patch.object(LOGIN, "collection_is_active", return_value=True):
            with self.assertRaises(LOGIN.SessionError):
                LOGIN.require_collection_idle()


class VpsBackendServiceTest(unittest.TestCase):
    def test_release_code_is_linked_into_a_stable_mutable_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            code = base / "releases" / "release" / "app"
            (code / "scripts").mkdir(parents=True)
            (code / "config").mkdir()
            workspace = base / "workspace"
            try:
                with (
                    patch.object(SERVICE, "BASE_DIR", base),
                    patch.object(SERVICE, "APP_CODE_ROOT", code),
                    patch.object(SERVICE, "APP_ROOT", workspace),
                ):
                    SERVICE.ensure_app_workspace()
            except OSError as exc:
                self.skipTest(f"Directory links are unavailable: {exc}")

            self.assertTrue((workspace / "scripts").is_symlink())
            self.assertTrue((workspace / "config").is_symlink())
            for name in ("data", "raw", ".codex"):
                self.assertTrue((workspace / name).is_dir())
                self.assertFalse((workspace / name).is_symlink())
            self.assertEqual(
                {path.name for path in code.iterdir()},
                {"scripts", "config"},
            )

    def test_child_process_receives_stable_workspace_and_no_bytecode_policy(self):
        result = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            with (
                patch.object(SERVICE, "APP_ROOT", workspace),
                patch.object(SERVICE.subprocess, "run", return_value=result) as mocked,
            ):
                SERVICE.run_command("amazon_ca", [sys.executable, "probe.py"])
        keyword = mocked.call_args.kwargs
        self.assertEqual(keyword["cwd"], workspace)
        self.assertEqual(
            keyword["env"]["EXPENSE_COLLECTOR_WORKSPACE_ROOT"],
            str(workspace),
        )
        self.assertEqual(keyword["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    @staticmethod
    def request_payload(platforms=None, contract=None):
        selected = tuple(platforms or SERVICE.PLATFORMS)
        payload = {
            "contract": contract or SERVICE.REQUEST_CONTRACT,
            "windows": {platform: "2026-07-01" for platform in selected},
            "max_orders": {platform: 12 for platform in selected},
            "payment_min_date": "2026-07-01",
            "payment_max_orders": 20,
            "seed_files": {},
        }
        if contract == SERVICE.REQUEST_CONTRACT_V2:
            payload["platforms"] = list(selected)
            payload["collection_run_id"] = "a" * 32
        return payload

    def test_v2_request_requires_exact_selected_windows_and_limits(self):
        payload = self.request_payload(
            ("amazon_ca",),
            SERVICE.REQUEST_CONTRACT_V2,
        )
        request = SERVICE.validate_request(payload)
        self.assertEqual(request["requested_platforms"], ("amazon_ca",))
        self.assertEqual(request["expected_components"], ("amazon_ca",))
        self.assertEqual(request["response_contract"], SERVICE.BUNDLE_CONTRACT_V2)
        self.assertEqual(request["collection_run_id"], "a" * 32)

        for field in ("windows", "max_orders"):
            invalid = {**payload, field: {**payload[field], "fantuan": 12}}
            if field == "windows":
                invalid[field]["fantuan"] = "2026-07-01"
            with self.assertRaises(SERVICE.ServiceError):
                SERVICE.validate_request(invalid)

    def test_v1_request_remains_full_and_allows_default_limits(self):
        payload = self.request_payload()
        payload["max_orders"] = {"amazon_ca": 3}
        request = SERVICE.validate_request(payload)
        self.assertEqual(request["requested_platforms"], SERVICE.PLATFORMS)
        self.assertEqual(request["max_orders"]["amazon_ca"], 3)
        self.assertEqual(request["max_orders"]["fantuan"], 12)

    def test_storage_status_thresholds(self):
        gib = 1024 * 1024 * 1024
        cases = (
            (100 * gib, 21 * gib, "ok"),
            (100 * gib, 20 * gib, "warning"),
            (100 * gib, 10 * gib, "critical"),
            (10 * gib, gib - 1, "critical"),
        )
        for total, free, expected in cases:
            with self.subTest(expected=expected, free=free):
                usage = SimpleNamespace(total=total, used=total - free, free=free)
                with patch.object(SERVICE.shutil, "disk_usage", return_value=usage):
                    payload = SERVICE.storage_status()
                self.assertEqual(payload["status"], expected)
                self.assertEqual(payload["free_bytes"], free)

    def test_bundle_pass_requires_every_primary_and_payment_component(self):
        rows = [
            {"id": component, "status": "passed"}
            for component in SERVICE.EXPECTED_COMPONENTS
        ]
        self.assertTrue(SERVICE.bundle_all_passed(rows))
        self.assertFalse(SERVICE.bundle_all_passed(rows[:-1]))
        rows[0]["status"] = "failed"
        self.assertFalse(SERVICE.bundle_all_passed(rows))
        rows.append(dict(rows[-1]))
        self.assertFalse(SERVICE.bundle_all_passed(rows))

    def test_seed_install_removes_only_missing_allowlisted_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / "app"
            profile = Path(temp) / "profiles" / "amazon_ca"
            source = app / "scripts" / "collectors" / "runner.py"
            old_output = app / "data" / "amazon_orders.json"
            retained = app / "data" / "fantuan_orders.json"
            auth = profile / ".authenticated.json"
            for path, content in (
                (source, "source"),
                (old_output, '{"old": true}'),
                (retained, '{"old": true}'),
                (auth, '{"authenticated": true}'),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            seed = {
                "data/fantuan_orders.json": base64.b64encode(
                    b'{"fresh": true}'
                ).decode("ascii"),
            }

            with patch.object(SERVICE, "APP_ROOT", app):
                SERVICE.install_seed_files(seed)

            self.assertFalse(old_output.exists())
            self.assertEqual(
                json.loads(retained.read_text(encoding="utf-8")),
                {"fresh": True},
            )
            self.assertEqual(source.read_text(encoding="utf-8"), "source")
            self.assertTrue(auth.is_file())

    def test_invalid_seed_is_rejected_before_existing_outputs_are_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            existing = app / "data" / "amazon_orders.json"
            existing.parent.mkdir(parents=True)
            existing.write_text('{"old": true}', encoding="utf-8")

            with patch.object(SERVICE, "APP_ROOT", app):
                with self.assertRaises(SERVICE.ServiceError):
                    SERVICE.install_seed_files({
                        "data/fantuan_orders.json": "not-base64",
                    })

            self.assertTrue(existing.is_file())

    def test_selective_seed_install_preserves_unrequested_platform_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            amazon = app / "data" / "amazon_orders.json"
            fantuan = app / "data" / "fantuan_orders.json"
            for path in (amazon, fantuan):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"old": true}', encoding="utf-8")

            with patch.object(SERVICE, "APP_ROOT", app):
                SERVICE.install_seed_files({}, ("amazon_ca",))

            self.assertFalse(amazon.exists())
            self.assertTrue(fantuan.is_file())

    def test_finalize_components_fills_all_eight_and_marks_cache_freshness(self):
        rows = [{
            "id": "amazon_ca",
            "status": "failed",
            "exit_code": 1,
            "duration_seconds": 1,
        }]
        finalized = SERVICE.finalize_component_records(
            rows,
            {"data/amazon_orders.json": "encoded"},
        )

        self.assertEqual(
            [row["id"] for row in finalized],
            list(SERVICE.EXPECTED_COMPONENTS),
        )
        self.assertEqual(len(finalized), 8)
        amazon = next(row for row in finalized if row["id"] == "amazon_ca")
        walmart = next(row for row in finalized if row["id"] == "walmart_ca")
        self.assertEqual(amazon["data_freshness"], "cache_preserved")
        self.assertEqual(walmart["data_freshness"], "none")

    def test_selective_primary_failure_marks_payment_skipped(self):
        expected = SERVICE.expected_components_for(("walmart_ca",))
        finalized = SERVICE.finalize_component_records(
            [{
                "id": "walmart_ca",
                "status": "failed",
                "exit_code": 1,
                "duration_seconds": 1,
            }],
            {"data/payment_evidence.json": "encoded"},
            expected,
        )

        self.assertEqual(
            [row["id"] for row in finalized],
            ["walmart_ca", "walmart_ca_payment"],
        )
        self.assertEqual(finalized[1]["status"], "skipped")
        self.assertEqual(finalized[1]["data_freshness"], "cache_preserved")

    def test_selective_collection_response_declares_dynamic_contract(self):
        payload = self.request_payload(
            ("walmart_ca",),
            SERVICE.REQUEST_CONTRACT_V2,
        )
        payload["seed_files"] = {
            "data/payment_evidence.json": base64.b64encode(b"{}").decode("ascii")
        }
        components = []
        with (
            patch.object(SERVICE, "status", return_value={"status": "ready"}),
            patch.object(LOGIN, "probe_home_proxy"),
            patch.object(SERVICE, "install_seed_files"),
            patch.object(SERVICE, "clear_raw_outputs"),
            patch.object(LOGIN, "start_session") as start_session,
            patch.object(LOGIN, "stop_session"),
            patch.object(SERVICE, "write_collection_progress"),
            patch.object(SERVICE, "run_command", return_value={
                "id": "walmart_ca",
                "status": "failed",
                "exit_code": 1,
                "started_at": "2026-07-26T22:23:00+00:00",
                "duration_seconds": 1,
                "message": "failed safely",
            }),
            patch.object(SERVICE, "build_bundle", return_value=b"bundle"),
        ):
            response = SERVICE._collect_bundle_locked(
                payload,
                "2026-07-26T22:23:00+00:00",
                components,
            )

        start_session.assert_called_once_with("walmart_ca", interactive=False)
        self.assertGreaterEqual(response["timings"]["session_start_seconds"], 0)
        self.assertGreaterEqual(response["timings"]["final_cleanup_seconds"], 0)
        self.assertEqual(response["contract"], SERVICE.BUNDLE_CONTRACT_V2)
        self.assertEqual(response["requested_platforms"], ["walmart_ca"])
        self.assertEqual(
            response["expected_components"],
            ["walmart_ca", "walmart_ca_payment"],
        )
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["components"][1]["status"], "skipped")

    def test_selective_preparing_and_failure_progress_use_dynamic_total(self):
        payload = self.request_payload(
            ("walmart_ca",),
            SERVICE.REQUEST_CONTRACT_V2,
        )
        calls = []

        with (
            patch.object(SERVICE, "exclusive_collection_lock", return_value=nullcontext()),
            patch.object(
                SERVICE,
                "_collect_bundle_locked",
                side_effect=SERVICE.ServiceError("stopped safely"),
            ),
            patch.object(
                SERVICE,
                "write_collection_progress",
                side_effect=lambda *args, **kwargs: calls.append((args, kwargs)),
            ),
        ):
            with self.assertRaises(SERVICE.ServiceError):
                SERVICE.collect_bundle(payload)

        self.assertEqual(calls[0][1]["components_total"], 2)
        self.assertEqual(calls[-1][1]["components_total"], 2)

    def test_selective_bundle_contains_only_requested_platform_files(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            for relative in SERVICE.DATA_OUTPUTS:
                path = app / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"ok": true}', encoding="utf-8")

            with patch.object(SERVICE, "APP_ROOT", app):
                content = SERVICE.build_bundle(("fantuan",))

            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"data/fantuan_orders.json", "data/payment_evidence.json"},
                )

    def test_status_requires_verified_profiles_and_deployed_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            app = base / "app"
            profiles = base / "profiles"
            node = base / "runtime" / "node" / "bin" / "node"
            node.parent.mkdir(parents=True)
            node.write_text("", encoding="utf-8")
            for relative in SERVICE.REQUIRED_SOURCES:
                path = app / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            for platform in SERVICE.PLATFORMS:
                marker = profiles / platform / ".authenticated.json"
                marker.parent.mkdir(parents=True)
                marker.write_text(json.dumps({
                    "contract": "expense-collector-auth-marker/v1",
                    "platform": platform,
                    "route": "home_exit",
                }), encoding="utf-8")

            with (
                patch.object(SERVICE, "BASE_DIR", base),
                patch.object(SERVICE, "APP_ROOT", app),
                patch.object(SERVICE, "PROFILE_ROOT", profiles),
                patch.object(SERVICE, "NODE", node),
                patch.object(SERVICE, "command_version", return_value="v24.18.0"),
                patch.object(LOGIN, "find_browser", return_value="/browser"),
                patch.object(SERVICE.shutil, "which", return_value="/tool"),
                patch.object(SERVICE, "storage_status", return_value={
                    "status": "warning",
                    "used_percent": 80.0,
                    "free_bytes": 2 * 1024 * 1024 * 1024,
                }),
            ):
                payload = SERVICE.status()

            self.assertEqual(payload["status"], "ready")
            self.assertIn("collect_bundle_v1", payload["capabilities"])
            self.assertIn("tls_diagnostics_v1", payload["capabilities"])
            self.assertEqual(payload["storage"]["status"], "warning")

    def test_request_rejects_unknown_seed_path(self):
        payload = {
            "contract": SERVICE.REQUEST_CONTRACT,
            "windows": {
                platform: "2026-07-01"
                for platform in SERVICE.PLATFORMS
            },
            "max_orders": {
                platform: 12
                for platform in SERVICE.PLATFORMS
            },
            "payment_min_date": "2026-07-01",
            "payment_max_orders": 20,
            "seed_files": {"data/expenses.sqlite": "AA=="},
        }
        with self.assertRaises(SERVICE.ServiceError):
            SERVICE.validate_request(payload)

    def test_bundle_contains_only_valid_json_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            data = app / "data"
            raw = app / "raw"
            data.mkdir()
            raw.mkdir()
            for relative in SERVICE.DATA_OUTPUTS:
                path = app / relative
                path.write_text('{"ok": true}', encoding="utf-8")
            (raw / "2026_walmart_ca_orders.json").write_text(
                '{"platform": "walmart_ca"}',
                encoding="utf-8",
            )
            with patch.object(SERVICE, "APP_ROOT", app):
                content = SERVICE.build_bundle()
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self.assertIn("data/amazon_orders.json", archive.namelist())
                self.assertIn(
                    "raw/2026_walmart_ca_orders.json",
                    archive.namelist(),
                )

    def test_tls_diagnostics_returns_only_valid_contract_payload(self):
        payload = {
            "contract": SERVICE.TLS_DIAGNOSTICS_CONTRACT,
            "status": "passed",
            "route": "home_exit",
            "generated_at": "2026-07-31T12:00:00+00:00",
            "runtime": {"browser": "Chrome/150.0.0.0"},
            "targets": [],
        }
        result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        with (
            patch.object(LOGIN, "find_browser", return_value="/browser"),
            patch.object(SERVICE, "command_version", return_value="Chrome/150.0.0.0"),
            patch.object(SERVICE.subprocess, "run", return_value=result),
        ):
            actual = SERVICE.tls_diagnostics()
        self.assertEqual(actual["status"], "passed")
        self.assertEqual(actual["route"], "home_exit")


class VpsBundleClientTest(unittest.TestCase):
    @staticmethod
    def tls_diagnostic(route, browser="Chrome/150.0.0.0"):
        return {
            "contract": CLIENT.TLS_DIAGNOSTICS_CONTRACT,
            "status": "passed",
            "route": route,
            "generated_at": "2026-07-31T12:00:00+00:00",
            "runtime": {"browser": browser},
            "targets": [
                {
                    "platform": platform,
                    "host": f"{platform}.example",
                    "status": "passed",
                    "category": "ok",
                    "tls_version": "TLSv1.3",
                    "alpn": "h2",
                }
                for platform in CLIENT.PLATFORMS
            ],
        }

    def bundle_response(self, members):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, payload in members.items():
                archive.writestr(name, json.dumps(payload))
        content = output.getvalue()
        return {
            "bundle_base64": base64.b64encode(content).decode("ascii"),
            "bundle_sha256": hashlib.sha256(content).hexdigest(),
        }

    @staticmethod
    def component_rows(failed=None, include_freshness=True):
        rows = []
        for component in CLIENT.EXPECTED_COMPONENTS:
            status = "failed" if component == failed else "passed"
            row = {
                "id": component,
                "status": status,
                "exit_code": 1 if status == "failed" else 0,
                "started_at": "2026-07-26T22:23:00+00:00",
                "duration_seconds": 1.5,
                "message": "failed safely" if status == "failed" else "ok",
            }
            if include_freshness:
                row["data_freshness"] = (
                    "cache_preserved" if status == "failed" else "live"
                )
            rows.append(row)
        return rows

    def test_valid_bundle_is_applied_without_touching_sqlite(self):
        response = self.bundle_response({
            "data/amazon_orders.json": {"orders": []},
            "data/the_ordinary_orders.json": {"orders": []},
            "raw/2026_uber_eats_orders.json": {"platform": "uber_eats"},
        })
        content = CLIENT.decode_bundle(response)
        members = CLIENT.read_bundle_members(content)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "data" / "expenses.sqlite"
            database.parent.mkdir(parents=True)
            database.write_bytes(b"sqlite")
            CLIENT.apply_members(root, members)
            self.assertEqual(database.read_bytes(), b"sqlite")
            self.assertTrue((root / "data" / "amazon_orders.json").is_file())
            self.assertTrue(
                (root / "data" / "the_ordinary_orders.json").is_file()
            )

    def test_bundle_rejects_path_traversal(self):
        response = self.bundle_response({
            "../outside.json": {"bad": True},
        })
        with self.assertRaises(CLIENT.BundleClientError):
            CLIENT.read_bundle_members(CLIENT.decode_bundle(response))

    def test_selective_bundle_rejects_unrequested_member(self):
        response = self.bundle_response({
            "data/fantuan_orders.json": {"orders": []},
        })
        with self.assertRaises(CLIENT.BundleClientError):
            CLIENT.read_bundle_members(
                CLIENT.decode_bundle(response),
                ("amazon_ca",),
            )

    def test_selective_arguments_reject_duplicates_and_require_exact_keys(self):
        with self.assertRaises(CLIENT.BundleClientError):
            CLIENT.parse_requested_platforms(["amazon_ca", "amazon_ca"])

        selected, selective = CLIENT.parse_requested_platforms(
            ["fantuan", "amazon_ca"]
        )
        self.assertTrue(selective)
        self.assertEqual(selected, ("amazon_ca", "fantuan"))
        with self.assertRaises(CLIENT.BundleClientError):
            CLIENT.parse_key_values(
                ["amazon_ca=2026-07-01"],
                selected,
                CLIENT.parse_date,
                "window",
            )

    def test_selective_client_uses_v2_and_writes_dynamic_summary(self):
        components = self.component_rows()
        components = [
            row for row in components
            if row["id"] in {"walmart_ca", "walmart_ca_payment"}
        ]
        response = {
            **self.bundle_response({
                "data/payment_evidence.json": {"evidence": []},
            }),
            "contract": CLIENT.BUNDLE_CONTRACT_V2,
            "status": "passed",
            "bundle_bytes": 1,
            "generated_at": "2026-07-26T22:23:38+00:00",
            "requested_platforms": ["walmart_ca"],
            "expected_components": ["walmart_ca", "walmart_ca_payment"],
            "collection_run_id": "a" * 32,
            "components": components,
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            data.mkdir()
            (data / "transactions.csv").write_text(
                "platform,order_id\nwalmart_ca,1\n",
                encoding="utf-8",
            )
            (data / "walmart_items.csv").write_text(
                "platform,order_id\nwalmart_ca,1\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(
                root=root,
                platform=["walmart_ca"],
                window=["walmart_ca=2026-07-01"],
                max_order=["walmart_ca=12"],
                payment_min_date="2026-07-01",
                payment_max_orders=12,
                collection_run_id="a" * 32,
            )
            captured = {}

            def request_remote(_config, _command, payload=None, timeout=None):
                captured.update(payload)
                return response

            with (
                patch.object(CLIENT, "load_backend_config", return_value={
                    "collection_timeout_seconds": 30,
                }),
                patch.object(CLIENT, "stop_login_tunnel"),
                patch.object(CLIENT, "start_home_tunnel", return_value=object()),
                patch.object(CLIENT, "wait_for_home_exit"),
                patch.object(CLIENT, "request_remote", side_effect=request_remote),
                patch.object(CLIENT, "stop_process"),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = CLIENT.collect(args)

            summary = json.loads(
                (root / ".codex" / "collector-runs" / "vps-bundle.json")
                .read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["contract"], CLIENT.REQUEST_CONTRACT_V2)
        self.assertEqual(captured["platforms"], ["walmart_ca"])
        self.assertEqual(set(captured["windows"]), {"walmart_ca"})
        self.assertEqual(summary["requested_platforms"], ["walmart_ca"])
        self.assertEqual(
            summary["expected_components"],
            ["walmart_ca", "walmart_ca_payment"],
        )

    def test_selective_retries_merge_unresolved_platforms_until_all_pass(self):
        windows = {platform: "2026-07-01" for platform in CLIENT.PLATFORMS}
        limits = {platform: 12 for platform in CLIENT.PLATFORMS}

        def row(component, status):
            return {
                "id": component,
                "status": status,
                "exit_code": 0 if status == "passed" else 1,
                "started_at": "2026-08-12T16:00:00+00:00",
                "duration_seconds": 1,
                "data_freshness": "live" if status == "passed" else "cache_preserved",
                "message": "ok" if status == "passed" else "failed safely",
            }

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            CLIENT.write_component_statuses(
                root,
                {
                    "status": "partial",
                    "generated_at": "2026-08-12T16:01:00+00:00",
                    "collection_run_id": "a" * 32,
                },
                [
                    row("amazon_ca", "failed"),
                    row("fantuan", "failed"),
                    row("fantuan_payment", "skipped"),
                ],
                windows,
                limits,
                ("amazon_ca", "fantuan"),
                ("amazon_ca", "fantuan", "fantuan_payment"),
            )
            CLIENT.write_component_statuses(
                root,
                {
                    "status": "passed",
                    "generated_at": "2026-08-12T16:02:00+00:00",
                    "collection_run_id": "b" * 32,
                },
                [row("amazon_ca", "passed")],
                windows,
                limits,
                ("amazon_ca",),
                ("amazon_ca",),
                True,
            )
            summary_path = root / ".codex" / "collector-runs" / "vps-bundle.json"
            after_amazon = json.loads(summary_path.read_text(encoding="utf-8"))

            self.assertEqual(after_amazon["status"], "partial")
            self.assertEqual(
                after_amazon["requested_platforms"],
                ["amazon_ca", "fantuan"],
            )
            self.assertEqual(after_amazon["attempt_requested_platforms"], ["amazon_ca"])
            self.assertEqual(
                next(
                    item for item in after_amazon["components"]
                    if item["id"] == "fantuan"
                )["status"],
                "failed",
            )

            CLIENT.write_component_statuses(
                root,
                {
                    "status": "passed",
                    "generated_at": "2026-08-12T16:03:00+00:00",
                    "collection_run_id": "c" * 32,
                },
                [row("fantuan", "passed"), row("fantuan_payment", "passed")],
                windows,
                limits,
                ("fantuan",),
                ("fantuan", "fantuan_payment"),
                True,
            )
            after_fantuan = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(after_fantuan["status"], "passed")
        self.assertTrue(all(
            item["status"] == "passed" for item in after_fantuan["components"]
        ))

    def test_new_selective_run_drops_an_unrelated_previous_recovery_set(self):
        windows = {platform: "2026-07-01" for platform in CLIENT.PLATFORMS}
        limits = {platform: 12 for platform in CLIENT.PLATFORMS}

        def row(component, status):
            return {
                "id": component,
                "status": status,
                "exit_code": 0 if status == "passed" else 1,
                "started_at": "2026-08-23T04:00:00+00:00",
                "duration_seconds": 1,
                "data_freshness": "live" if status == "passed" else "cache_preserved",
                "message": status,
            }

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            CLIENT.write_component_statuses(
                root,
                {
                    "status": "partial",
                    "generated_at": "2026-08-23T04:01:00+00:00",
                    "collection_run_id": "a" * 32,
                },
                [row("amazon_ca", "failed")],
                windows,
                limits,
                ("amazon_ca",),
                ("amazon_ca",),
            )
            routine = ("walmart_ca", "uber_eats", "fantuan")
            CLIENT.write_component_statuses(
                root,
                {
                    "status": "passed",
                    "generated_at": "2026-08-23T04:02:00+00:00",
                    "collection_run_id": "b" * 32,
                },
                [
                    row("walmart_ca", "passed"),
                    row("uber_eats", "passed"),
                    row("fantuan", "passed"),
                    row("walmart_ca_payment", "passed"),
                    row("uber_eats_payment", "passed"),
                    row("fantuan_payment", "passed"),
                ],
                windows,
                limits,
                routine,
                CLIENT.expected_components_for(routine),
            )
            summary = json.loads(
                (root / ".codex" / "collector-runs" / "vps-bundle.json")
                .read_text(encoding="utf-8")
            )
            backlog = json.loads(
                (root / ".codex" / "collector-runs" / "vps-recovery.json")
                .read_text(encoding="utf-8")
            )

        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["requested_platforms"], list(routine))
        self.assertNotIn("amazon_ca", summary["expected_components"])
        self.assertEqual(backlog["status"], "partial")
        self.assertEqual([row["id"] for row in backlog["platforms"]], ["amazon_ca"])

    def test_recovery_backlog_survives_unrelated_run_and_clears_on_success(self):
        windows = {platform: "2026-07-01" for platform in CLIENT.PLATFORMS}
        limits = {platform: 12 for platform in CLIENT.PLATFORMS}

        def row(component, status):
            return {
                "id": component,
                "status": status,
                "exit_code": 0 if status == "passed" else 1,
                "started_at": "2026-08-27T23:00:00+00:00",
                "duration_seconds": 1,
                "data_freshness": "live" if status == "passed" else "cache_preserved",
                "message": status,
            }

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            CLIENT.write_component_statuses(
                root,
                {
                    "status": "partial",
                    "generated_at": "2026-08-27T23:01:00+00:00",
                    "collection_run_id": "a" * 32,
                },
                [row("uber_eats", "login_required"), row("uber_eats_payment", "skipped")],
                windows,
                limits,
                ("uber_eats",),
                ("uber_eats", "uber_eats_payment"),
            )
            CLIENT.write_component_statuses(
                root,
                {
                    "status": "passed",
                    "generated_at": "2026-08-27T23:02:00+00:00",
                    "collection_run_id": "b" * 32,
                },
                [row("amazon_ca", "passed")],
                windows,
                limits,
                ("amazon_ca",),
                ("amazon_ca",),
            )
            backlog_path = root / ".codex" / "collector-runs" / "vps-recovery.json"
            after_amazon = json.loads(backlog_path.read_text(encoding="utf-8"))
            CLIENT.write_component_statuses(
                root,
                {
                    "status": "passed",
                    "generated_at": "2026-08-27T23:03:00+00:00",
                    "collection_run_id": "c" * 32,
                },
                [row("uber_eats", "passed"), row("uber_eats_payment", "passed")],
                windows,
                limits,
                ("uber_eats",),
                ("uber_eats", "uber_eats_payment"),
            )
            after_uber = json.loads(backlog_path.read_text(encoding="utf-8"))

        self.assertEqual([row["id"] for row in after_amazon["platforms"]], ["uber_eats"])
        self.assertEqual(after_uber["status"], "passed")
        self.assertEqual(after_uber["platforms"], [])

    def test_selective_shared_payment_seed_preserves_other_platform_rows(self):
        evidence = {
            "contract": "expense-payment-evidence/v1",
            "evidence": [
                {"platform": "walmart_ca", "order_id": "W1"},
                {"platform": "fantuan", "order_id": "F1"},
                {"platform": "amazon_ca", "order_id": "A1"},
                {"platform": "the_ordinary_ca", "order_id": "O1"},
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "data" / "payment_evidence.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(evidence), encoding="utf-8")

            seed = CLIENT.build_seed(root, ("walmart_ca",))
            decoded = json.loads(base64.b64decode(
                seed["data/payment_evidence.json"]
            ).decode("utf-8"))

        self.assertEqual(decoded["evidence"], evidence["evidence"])

    def test_partial_bundle_is_applied_as_a_successful_transport(self):
        response = {
            **self.bundle_response({
                "data/amazon_orders.json": {"orders": []},
            }),
            "contract": CLIENT.BUNDLE_CONTRACT,
            "status": "partial",
            "bundle_bytes": 1,
            "generated_at": "2026-07-26T22:23:38+00:00",
            "components": self.component_rows(
                failed="amazon_ca",
                include_freshness=False,
            ),
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(
                root=root,
                window=[
                    f"{platform}=2026-07-01"
                    for platform in CLIENT.PLATFORMS
                ],
                max_order=[
                    f"{platform}=12"
                    for platform in CLIENT.PLATFORMS
                ],
                payment_min_date="2026-07-01",
                payment_max_orders=12,
                collection_run_id="d" * 32,
            )
            (root / "data").mkdir(parents=True)
            (root / "data" / "amazon_orders.json").write_text(
                '{"orders": [{"cached": true}]}',
                encoding="utf-8",
            )
            output = io.StringIO()
            captured_request = {}

            def request_remote(_config, _command, payload=None, timeout=None):
                captured_request.update(payload)
                return response

            with (
                patch.object(CLIENT, "load_backend_config", return_value={
                    "collection_timeout_seconds": 30,
                }),
                patch.object(CLIENT, "stop_login_tunnel"),
                patch.object(CLIENT, "start_home_tunnel", return_value=object()),
                patch.object(CLIENT, "wait_for_home_exit"),
                patch.object(CLIENT, "request_remote", side_effect=request_remote),
                patch.object(CLIENT, "stop_process"),
                redirect_stdout(output),
            ):
                exit_code = CLIENT.collect(args)

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / "data" / "amazon_orders.json").is_file())
            component = json.loads(
                (root / ".codex" / "collector-runs" / "amazon_ca-collect.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(component["status"], "failed")
            self.assertEqual(component["data_freshness"], "cache_preserved")
            summary = json.loads(
                (root / ".codex" / "collector-runs" / "vps-bundle.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(len(summary["components"]), 8)
            self.assertEqual(captured_request["contract"], CLIENT.REQUEST_CONTRACT)
            self.assertNotIn("platforms", captured_request)
            self.assertNotIn("collection_run_id", captured_request)
            self.assertEqual(summary["collection_run_id"], "d" * 32)
            self.assertIn(
                "collector_bundle_failed_components=amazon_ca",
                output.getvalue(),
            )
            self.assertIn(
                "collector_bundle_cached_components=amazon_ca",
                output.getvalue(),
            )

    def test_component_validation_rejects_missing_duplicate_and_disagreement(self):
        response = {
            "status": "partial",
            "components": self.component_rows(failed="amazon_ca"),
        }
        self.assertEqual(
            len(CLIENT.validate_component_results(response, set())),
            8,
        )

        missing = {**response, "components": response["components"][:-1]}
        with self.assertRaises(CLIENT.BundleClientError):
            CLIENT.validate_component_results(missing, set())

        duplicate_rows = list(response["components"])
        duplicate_rows[-1] = dict(duplicate_rows[0])
        with self.assertRaises(CLIENT.BundleClientError):
            CLIENT.validate_component_results(
                {**response, "components": duplicate_rows},
                set(),
            )

        with self.assertRaises(CLIENT.BundleClientError):
            CLIENT.validate_component_results(
                {**response, "status": "passed"},
                set(),
            )

    def test_legacy_v1_omitted_payment_after_primary_failure_is_synthesized(self):
        components = self.component_rows(
            failed="walmart_ca",
            include_freshness=False,
        )
        components = [
            row for row in components if row["id"] != "walmart_ca_payment"
        ]
        response = {
            "contract": CLIENT.BUNDLE_CONTRACT,
            "status": "partial",
            "components": components,
        }

        rows = CLIENT.validate_component_results(
            response,
            {"data/payment_evidence.json"},
        )

        payment = next(
            row for row in rows if row["id"] == "walmart_ca_payment"
        )
        self.assertEqual(len(rows), 8)
        self.assertEqual(payment["status"], "skipped")
        self.assertEqual(payment["data_freshness"], "cache_preserved")

    def test_component_summary_sanitizes_remote_operational_details(self):
        response = {
            "status": "partial",
            "components": self.component_rows(failed="amazon_ca"),
        }
        response["components"][1]["message"] = (
            "Failed at https://example.test/private from "
            "/home/ledger/apps/private on 192.0.2.1"
        )

        rows = CLIENT.validate_component_results(response, set())
        message = rows[1]["message"]

        self.assertNotIn("example.test", message)
        self.assertNotIn("/home/ledger", message)
        self.assertNotIn("192.0.2.1", message)
        self.assertIn("[url]", message)
        self.assertIn("[remote-path]", message)
        self.assertIn("[host]", message)

    def test_payment_only_partial_remains_visible_in_eight_component_summary(self):
        components = self.component_rows(failed="walmart_ca_payment")
        response = {
            "status": "partial",
            "generated_at": "2026-07-26T22:23:38+00:00",
        }
        windows = {platform: "2026-07-01" for platform in CLIENT.PLATFORMS}
        limits = {platform: 12 for platform in CLIENT.PLATFORMS}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            CLIENT.write_component_statuses(
                root,
                response,
                components,
                windows,
                limits,
            )
            summary = json.loads(
                (root / ".codex" / "collector-runs" / "vps-bundle.json")
                .read_text(encoding="utf-8")
            )

        self.assertEqual(len(summary["components"]), 8)
        payment = next(
            row
            for row in summary["components"]
            if row["id"] == "walmart_ca_payment"
        )
        self.assertEqual(payment["status"], "failed")
        self.assertEqual(payment["data_freshness"], "cache_preserved")

    def test_diagnose_writes_sanitized_consistency_result(self):
        local = self.tls_diagnostic("local")
        remote = self.tls_diagnostic(
            "home_exit",
            browser="Google Chrome for Testing 150.0.0.0",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(root=root, output=".codex/tls-consistency.json")
            with (
                patch.object(CLIENT, "load_backend_config", return_value={}),
                patch.object(CLIENT, "run_local_tls_diagnostics", return_value=local),
                patch.object(CLIENT, "stop_login_tunnel"),
                patch.object(CLIENT, "start_home_tunnel", return_value=object()),
                patch.object(CLIENT, "wait_for_home_exit"),
                patch.object(CLIENT, "request_remote", return_value=remote),
                patch.object(CLIENT, "stop_process"),
            ):
                exit_code = CLIENT.diagnose(args)

            self.assertEqual(exit_code, 0)
            payload = json.loads(
                (root / ".codex" / "tls-consistency.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["browser_consistency"]["major_gap"], 0)


if __name__ == "__main__":
    unittest.main()
