import csv
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reporting" / "status_report.py"

spec = importlib.util.spec_from_file_location("status_report", SCRIPT)
status_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status_report)


class StatusReportTest(unittest.TestCase):
    def test_report_includes_machine_readable_workflow_state(self):
        with tempfile.TemporaryDirectory() as temp:
            current = Path(temp)
            status_path = current / "weekly-status.json"
            run_state_path = current / "run-state.json"
            ledger_path = current / "accounting-ledger.csv"
            review_path = current / "card-assignment-review.csv"

            status_path.write_text(json.dumps({
                "generated_at": "2026-06-27T02:00:00-04:00",
                "validation": "passed",
            }), encoding="utf-8")
            run_state_path.write_text(
                json.dumps({"status": "passed"}),
                encoding="utf-8",
            )
            for path in (ledger_path, review_path):
                with path.open("w", encoding="utf-8", newline="") as handle:
                    csv.writer(handle).writerow(["total"])

            with (
                patch.object(status_report, "STATUS_JSON", status_path),
                patch.object(status_report, "RUN_STATE_JSON", run_state_path),
                patch.object(status_report, "LEDGER_CSV", ledger_path),
                patch.object(status_report, "REVIEW_CSV", review_path),
                patch.object(status_report, "RAW_DIR", current / "raw"),
            ):
                report = status_report.build_report()

            self.assertIn("- Workflow state: passed", report)
            self.assertIn("- Live collection: unknown", report)

    def test_report_distinguishes_partial_collection_from_passed_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            current = Path(temp)
            status_path = current / "weekly-status.json"
            run_state_path = current / "run-state.json"
            ledger_path = current / "accounting-ledger.csv"
            review_path = current / "card-assignment-review.csv"
            status_path.write_text(json.dumps({
                "generated_at": "2026-08-12T12:00:00-04:00",
                "validation": "passed",
                "collection_status": "partial",
                "warnings": ["The Ordinary reused the last valid cache."],
            }), encoding="utf-8")
            run_state_path.write_text(
                json.dumps({"status": "partial"}), encoding="utf-8"
            )
            for path in (ledger_path, review_path):
                with path.open("w", encoding="utf-8", newline="") as handle:
                    csv.writer(handle).writerow(["total"])

            with (
                patch.object(status_report, "STATUS_JSON", status_path),
                patch.object(status_report, "RUN_STATE_JSON", run_state_path),
                patch.object(status_report, "LEDGER_CSV", ledger_path),
                patch.object(status_report, "REVIEW_CSV", review_path),
                patch.object(status_report, "RAW_DIR", current / "raw"),
            ):
                report = status_report.build_report()

            self.assertIn("- Workflow state: partial", report)
            self.assertIn("- Live collection: partial", report)
            self.assertIn("- Validation: passed", report)

    def test_report_surfaces_current_component_failure_without_generic_bundle_error(self):
        warning = status_report.current_collector_warnings({
            "collector_runs": [{
                "collector_id": "walmart_ca",
                "label": "Walmart Canada",
                "phase": "collect",
                "status": "failed",
                "message": "Timed out waiting for Walmart page content.",
            }],
        })

        self.assertEqual(
            warning,
            [
                "Walmart Canada collection failed: "
                "Timed out waiting for Walmart page content."
            ],
        )
