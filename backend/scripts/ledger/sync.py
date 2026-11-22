import argparse
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from db import recover_csv_publications, sync_all


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--recover-csv', action='store_true', help='Publish committed CSV exports without re-importing data.')
    args = parser.parse_args()
    if args.recover_csv:
        print(f'csv_publications_recovered={recover_csv_publications()}')
        return
    counts = sync_all()
    print(
        "Synced SQLite ledger. "
        f"orders={counts['orders']} items={counts['order_items']} "
        f"payments={counts['order_payments']} evidence={counts['payment_evidence']} "
        f"total={counts['total']:.2f}"
    )


if __name__ == "__main__":
    main()
