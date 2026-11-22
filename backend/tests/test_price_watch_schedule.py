import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts" / "price_watch"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location(
    "price_watch_vps_scheduled",
    SCRIPT_DIR / "vps_scheduled.py",
)
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)


class PriceWatchScheduleTest(unittest.TestCase):
    def test_scheduled_mode_is_explicitly_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            status_path = Path(temp) / "schedule-status.json"
            lock_path = Path(temp) / "schedule.lock"
            with (
                patch.object(schedule, "STATUS_PATH", status_path),
                patch.object(schedule, "LOCK_PATH", lock_path),
                patch.object(
                    schedule,
                    "exclusive_lock",
                    return_value=nullcontext(True),
                ),
            ):
                result = schedule.process("scheduled", {})

        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["mode"], "manual_only")
        self.assertEqual(result["strategy"], "my_items")

    def test_initialize_removes_stale_periodic_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            status_path = Path(temp) / "schedule-status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "strategy": "my_items",
                        "cadence_days": 2,
                        "scheduled_local_time": "03:17",
                        "next_due_at": "2026-07-27T03:17:00-04:00",
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(schedule, "STATUS_PATH", status_path):
                result = schedule.initialize_status()

        self.assertEqual(result["mode"], "manual_only")
        self.assertNotIn("cadence_days", result)
        self.assertNotIn("scheduled_local_time", result)
        self.assertNotIn("next_due_at", result)


if __name__ == "__main__":
    unittest.main()
