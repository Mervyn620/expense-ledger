import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "maintenance"
    / "initialize_workspace.py"
)
spec = importlib.util.spec_from_file_location("workspace_initializer", SCRIPT)
initializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initializer)


def create_configs(root):
    config_dir = root / "config"
    config_dir.mkdir(parents=True)
    for name in initializer.REQUIRED_CONFIGS:
        (config_dir / name).write_text("{}\n", encoding="utf-8")


class WorkspaceInitializationTest(unittest.TestCase):
    def test_initializes_empty_database_and_runtime_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_configs(root)

            status = initializer.initialize_workspace(root)

            self.assertTrue(status["database_created"])
            self.assertFalse(status["existing_data_preserved"])
            self.assertTrue((root / "data" / "expenses.sqlite").exists())
            self.assertTrue((root / "exports" / "current").is_dir())
            self.assertTrue((root / ".codex" / "initialization-status.json").exists())
            with closing(sqlite3.connect(root / "data" / "expenses.sqlite")) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            self.assertIn("orders", tables)
            self.assertIn("bank_transactions", tables)
            self.assertIn("cash_withdrawal_notes", tables)

    def test_repeated_initialization_preserves_existing_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_configs(root)
            initializer.initialize_workspace(root)
            db_path = root / "data" / "expenses.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "INSERT INTO cards (card_id, display_name) VALUES ('keep', 'Keep')"
                )
                conn.commit()

            status = initializer.initialize_workspace(root)

            self.assertFalse(status["database_created"])
            self.assertTrue(status["existing_data_preserved"])
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT display_name FROM cards WHERE card_id = 'keep'"
                    ).fetchone()[0],
                    "Keep",
                )

    def test_missing_configuration_is_rejected_without_creating_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config").mkdir()

            with self.assertRaises(initializer.InitializationError):
                initializer.initialize_workspace(root)

            self.assertFalse((root / "data" / "expenses.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
