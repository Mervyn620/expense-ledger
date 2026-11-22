import argparse
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "expenses.sqlite"
EXPORT_ROOT = ROOT / "exports"
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ledger"
if str(LEDGER_DIR) not in sys.path:
    sys.path.insert(0, str(LEDGER_DIR))

from db import atomic_write_text, write_csv

def money(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def query_rows(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params)]


def fmt(value):
    return f"{money(value):.2f}"


def date_filter(start_date="", end_date="", month=""):
    clauses = []
    params = []
    if month:
        clauses.append("o.month = ?")
        params.append(month)
    if start_date:
        clauses.append("o.date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("o.date <= ?")
        params.append(end_date)
    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def scope_label(start_date="", end_date="", month=""):
    if month:
        return month
    if start_date and end_date:
        return f"{start_date} to {end_date}"
    if start_date:
        return f"from {start_date}"
    if end_date:
        return f"through {end_date}"
    return "all dates"


def ledger_rows(conn, start_date="", end_date="", month=""):
    where, params = date_filter(start_date, end_date, month)
    rows = query_rows(
        conn,
        f"""
        SELECT
          o.year,
          o.month,
          o.date,
          o.platform,
          o.order_id,
          o.merchant,
          o.category,
          COALESCE(p.card_id, 'unassigned') AS card_id,
          COALESCE(c.display_name, COALESCE(p.card_id, 'unassigned')) AS card_label,
          COALESCE(p.status, '') AS assignment_status,
          COALESCE(p.source, '') AS assignment_source,
          COALESCE(p.payment_method, '') AS payment_method,
          COALESCE(p.payment_channel, 'unknown') AS payment_channel,
          COALESCE(
            (
              SELECT e.status
              FROM payment_evidence e
              WHERE e.order_pk = o.id
              ORDER BY e.updated_at DESC, e.id DESC
              LIMIT 1
            ),
            p.status,
            ''
          ) AS payment_status,
          o.item_total,
          o.tax,
          o.fees,
          o.tip,
          o.total,
          o.currency,
          (SELECT COUNT(*) FROM order_items i WHERE i.order_pk = o.id) AS item_rows,
          o.source_url,
          COALESCE(p.notes, o.notes, '') AS notes
        FROM orders o
        LEFT JOIN order_payments p ON p.order_pk = o.id AND p.payment_index = 1
        LEFT JOIN cards c ON c.card_id = p.card_id
        {where}
        ORDER BY o.date, o.platform, o.merchant, o.order_id
        """,
        params,
    )
    for row in rows:
        for field in ["item_total", "tax", "fees", "tip", "total"]:
            row[field] = fmt(row[field])
        row["year"] = str(row["year"] or "")
        row["item_rows"] = str(row["item_rows"] or "")
    return rows


def item_rows(conn, start_date="", end_date="", month=""):
    where, params = date_filter(start_date, end_date, month)
    rows = query_rows(
        conn,
        f"""
        SELECT
          o.year,
          o.month,
          COALESCE(p.card_id, 'unassigned') AS card_id,
          COALESCE(c.display_name, COALESCE(p.card_id, 'unassigned')) AS card_label,
          o.platform,
          o.date,
          o.order_id,
          o.merchant,
          o.category AS order_category,
          o.total AS order_total,
          COALESCE(p.payment_method, '') AS payment_method,
          COALESCE(p.payment_channel, 'unknown') AS payment_channel,
          COALESCE(
            (
              SELECT e.status
              FROM payment_evidence e
              WHERE e.order_pk = o.id
              ORDER BY e.updated_at DESC, e.id DESC
              LIMIT 1
            ),
            p.status,
            ''
          ) AS payment_status,
          COALESCE(p.source, '') AS assignment_source,
          COALESCE(p.notes, '') AS assignment_notes,
          i.category AS item_category,
          i.product_name,
          i.quantity,
          i.price AS item_price,
          i.currency
        FROM orders o
        JOIN order_items i ON i.order_pk = o.id
        LEFT JOIN order_payments p ON p.order_pk = o.id AND p.payment_index = 1
        LEFT JOIN cards c ON c.card_id = p.card_id
        {where}
        ORDER BY o.date, o.platform, o.merchant, o.order_id, i.product_name
        """,
        params,
    )
    for row in rows:
        row["year"] = str(row["year"] or "")
        row["order_total"] = fmt(row["order_total"])
        row["item_price"] = "" if row["item_price"] is None else fmt(row["item_price"])
    return rows


def summary_rows(conn, group_fields, start_date="", end_date="", month=""):
    select_fields = ", ".join(group_fields)
    group_by = ", ".join(group_field_name(field) for field in group_fields)
    where, params = date_filter(start_date, end_date, month)
    return query_rows(
        conn,
        f"""
        SELECT
          {select_fields},
          COUNT(*) AS transactions,
          SUM(o.total) AS total
        FROM orders o
        LEFT JOIN order_payments p ON p.order_pk = o.id AND p.payment_index = 1
        LEFT JOIN cards c ON c.card_id = p.card_id
        {where}
        GROUP BY {group_by}
        ORDER BY {group_by}
        """,
        params,
    )


def group_field_name(field):
    lower = field.lower()
    marker = " as "
    if marker in lower:
        return field[:lower.rfind(marker)].strip()
    return field


def format_summary(rows):
    for row in rows:
        row["transactions"] = str(row["transactions"])
        row["total"] = fmt(row["total"])
    return rows


def review_rows(ledger):
    return [
        {
            "date": row["date"],
            "platform": row["platform"],
            "order_id": row["order_id"],
            "merchant": row["merchant"],
            "category": row["category"],
            "card_id": row["card_id"],
            "card_label": row["card_label"],
            "total": row["total"],
            "currency": row["currency"],
            "payment_method": row["payment_method"],
            "payment_status": row["payment_status"],
            "assignment_source": row["assignment_source"],
            "assignment_notes": row["notes"],
            "suggested_action": "confirm underlying card_id for this payment method",
        }
        for row in ledger
        if row["card_id"] == "unassigned" or row["assignment_status"] == "needs_review"
    ]


def write_dashboard(ledger, source_rows, review, scope, output_dir):
    totals_by_card = defaultdict(float)
    orders_by_card = defaultdict(int)
    for row in ledger:
        totals_by_card[row["card_label"]] += money(row["total"])
        orders_by_card[row["card_label"]] += 1

    lines = [
        "# Accounting Dashboard",
        "",
        "## Snapshot",
        "",
        f"- Scope: {scope}",
        f"- Orders: {len(ledger)}",
        f"- Total CAD: {sum(money(row['total']) for row in ledger):.2f}",
        f"- Waiting for card assignment: {len(review)}",
        "",
        "## Totals By Card",
        "",
        "| Card | Orders | Total CAD |",
        "|---|---:|---:|",
    ]
    for card in sorted(totals_by_card):
        lines.append(f"| {card} | {orders_by_card[card]} | {totals_by_card[card]:.2f} |")

    lines += [
        "",
        "## Totals By Source",
        "",
        "| Month | Card | Source | Orders | Total CAD |",
        "|---|---|---|---:|---:|",
    ]
    for row in source_rows:
        lines.append(
            f"| {row['month']} | {row['card_label']} | {row['platform']} | "
            f"{row['transactions']} | {row['total']} |"
        )

    lines += [
        "",
        "## Orders Needing Review",
        "",
        "| Date | Source | Merchant | Total CAD | Payment Method | Notes |",
        "|---|---|---|---:|---|---|",
    ]
    if review:
        for row in review:
            lines.append(
                f"| {row['date']} | {row['platform']} | {row['merchant']} | {row['total']} | "
                f"{row['payment_method']} | {row['assignment_notes']} |"
            )
    else:
        lines.append("|  |  |  |  |  | No review needed |")

    lines += [
        "",
        "## Useful Files",
        "",
        "- `data/expenses.sqlite`: normalized SQLite ledger.",
        f"- `{(output_dir / 'accounting-ledger.csv').relative_to(ROOT).as_posix()}`: order-level ledger.",
        f"- `{(output_dir / 'card-order-items.csv').relative_to(ROOT).as_posix()}`: item-level detail.",
        "- `data/payment_assignments.csv`: editable card assignment source until direct DB editing is enabled.",
    ]
    atomic_write_text(output_dir / "accounting-dashboard.md", "\n".join(lines) + "\n")


def write_detail_markdown(items, output_dir):
    lines = [
        "# Card Order Item Detail",
        "",
        "Each order total, card assignment, and payment method is repeated beside its item rows so it can be filtered in a spreadsheet.",
        "",
    ]
    grouped = defaultdict(list)
    for row in items:
        grouped[(
            row["card_label"],
            row["platform"],
            row["date"],
            row["order_id"],
            row["merchant"],
            row["order_total"],
            row["payment_method"],
            row["payment_status"],
        )].append(row)

    for (card, platform, date, order_id, merchant, total, method, status), rows in sorted(grouped.items()):
        lines += [
            f"## {card} / {platform}",
            "",
            f"- Date: {date}",
            f"- Order: {order_id}",
            f"- Merchant: {merchant}",
            f"- Order total: CAD {total}",
            f"- Payment method: {method or 'unknown'}",
            f"- Payment status: {status or 'unknown'}",
            "",
            "| Item | Category | Qty | Item Price |",
            "|---|---|---:|---:|",
        ]
        for row in rows:
            lines.append(
                f"| {row['product_name']} | {row['item_category']} | {row['quantity']} | {row['item_price']} |"
            )
        lines.append("")

    atomic_write_text(output_dir / "card-order-detail.md", "\n".join(lines) + "\n")


def write_card_summary(cards, source_rows, category_rows, review, output_dir):
    active_card_ids = {row["card_id"] for row in source_rows}
    if review:
        active_card_ids.update(row["card_id"] for row in review)
    active_cards = [row for row in cards if row["card_id"] in active_card_ids]

    lines = [
        "# Card Spending Summary",
        "",
        "## Cards",
        "",
        "| Card | Network | Issuer | Last4 | Type |",
        "|---|---|---|---:|---|",
    ]
    for row in active_cards:
        lines.append(
            f"| {row['display_name']} | {row['network'] or ''} | {row['issuer'] or ''} | "
            f"{row['last4'] or ''} | {row['kind'] or ''} |"
        )

    lines += [
        "",
        "## Source By Card",
        "",
        "| Month | Card | Source | Transactions | Total CAD |",
        "|---|---|---|---:|---:|",
    ]
    for row in source_rows:
        lines.append(
            f"| {row['month']} | {row['card_label']} | {row['platform']} | "
            f"{row['transactions']} | {row['total']} |"
        )

    lines += [
        "",
        "## Category By Card",
        "",
        "| Month | Card | Source | Category | Transactions | Total CAD |",
        "|---|---|---|---|---:|---:|",
    ]
    for row in category_rows:
        lines.append(
            f"| {row['month']} | {row['card_label']} | {row['platform']} | {row['category']} | "
            f"{row['transactions']} | {row['total']} |"
        )

    lines += [
        "",
        "## Review",
        "",
        f"- Orders waiting for card assignment: {len(review)}",
    ]
    atomic_write_text(output_dir / "card-spending-summary.md", "\n".join(lines) + "\n")


def output_dirs(start_date="", end_date="", month=""):
    if month:
        base = EXPORT_ROOT / "periods" / month
    elif start_date or end_date:
        start_label = start_date or "beginning"
        end_label = end_date or "latest"
        base = EXPORT_ROOT / "periods" / f"{start_label}_to_{end_label}"
    else:
        base = EXPORT_ROOT
    return base / "current", base / "summaries"


def export_all(db_path=DB_PATH, start_date="", end_date="", month=""):
    if not Path(db_path).exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    current_dir, summary_dir = output_dirs(start_date, end_date, month)
    current_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        ledger = ledger_rows(conn, start_date, end_date, month)
        items = item_rows(conn, start_date, end_date, month)
        cards = query_rows(conn, "SELECT * FROM cards ORDER BY card_id")
        source = format_summary(summary_rows(conn, [
            "o.month",
            "COALESCE(p.card_id, 'unassigned') AS card_id",
            "COALESCE(c.display_name, COALESCE(p.card_id, 'unassigned')) AS card_label",
            "o.platform",
            "o.currency",
        ], start_date, end_date, month))
        category = format_summary(summary_rows(conn, [
            "o.month",
            "COALESCE(p.card_id, 'unassigned') AS card_id",
            "COALESCE(c.display_name, COALESCE(p.card_id, 'unassigned')) AS card_label",
            "o.platform",
            "o.category",
            "o.currency",
        ], start_date, end_date, month))
        yearly = format_summary(summary_rows(conn, ["o.year", "o.currency"], start_date, end_date, month))
        monthly = format_summary(summary_rows(conn, ["o.year", "o.month", "o.currency"], start_date, end_date, month))
        monthly_card = format_summary(summary_rows(conn, [
            "o.year",
            "o.month",
            "COALESCE(p.card_id, 'unassigned') AS card_id",
            "COALESCE(c.display_name, COALESCE(p.card_id, 'unassigned')) AS card_label",
            "o.currency",
        ], start_date, end_date, month))
        monthly_category = format_summary(summary_rows(conn, [
            "o.year",
            "o.month",
            "o.category",
            "o.currency",
        ], start_date, end_date, month))

    review = review_rows(ledger)

    write_csv(current_dir / "accounting-ledger.csv", [
        "year", "month", "date", "platform", "order_id", "merchant", "category",
        "card_id", "card_label", "assignment_status", "assignment_source",
        "payment_method", "payment_channel", "payment_status", "item_total", "tax", "fees", "tip",
        "total", "currency", "item_rows", "source_url", "notes",
    ], ledger)
    write_csv(current_dir / "card-order-items.csv", [
        "year", "month", "card_id", "card_label", "platform", "date", "order_id",
        "merchant", "order_category", "order_total", "payment_method", "payment_channel",
        "payment_status", "assignment_source", "assignment_notes", "item_category",
        "product_name", "quantity", "item_price", "currency",
    ], items)
    write_csv(summary_dir / "card-source-summary.csv", [
        "month", "card_id", "card_label", "platform", "currency", "transactions", "total",
    ], source)
    write_csv(summary_dir / "card-category-summary.csv", [
        "month", "card_id", "card_label", "platform", "category", "currency", "transactions", "total",
    ], category)
    write_csv(current_dir / "card-assignment-review.csv", [
        "date", "platform", "order_id", "merchant", "category", "total", "currency",
        "card_id", "card_label",
        "payment_method", "payment_status", "assignment_source", "assignment_notes", "suggested_action",
    ], review)
    write_csv(summary_dir / "yearly-summary.csv", [
        "year", "currency", "transactions", "total",
    ], yearly)
    write_csv(summary_dir / "monthly-summary.csv", [
        "year", "month", "currency", "transactions", "total",
    ], monthly)
    write_csv(summary_dir / "monthly-card-summary.csv", [
        "year", "month", "card_id", "card_label", "currency", "transactions", "total",
    ], monthly_card)
    write_csv(summary_dir / "monthly-category-summary.csv", [
        "year", "month", "category", "currency", "transactions", "total",
    ], monthly_category)

    scope = scope_label(start_date, end_date, month)
    write_dashboard(ledger, source, review, scope, current_dir)
    write_detail_markdown(items, current_dir)
    write_card_summary(cards, source, category, review, summary_dir)

    return {
        "orders": len(ledger),
        "items": len(items),
        "review": len(review),
        "total": sum(money(row["total"]) for row in ledger),
        "scope": scope,
        "output_dir": str(current_dir.parent),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", help="Export one month, formatted as YYYY-MM")
    parser.add_argument("--start-date", help="Export orders on or after this YYYY-MM-DD date")
    parser.add_argument("--end-date", help="Export orders on or before this YYYY-MM-DD date")
    args = parser.parse_args()

    stats = export_all(month=args.month or "", start_date=args.start_date or "", end_date=args.end_date or "")
    print(
        "Exported SQLite reports. "
        f"scope={stats['scope']} "
        f"orders={stats['orders']} items={stats['items']} "
        f"review={stats['review']} total={stats['total']:.2f}"
        f" output={stats['output_dir']}"
    )


if __name__ == "__main__":
    main()
