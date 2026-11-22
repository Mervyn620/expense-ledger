import csv
import importlib.util
import json
import sqlite3
import tempfile
import unittest
import warnings
import zipfile
from contextlib import closing
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "maintenance" / "safety.py"
spec = importlib.util.spec_from_file_location("expense_safety", SCRIPT)
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


def create_workspace(root):
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    (root / "exports").mkdir(parents=True)
    (root / "config" / "workflow.json").write_text(
        json.dumps(
            {
                "collection_floor": "2026-05-01",
                "payment_evidence_floor": "2026-05-01",
                "port": 9223,
                "overlap_days": 7,
                "walmart_overlap_days": 14,
                "collector_max_orders": {"walmart_ca": 12},
                "collector_policy": {
                    "min_action_delay_ms": 700,
                    "max_action_delay_ms": 1400,
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "config" / "payment_cards.json").write_text(
        json.dumps({"cards": {}}),
        encoding="utf-8",
    )
    db_path = root / "data" / "expenses.sqlite"
    with closing(sqlite3.connect(db_path)) as conn:
        for table in safety.REQUIRED_TABLES:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.commit()
    with (root / "data" / "transactions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["platform", "order_id", "total"])
        writer.writerow(["test", "one", "1.00"])
    return db_path


class SafetyTest(unittest.TestCase):
    def test_preflight_creates_verified_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)

            result = safety.preflight(root, min_free_mb=1, retention=3)

            snapshot = Path(result["backup"])
            self.assertTrue(snapshot.exists())
            self.assertEqual(result["database_status"], "ok")
            manifest = safety.verify_snapshot(
                snapshot,
                expected_workspace_id=safety.workspace_id(root),
            )
            self.assertIn("data/expenses.sqlite", manifest["files"])
            self.assertIn("data/transactions.csv", manifest["files"])
            self.assertNotIn("raw/page.html", manifest["files"])

    def test_corrupt_database_is_rejected_before_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            (root / "data" / "expenses.sqlite").write_bytes(b"not sqlite")

            with self.assertRaises(safety.SafetyError):
                safety.preflight(root, min_free_mb=1)

            self.assertEqual(list((root / "backups").glob("*.zip")), [])

    def test_invalid_collection_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            workflow_path = root / "config" / "workflow.json"
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
            workflow["collector_policy"]["min_action_delay_ms"] = 2000
            workflow["collector_policy"]["max_action_delay_ms"] = 500
            workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

            with self.assertRaises(safety.SafetyError):
                safety.validate_configs(root)

    def test_verify_rejects_modified_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            snapshot = safety.create_snapshot(root)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(snapshot, mode="a") as archive:
                    archive.writestr("data/transactions.csv", "changed")

            with self.assertRaises(safety.SafetyError):
                safety.verify_snapshot(snapshot)

    def test_retention_keeps_latest_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_workspace(root)
            for index in range(4):
                safety.create_snapshot(root, f"run-{index}", retention=2)

            self.assertEqual(len(list((root / "backups").glob("*.zip"))), 2)

    def test_restore_replaces_files_and_creates_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = create_workspace(root)
            snapshot = safety.create_snapshot(root, "known-good")
            original_csv = (root / "data" / "transactions.csv").read_bytes()

            (root / "data" / "transactions.csv").write_text(
                "broken\n",
                encoding="utf-8",
            )
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("CREATE TABLE changed_after_backup (id INTEGER)")
                conn.commit()

            rollback = safety.restore_snapshot(root, snapshot)

            self.assertTrue(rollback.exists())
            self.assertEqual(
                (root / "data" / "transactions.csv").read_bytes(),
                original_csv,
            )
            with closing(sqlite3.connect(db_path)) as conn:
                changed = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'changed_after_backup'"
                ).fetchone()[0]
            self.assertEqual(changed, 0)

    def test_cross_workspace_restore_requires_explicit_migration_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            target = base / "target"
            create_workspace(source)
            create_workspace(target)
            snapshot = safety.create_snapshot(source, "portable")

            with self.assertRaisesRegex(
                safety.SafetyError,
                "different expense workspace",
            ):
                safety.restore_snapshot(target, snapshot)

            rollback = safety.restore_snapshot(
                target,
                snapshot,
                allow_workspace_mismatch=True,
            )

            self.assertTrue(rollback.exists())
            self.assertEqual(
                (target / "data" / "transactions.csv").read_bytes(),
                (source / "data" / "transactions.csv").read_bytes(),
            )

    def test_cross_workspace_restore_removes_stale_managed_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            target = base / "target"
            create_workspace(source)
            create_workspace(target)
            stale = target / "data" / "amazon_orders.json"
            stale.write_text('{"stale": true}', encoding="utf-8")
            snapshot = safety.create_snapshot(source, "portable")

            safety.restore_snapshot(
                target,
                snapshot,
                allow_workspace_mismatch=True,
            )

            self.assertFalse(stale.exists())

    def test_latest_snapshot_selects_current_workspace_after_move(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            old_root = base / "old"
            current_root = base / "current"
            create_workspace(old_root)
            create_workspace(current_root)
            old_snapshot = safety.create_snapshot(old_root, "old")
            current_snapshot = safety.create_snapshot(current_root, "current")
            copied_old = current_root / "backups" / old_snapshot.name
            copied_old.write_bytes(old_snapshot.read_bytes())
            copied_old.touch()

            selected = safety.latest_snapshot(
                current_root,
                expected_workspace_id=safety.workspace_id(current_root),
            )

            self.assertTrue(selected.samefile(current_snapshot))


if __name__ == "__main__":
    unittest.main()
