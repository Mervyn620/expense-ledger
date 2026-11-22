"""First-run setup state and first-launch workspace initialization."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from app.setup_state import SetupError, SetupStore
from scripts.maintenance.initialize_workspace import initialize_workspace


BACKEND = Path(__file__).resolve().parents[1]
REQUIRED = ("collector-manifest.schema.json", "collectors.json", "merchant_rules.json",
            "payment_cards.json", "platforms.json", "workflow.json")


class SetupStoreTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "config").mkdir()
        (self.root / "data").mkdir()
        shutil.copy(BACKEND / "config" / "workflow.json", self.root / "config" / "workflow.json")
        self.store = SetupStore(self.root)

    def test_fresh_workspace_needs_onboarding(self):
        status = self.store.status()
        self.assertEqual((status["needs_onboarding"], status["completed"], status["bank_mode"]), (True, False, "later"))

    def test_existing_ledger_never_sees_the_guide(self):
        (self.root / "data" / "scotia_transactions.json").write_text("{}", encoding="utf-8")
        status = self.store.status()
        self.assertEqual((status["needs_onboarding"], status["bank_mode"]), (False, "scotia"))

    def test_empty_database_still_counts_as_fresh(self):
        initialize_workspace_root = self.root
        with closing(sqlite3.connect(initialize_workspace_root / "data" / "expenses.sqlite")) as conn:
            conn.execute("CREATE TABLE orders (id INTEGER)")
        self.assertTrue(self.store.status()["needs_onboarding"])
        with closing(sqlite3.connect(self.root / "data" / "expenses.sqlite")) as conn:
            conn.execute("INSERT INTO orders VALUES (1)"); conn.commit()
        self.assertFalse(self.store.status()["needs_onboarding"])

    def test_save_writes_choice_and_start_date(self):
        status = self.store.save("statements", ["walmart_ca", "uber_eats", "walmart_ca"], "2026-08-01",
                                 today=date(2026, 11, 21))
        self.assertEqual((status["completed"], status["needs_onboarding"], status["bank_mode"]), (True, False, "statements"))
        self.assertEqual(status["platforms"], ["walmart_ca", "uber_eats"])
        workflow = json.loads((self.root / "config" / "workflow.json").read_text(encoding="utf-8"))
        self.assertEqual((workflow["collection_floor"], workflow["payment_evidence_floor"]), ("2026-08-01", "2026-08-01"))
        self.assertIn("collector_policy", workflow)   # other workflow settings preserved

    def test_skip_keeps_the_existing_start_date(self):
        before = (self.root / "config" / "workflow.json").read_text(encoding="utf-8")
        self.store.save("later")
        self.assertEqual((self.root / "config" / "workflow.json").read_text(encoding="utf-8"), before)
        self.assertTrue(self.store.status()["completed"])

    def test_invalid_choices_are_rejected(self):
        for args in (("bank_x",), ("later", ["ebay"]), ("later", [], "2026-13-01"), ("later", [], "2099-01-01")):
            with self.assertRaises(SetupError, msg=args):
                self.store.save(*args)

    def test_suggested_floor_is_first_of_month_three_months_back(self):
        self.assertEqual(SetupStore.suggested_floor(date(2026, 11, 21)), date(2026, 8, 1))
        self.assertEqual(SetupStore.suggested_floor(date(2026, 2, 15)), date(2025, 11, 1))


class FirstLaunchInitializationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "config").mkdir()
        for name in REQUIRED:
            source = BACKEND / "config" / name
            if source.exists():
                shutil.copy(source, self.root / "config" / name)
            else:
                (self.root / "config" / name).write_text('{"cards": {}}', encoding="utf-8")

    def test_new_workspace_gets_empty_ledger_and_local_collector_config(self):
        status = initialize_workspace(self.root)
        self.assertTrue(status["database_created"])
        with closing(sqlite3.connect(self.root / "data" / "expenses.sqlite")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
        backends = json.loads((self.root / "config" / "collector_backends.json").read_text(encoding="utf-8"))
        self.assertEqual((backends["default_mode"], backends["vps"]["enabled"]), ("local", False))

    def test_existing_machine_config_is_not_replaced(self):
        (self.root / "config" / "collector_backends.json").write_text('{"mine": true}', encoding="utf-8")
        initialize_workspace(self.root)
        self.assertEqual((self.root / "config" / "collector_backends.json").read_text(encoding="utf-8"), '{"mine": true}')


if __name__ == "__main__":
    unittest.main()
