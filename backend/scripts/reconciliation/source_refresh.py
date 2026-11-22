"""Read-only bank/order comparison. A plan opens no pages and changes no ledger."""
import argparse
import csv
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import scotia

CONTRACT = 'expense-reconciliation-source-plan/v1'

def build_plan(bank, orders, manifest, floor, already_attempted=()):
    floor_date = date.fromisoformat(floor)
    if not isinstance(bank, dict) or not isinstance(bank.get('transactions'), list):
        raise ValueError('Sanitized bank transaction list is invalid.')
    available = {row['ledger_platform']: row['id'] for row in manifest['collectors']
                 if row.get('enabled') and row.get('role') == 'orders'}
    rows = []; seen = set()
    for row in bank['transactions']:
        key = row.get('transaction_id')
        if not key or key in seen:
            raise ValueError('Bank transaction identifiers are missing or duplicated.')
        seen.add(key)
        try:
            posted = date.fromisoformat(row['date'])
            amount = Decimal(str(row['amount']))
        except (KeyError, ValueError, InvalidOperation):
            raise ValueError('Bank transaction date or amount is invalid.') from None
        if not amount.is_finite() or amount < 0 or row.get('direction') not in {'credit','debit'}:
            raise ValueError('Bank transaction amount or direction is invalid.')
        if posted >= floor_date: rows.append(row)
    orders = [row for row in orders if not str(row.get('order_id','')).startswith(scotia.BANK_ONLY_ORDER_PREFIXES)]
    reversed_ids, _ = scotia.find_reversals(rows)
    matches, attempts = scotia.plan_existing_order_matches(rows, orders, reversed_ids)
    result = {platform: {'collector_id': collector, 'action': 'not_present',
                       'bank_charges': 0, 'matched_charges': 0,
                       'missing_charges': 0, 'ambiguous_charges': 0}
              for platform, collector in available.items()}
    missing_dates = {}
    for row in rows:
        key = row['transaction_id']; description = row.get('description','')
        if key in reversed_ids or row['direction'] != 'debit' or scotia.is_transfer(description) or not scotia.is_purchase(description):
            continue
        profile = scotia.merchant_profile(description)
        platform = profile.get('platform')
        # Memberships, in-store purchases and unsupported merchants need no order page.
        if platform not in result or not profile.get('requires_order_match'): continue
        target = result[platform]; target['bank_charges'] += 1
        if key in matches: target['matched_charges'] += 1
        elif attempts.get(key, ('no_candidate',0))[0] == 'ambiguous_candidates':
            target['ambiguous_charges'] += 1
        else:
            target['missing_charges'] += 1
            missing_dates.setdefault(platform, []).append(date.fromisoformat(row['date']))
    for platform, target in result.items():
        if target['missing_charges']:
            target['action'] = 'already_attempted' if target['collector_id'] in already_attempted else 'refresh'
            target['min_date'] = max(floor_date, min(missing_dates[platform])-timedelta(days=3)).isoformat()
        elif target['ambiguous_charges']: target['action'] = 'review'
        elif target['bank_charges']: target['action'] = 'cached_match'
    return {'contract': CONTRACT, 'read_only': True,
            'refresh_platforms': [row['collector_id'] for row in result.values() if row['action']=='refresh'],
            'platforms': list(result.values())}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--floor', required=True)
    parser.add_argument('--already-attempted', default='')
    args = parser.parse_args()
    root = Path.cwd()
    bank = json.loads((root/'data/scotia_transactions.json').read_text(encoding='utf-8-sig'))
    manifest = json.loads((root/'config/collectors.json').read_text(encoding='utf-8-sig'))
    # CSV recovery has already run in the workflow. Never repair or import here.
    with (root/'data/transactions.csv').open(encoding='utf-8-sig', newline='') as source:
        orders = list(csv.DictReader(source))
    print(json.dumps(build_plan(bank,orders,manifest,args.floor,args.already_attempted.split(','))))

if __name__=='__main__': main()
