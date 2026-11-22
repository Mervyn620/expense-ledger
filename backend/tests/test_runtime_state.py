import importlib.util
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ledger" / "runtime_state.py"

spec = importlib.util.spec_from_file_location("runtime_state", SCRIPT)
runtime_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_state)


class RuntimeStateTest(unittest.TestCase):
    def test_missing_database_returns_empty_state(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(runtime_state.read_orders(Path(temp) / "missing.sqlite"), [])

    def test_reads_collection_state_from_sqlite_orders(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "expenses.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE orders (
                        date TEXT,
                        platform TEXT,
                        order_id TEXT,
                        merchant TEXT,
                        total REAL,
                        currency TEXT
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-06-25", "walmart_ca", "order-1", "Walmart", 43.23, "CAD"),
                )
                conn.commit()

            self.assertEqual(runtime_state.read_orders(db_path), [{
                "date": "2026-06-25",
                "platform": "walmart_ca",
                "order_id": "order-1",
                "merchant": "Walmart",
                "total": 43.23,
                "currency": "CAD",
            }])
