import json
import tempfile
import unittest
from pathlib import Path
from app.desktop_collection import collection_summary

class DesktopCollectionTests(unittest.TestCase):
    def test_only_matching_run_and_requested_platforms_are_displayed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            state=root/"exports/current/run-state.json"; state.parent.mkdir(parents=True)
            bundle=root/".codex/collector-runs/vps-bundle.json"; bundle.parent.mkdir(parents=True)
            state.write_text(json.dumps({"status":"partial","collector_backend_resolved":"vps","collection_run_id":"current","duration_seconds":72}))
            payload={"collection_run_id":"old","attempt_requested_platforms":["walmart_ca"],"components":[{"id":"walmart_ca","status":"passed","duration_seconds":3},{"id":"uber_eats","status":"login_required","duration_seconds":2}]}
            bundle.write_text(json.dumps(payload))
            self.assertEqual(collection_summary(root)["components"],[])
            payload["collection_run_id"]="current"; payload["timings"]={"remote":{"total_seconds":70,"session_start_seconds":2},"client":{"connection_seconds":1,"secret":"excluded"}}
            bundle.write_text(json.dumps(payload))
            result=collection_summary(root)
            self.assertEqual([x["id"] for x in result["components"]],["walmart_ca"])
            self.assertEqual([x["seconds"] for x in result["timings"]],[1.0,2.0])
            self.assertNotIn("secret",str(result))

    def test_import_only_refresh_falls_back_to_latest_collection_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "exports/current/run-state.json"; state.parent.mkdir(parents=True)
            records = root / ".codex/collector-runs"; records.mkdir(parents=True)
            state.write_text(json.dumps({"status":"partial", "collector_backend_resolved":"not_applicable", "duration_seconds":6.2,
                                         "started_at":"2026-11-16T01:28:59+00:00", "collector_platforms":[]}))
            def write(platform, start, end, status="passed", backend=None):
                row = {"collector_id":platform, "phase":"collect", "status":status, "started_at":start, "completed_at":end, "duration_seconds":5}
                if backend: row["context"] = {"backend":backend}
                (records / f"{platform}-collect.json").write_text(json.dumps(row))
            write("walmart_ca", "2026-11-16T01:13:48+00:00", "2026-11-16T01:14:46+00:00")
            write("uber_eats", "2026-11-16T01:16:52+00:00", "2026-11-16T01:16:58+00:00", "login_required")
            write("tnt_ca", "2026-10-18T03:52:45+00:00", "2026-10-18T03:52:59+00:00")
            write("fantuan", "2026-11-16T01:15:00+00:00", "2026-11-16T01:15:08+00:00", backend="vps")
            result = collection_summary(root)
            self.assertEqual([x["id"] for x in result["components"]], ["uber_eats", "walmart_ca"])
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["duration_seconds"], 190.0)
            self.assertEqual(result["source"], "latest_collection")

    def test_missing_truncated_and_nonfinite_status_remain_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertIsNone(collection_summary(root))
            state=root/"exports/current/run-state.json"; state.parent.mkdir(parents=True)
            state.write_text("{")
            self.assertIsNone(collection_summary(root))
            state.write_text(json.dumps({"status":"passed","duration_seconds":float("nan")}))
            self.assertIsNone(collection_summary(root)["duration_seconds"])

    def test_local_run_excludes_old_records_and_vps_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "exports/current/run-state.json"; state.parent.mkdir(parents=True)
            records = root / ".codex/collector-runs"; records.mkdir(parents=True)
            state.write_text(json.dumps({"status":"partial", "collector_backend_resolved":"local", "started_at":"2026-09-29T12:00:00.500+00:00", "completed_at":"2026-09-29T12:01:00+00:00", "collector_platforms":["walmart_ca", "uber_eats", "fantuan", "../invalid"]}))
            row = {"collector_id":"walmart_ca", "phase":"collect", "status":"passed", "started_at":"2026-09-29T12:00:00+00:00", "completed_at":"2026-09-29T12:00:15+00:00", "duration_seconds":15}
            (records / "walmart_ca-collect.json").write_text(json.dumps(row))
            (records / "uber_eats-collect.json").write_text(json.dumps(dict(row, collector_id="uber_eats", started_at="2026-09-28T12:00:00+00:00")))
            (records / "fantuan-collect.json").write_text(json.dumps(dict(row, collector_id="fantuan", context={"backend":"vps"})))
            result = collection_summary(root)
            self.assertEqual([x["id"] for x in result["components"]], ["walmart_ca"])
            self.assertEqual(result["backend"], "本地")
            self.assertEqual(result["components"][0]["duration_seconds"], 15)

    def test_malformed_status_values_do_not_break_desktop_polling(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "exports/current/run-state.json"; state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"status": [], "started_at": {}, "duration_seconds": True}))
            result = collection_summary(root)
            self.assertEqual(result["status"], "unknown")
            self.assertIsNone(result["started_at"])
            self.assertIsNone(result["duration_seconds"])
