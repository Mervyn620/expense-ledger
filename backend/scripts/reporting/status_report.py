import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CURRENT_DIR = ROOT / "exports" / "current"
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import atomic_write_text

STATUS_JSON = CURRENT_DIR / "weekly-status.json"
RUN_STATE_JSON = CURRENT_DIR / "run-state.json"
LEDGER_CSV = CURRENT_DIR / "accounting-ledger.csv"
REVIEW_CSV = CURRENT_DIR / "card-assignment-review.csv"
RUN_REPORT = CURRENT_DIR / "run-report.md"
RAW_DIR = ROOT / "raw"


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def money(value):
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def raw_snapshot_count():
    if not RAW_DIR.exists():
        return 0
    return sum(
        1
        for path in RAW_DIR.iterdir()
        if path.is_file()
        and any(marker in path.name for marker in [
            "_walmart_ca_",
            "_uber_eats_",
            "_fantuan_",
        ])
    )


def ledger_total(rows):
    return sum((money(row.get("total")) for row in rows), Decimal("0.00"))


def payment_counts(rows):
    confirmed = 0
    inferred = 0
    review = 0
    for row in rows:
        status = row.get("assignment_status", "")
        card_id = row.get("card_id", "")
        if status == "confirmed" and card_id != "unassigned":
            confirmed += 1
        if status == "inferred" and card_id != "unassigned":
            inferred += 1
        if status == "needs_review" or card_id == "unassigned":
            review += 1
    return confirmed, inferred, review


def current_collector_warnings(status):
    warnings = []
    for row in status.get("collector_runs") or []:
        if row.get("phase") != "collect" or row.get("status") == "passed":
            continue
        label = row.get("label") or row.get("collector_id") or "Collector"
        state = "failed" if row.get("status") == "failed" else "was not completed"
        message = str(row.get("message") or "").strip()
        warning = f"{label} collection {state}"
        if message:
            warning += f": {message}"
        warnings.append(warning)
    return warnings


def build_report():
    status = read_json(STATUS_JSON, {})
    run_state = read_json(RUN_STATE_JSON, {})
    ledger = read_csv(LEDGER_CSV)
    review = read_csv(REVIEW_CSV)
    confirmed, inferred, review_count = payment_counts(ledger)
    raw_count = raw_snapshot_count()
    platform_totals = status.get("platform_totals") or []
    warnings = list(status.get("warnings") or [])
    for warning in current_collector_warnings(status):
        if warning not in warnings:
            warnings.append(warning)
    new_orders = status.get("new_orders") or []
    collector_health = status.get("collector_health") or []
    validation = status.get("validation") or "unknown"
    collection_status = status.get("collection_status") or "unknown"

    lines = [
        "# Expense Run Report",
        "",
        f"- Workflow state: {run_state.get('status', 'unknown')}",
        f"- Generated status: {status.get('generated_at', 'unknown')}",
        f"- Validation: {validation}",
        f"- Live collection: {collection_status}",
        f"- New orders: {status.get('new_order_count', 0)}",
        f"- New total: CAD {money(status.get('new_total'))}",
        f"- Ledger rows: {len(ledger)}",
        f"- Ledger total: CAD {ledger_total(ledger)}",
        f"- Confirmed payment sources: {status.get('confirmed_payment_count', confirmed)}",
        f"- Inferred payment sources: {status.get('inferred_payment_count', inferred)}",
        f"- Payment rows needing review: {status.get('review_payment_count', review_count)}",
        f"- Raw platform snapshots remaining: {raw_count}",
        "",
        "## Platform Totals",
        "",
        "| Platform | Latest Date | Orders | Total CAD |",
        "|---|---:|---:|---:|",
    ]
    for row in platform_totals:
        lines.append(
            f"| {row.get('platform', '')} | {row.get('latest_date', '')} | "
            f"{row.get('orders', 0)} | {money(row.get('total'))} |"
        )

    lines += [
        "",
        "## New Orders",
        "",
        "| Date | Platform | Merchant | Order ID | Total CAD |",
        "|---|---|---|---|---:|",
    ]
    if new_orders:
        for row in new_orders:
            lines.append(
                f"| {row.get('date', '')} | {row.get('platform', '')} | "
                f"{row.get('merchant', '')} | {row.get('order_id', '')} | "
                f"{money(row.get('total'))} |"
            )
    else:
        lines.append("|  |  | No new orders |  | 0.00 |")

    lines += [
        "",
        "## Collector Health",
        "",
        "| Platform | Status | Last Success | Cooldown Until |",
        "|---|---|---|---|",
    ]
    if collector_health:
        for row in collector_health:
            lines.append(
                f"| {row.get('platform', '')} | {row.get('status', '')} | "
                f"{row.get('last_success', '')} | {row.get('blocked_until', '')} |"
            )
    else:
        lines.append("|  | Not collected in this workspace yet |  |  |")

    lines += [
        "",
        "## Review Rows",
        "",
        "| Date | Platform | Merchant | Order ID | Total CAD | Suggested Action |",
        "|---|---|---|---|---:|---|",
    ]
    if review:
        for row in review:
            lines.append(
                f"| {row.get('date', '')} | {row.get('platform', '')} | "
                f"{row.get('merchant', '')} | {row.get('order_id', '')} | "
                f"{money(row.get('total'))} | {row.get('suggested_action', '')} |"
            )
    else:
        lines.append("|  |  |  |  | 0.00 | No review needed |")

    if warnings:
        lines += ["", "## Warnings", ""]
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines += ["", "## Warnings", "", "- None"]

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(RUN_REPORT))
    args = parser.parse_args()
    report = build_report()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output, report)
    print(f"Wrote {output}")


if __name__ == "__main__":
    raise SystemExit(main())
