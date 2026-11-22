"""Parallel collection: run-many in the collector runner and the on/off setting."""
import io
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.setup_state import SetupStore
from scripts.collectors import runner


BACKEND = Path(__file__).resolve().parents[1]
COLLECTORS = [{"id": name, "enabled": True} for name in ("walmart_ca", "fantuan", "tnt_ca", "amazon_ca")]


def args(*jobs, workers=3):
    return SimpleNamespace(job=list(jobs), phase="collect", port=9223, node=None,
                           max_workers=workers, stagger_seconds=0.0)


class RunManyTest(unittest.TestCase):
    def run_with_fake(self, fake, *jobs, workers=3):
        out = io.StringIO()
        with patch.object(runner.subprocess, "run", side_effect=fake), redirect_stdout(out):
            code = runner.run_many(args(*jobs, workers=workers), Path("manifest.json"), COLLECTORS)
        return code, out.getvalue()

    def test_stores_run_at_the_same_time_and_report_in_order(self):
        running, peak, lock = [0], [0], threading.Lock()

        def fake(command, **_):
            with lock:
                running[0] += 1; peak[0] = max(peak[0], running[0])
            time.sleep(0.3)
            with lock:
                running[0] -= 1
            store = command[command.index("--id") + 1]
            return subprocess.CompletedProcess(command, 1 if store == "fantuan" else 0, f"{store} done", "")

        started = time.monotonic()
        code, output = self.run_with_fake(fake, "walmart_ca=2026-10-01=12", "fantuan=2026-10-01", "tnt_ca=2026-09-27")
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertEqual((code, peak[0]), (0, 3))
        self.assertLess(output.index("walmart_ca done"), output.index("fantuan done"))
        self.assertIn("collector_parallel_result=walmart_ca:0", output)
        self.assertIn("collector_parallel_result=fantuan:1", output)

    def test_worker_limit_is_respected(self):
        running, peak, lock = [0], [0], threading.Lock()

        def fake(command, **_):
            with lock:
                running[0] += 1; peak[0] = max(peak[0], running[0])
            time.sleep(0.1)
            with lock:
                running[0] -= 1
            return subprocess.CompletedProcess(command, 0, "", "")

        self.run_with_fake(fake, "walmart_ca=", "fantuan=", "tnt_ca=", "amazon_ca=", workers=2)
        self.assertEqual(peak[0], 2)

    def test_each_store_gets_its_own_run_command(self):
        commands = []

        def fake(command, **_):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        self.run_with_fake(fake, "walmart_ca=2026-10-01=12")
        command = commands[0]
        self.assertEqual(command[command.index("run"):command.index("run") + 11],
                         ["run", "--id", "walmart_ca", "--phase", "collect", "--port", "9223",
                          "--min-date", "2026-10-01", "--max-orders", "12"])
        self.assertEqual(commands[0][commands[0].index("--max-orders") + 1], "12")

    def test_bad_jobs_are_rejected(self):
        for jobs in (("walmart_ca=2026-10-01", "walmart_ca=2026-10-01"), ("unknown=2026-10-01",),
                     ("walmart_ca=10/01/2026",), ("walmart_ca=2026-10-01=many",)):
            with self.assertRaises(runner.ManifestError, msg=jobs):
                runner.run_many(args(*jobs), Path("manifest.json"), COLLECTORS)


class ParallelSettingTest(unittest.TestCase):
    def test_setting_defaults_off_and_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config").mkdir()
            shutil.copy(BACKEND / "config" / "workflow.json", root / "config" / "workflow.json")
            store = SetupStore(root)
            self.assertEqual(store.collection_settings(), {"parallel_collection": False, "max_workers": 3})
            self.assertTrue(store.set_parallel_collection(True)["parallel_collection"])
            workflow = json.loads((root / "config" / "workflow.json").read_text(encoding="utf-8"))
            self.assertFalse(workflow["parallel_collection"]["enabled"])   # repository default untouched
            local = json.loads((root / "config" / "collection.local.json").read_text(encoding="utf-8"))
            self.assertEqual(local, {"parallel_collection": {"enabled": True}})
            self.assertFalse(store.set_parallel_collection(False)["parallel_collection"])


if __name__ == "__main__":
    unittest.main()
