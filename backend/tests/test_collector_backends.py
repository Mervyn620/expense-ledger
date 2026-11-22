import json
import tempfile
import unittest
from pathlib import Path

from app.collector_backends import (
    CollectorBackendError,
    CollectorBackendManager,
    REQUIRED_COLLECT_CAPABILITY,
    STATE_CONTRACT,
)


def write_config(root):
    path = root / "config" / "collector_backends.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "contract": "expense-collector-backends/v1",
            "default_mode": "auto",
            "health_cache_seconds": 15,
            "local": {
                "label": "本地",
                "type": "local",
                "enabled": True,
            },
            "vps": {
                "label": "VPS",
                "type": "ssh",
                "enabled": True,
                "ssh_host": "ledger_vps",
                "remote_python": "/usr/bin/python3",
                "remote_service": "/home/ledger/vps_backend_service.py",
                "timeout_seconds": 6,
                "home_exit_remote_port": 1080,
                "collection_timeout_seconds": 1200,
            },
        }),
        encoding="utf-8",
    )
    return path


def ready(message):
    return {
        "status": "ready",
        "message": message,
        "capabilities": [REQUIRED_COLLECT_CAPABILITY],
        "browser_status": "ready",
    }


class CollectorBackendManagerTest(unittest.TestCase):
    def manager(self, root, remote):
        return CollectorBackendManager(
            root=root,
            config_path=write_config(root),
            local_probe=lambda _config: ready("local ready"),
            remote_probe=lambda _config: remote,
        )

    def test_paused_vps_is_never_probed_and_local_selection_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = write_config(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["default_mode"] = "local"
            config["vps"]["enabled"] = False
            config_path.write_text(json.dumps(config), encoding="utf-8")
            def forbidden_remote_probe(_config):
                self.fail("Paused VPS must not be contacted")
            manager = CollectorBackendManager(
                root=root, config_path=config_path,
                local_probe=lambda _: ready("local ready"),
                remote_probe=forbidden_remote_probe,
            )
            self.assertEqual(manager.resolve()["resolved_backend"], "local")
            self.assertEqual(manager.resolve(mode="auto")["resolved_backend"], "local")
            status = manager.set_mode("local")
            self.assertEqual(manager.selected_mode(), "local")
            self.assertFalse(status["backends"]["vps"]["selectable"])
            with self.assertRaises(CollectorBackendError):
                manager.set_mode("vps")
            self.assertEqual(manager.selected_mode(), "local")

    def test_auto_prefers_ready_vps_and_persists_selection_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = self.manager(root, ready("vps ready"))

            initial = manager.status(force=True)
            selected = manager.set_mode("local")

            self.assertEqual(initial["selected_mode"], "auto")
            self.assertEqual(initial["resolved_backend"], "vps")
            self.assertEqual(selected["selected_mode"], "local")
            self.assertEqual(selected["resolved_backend"], "local")
            state = json.loads(manager.state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["contract"], STATE_CONTRACT)
            self.assertEqual(state["selected_mode"], "local")
            self.assertEqual(list(manager.state_path.parent.glob("*.tmp")), [])

    def test_status_and_resolution_expose_validated_local_only_platforms(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = write_config(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["local_only_platforms"] = ["the_ordinary_ca"]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            manager = CollectorBackendManager(
                root=root,
                config_path=config_path,
                local_probe=lambda _config: ready("local"),
                remote_probe=lambda _config: ready("vps"),
            )

            status = manager.status(force=True)
            resolved = manager.resolve("vps", force=False)

            self.assertEqual(status["local_only_platforms"], ["the_ordinary_ca"])
            self.assertEqual(resolved["local_only_platforms"], ["the_ordinary_ca"])
            self.assertIn("本地专用浏览器", status["message"])

    def test_unknown_or_duplicate_local_only_platforms_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = write_config(root)
            for values in (["unknown"], ["the_ordinary_ca", "the_ordinary_ca"]):
                config = json.loads(config_path.read_text(encoding="utf-8"))
                config["local_only_platforms"] = values
                config_path.write_text(json.dumps(config), encoding="utf-8")
                manager = CollectorBackendManager(
                    root=root,
                    config_path=config_path,
                    local_probe=lambda _config: ready("local"),
                    remote_probe=lambda _config: ready("vps"),
                )
                with self.subTest(values=values), self.assertRaisesRegex(
                    CollectorBackendError,
                    "local_only_platforms",
                ):
                    manager.status(force=True)

    def test_auto_falls_back_before_run_but_manual_unready_vps_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = self.manager(root, {
                "status": "setup_required",
                "message": "VPS profiles are not configured",
                "capabilities": ["status"],
            })

            payload = manager.status(force=True)

            self.assertEqual(payload["resolved_backend"], "local")
            self.assertIn("自动使用本地运行环境", payload["message"])
            self.assertFalse(payload["backends"]["vps"]["selectable"])
            with self.assertRaisesRegex(
                CollectorBackendError,
                "profiles are not configured",
            ):
                manager.set_mode("vps")

    def test_status_does_not_expose_ssh_host_or_remote_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = self.manager(root, ready("vps ready"))

            serialized = json.dumps(manager.status(force=True))

            self.assertNotIn("ledger_vps", serialized)
            self.assertNotIn("/home/ledger", serialized)
            self.assertNotIn("remote_python", serialized)

    def test_status_exposes_only_bounded_collection_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = ready("vps ready")
            remote["collection"] = {
                "status": "running",
                "stage": "collecting",
                "platform": "the_ordinary_ca",
                "components_completed": 5,
                "started_at": "private timestamp",
                "unexpected": "/home/ledger/private",
            }
            manager = self.manager(root, remote)

            collection = manager.status(force=True)["backends"]["vps"]["collection"]

            self.assertEqual(collection, {
                "status": "running",
                "stage": "collecting",
            "platform": "the_ordinary_ca",
            "components_completed": 5,
            "components_total": 8,
        })

    def test_storage_warning_is_sanitized_without_disabling_vps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = ready("vps ready")
            remote["storage"] = {
                "status": "warning",
                "used_percent": 80.04,
                "free_bytes": 2 * 1024 * 1024 * 1024,
                "private_path": "/home/ledger/private",
            }
            manager = self.manager(root, remote)

            vps = manager.status(force=True)["backends"]["vps"]

            self.assertTrue(vps["selectable"])
            self.assertEqual(vps["storage"], {
                "status": "warning",
                "used_percent": 80.0,
                "free_bytes": 2 * 1024 * 1024 * 1024,
            })
            self.assertNotIn("private_path", vps["storage"])

    def test_invalid_remote_path_is_rejected_before_any_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = write_config(root)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["vps"]["remote_service"] = "../../unsafe.py"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            manager = CollectorBackendManager(
                root=root,
                config_path=config_path,
                local_probe=lambda _config: ready("local"),
                remote_probe=lambda _config: ready("vps"),
            )

            with self.assertRaisesRegex(
                CollectorBackendError,
                "absolute remote path",
            ):
                manager.status(force=True)


if __name__ == "__main__":
    unittest.main()
