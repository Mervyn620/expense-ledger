import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "expenses.sqlite"


def read_orders(db_path=DB_PATH):
    db_path = Path(db_path)
    if not db_path.exists():
        return []

    with closing(sqlite3.connect(db_path)) as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
        ).fetchone()
        if not table_exists:
            return []

        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT date, platform, order_id, merchant, total, currency
            FROM orders
            ORDER BY date, platform, order_id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    print(json.dumps(read_orders(args.db), ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
