import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.ledger.schema import create_schema
from scripts.maintenance import stability_suite


class StabilitySuiteTest(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(stability_suite.percentile([1, 2, 3, 4], 95), 4)
        self.assertEqual(stability_suite.percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(stability_suite.percentile([], 95), 0)

    def test_database_checks_are_read_only_and_validate_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "expenses.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                create_schema(conn)
                conn.commit()
            before = db_path.stat()

            with patch.object(stability_suite, "DB_PATH", db_path):
                result = stability_suite.database_checks()

            after = db_path.stat()
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["integrity"], "ok")
            self.assertEqual(result["foreign_key_violations"], 0)
            self.assertEqual(result["missing_tables"], [])
            self.assertEqual(result["missing_views"], [])
            self.assertEqual(before.st_size, after.st_size)
            self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    def test_summarizes_python_and_node_test_outputs(self):
        self.assertEqual(
            stability_suite.summarize_test_output(
                "...\nRan 52 tests in 3.2s\nOK"
            ),
            "Ran 52 tests in 3.2s",
        )
        self.assertEqual(
            stability_suite.summarize_test_output("# tests 8\n# pass 8"),
            "tests 8",
        )

    def test_history_comparison_flags_large_latency_regression(self):
        history = [
            {
                "status": "passed",
                "http_load": {
                    "p95_ms": value,
                    "requests_per_second": 200,
                },
            }
            for value in (40, 45, 50)
        ]
        baseline, warnings = stability_suite.compare_with_history(
            {
                "p95_ms": 300,
                "requests_per_second": 190,
            },
            history,
        )

        self.assertEqual(baseline["sample_count"], 3)
        self.assertEqual(baseline["median_p95_ms"], 45)
        self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
