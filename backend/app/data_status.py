"""Persist observations locally; a successful bounded scan is not proof of completeness."""
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.ledger.schema import create_schema

LABELS = {'walmart_ca': 'Walmart', 'amazon_ca': 'Amazon', 'uber_eats': 'Uber Eats',
          'fantuan': '饭团', 'the_ordinary_ca': 'The Ordinary', 'tnt_ca': '大统华', 'scotiabank': 'Scotiabank', 'bank': '其他银行'}


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def record_run(root):
    root = Path(root)
    state = read_json(root / 'exports/current/run-state.json')
    weekly = read_json(root / 'exports/current/weekly-status.json')
    run_id = state.get('collection_run_id')
    if state.get('status') == 'failed':
        # Fatal exits can precede weekly-status publication. Only consume
        # component attempts whose timestamps belong to this run.
        start, end = (_utc_time(state.get(k)) for k in ('started_at','completed_at'))
        if not start or not end:
            return 0
        runs = []
        for platform in set(state.get('collector_platforms', [])) & LABELS.keys():
            for phase in ('collect','import'):
                row = read_json(root / '.codex/collector-runs' / f'{platform}-{phase}.json')
                began, finished = (_utc_time(row.get(k)) for k in ('started_at','completed_at'))
                if began and finished and start <= began <= finished <= end:
                    runs.append({**row, 'phase': phase, 'collector_id': platform})
        weekly = {'collection_run_id': run_id, 'collector_runs': runs}
        bank_health = read_json(root / '.codex/collector-health.json').get('platforms', {}).get('scotia', {})
        failure = _utc_time(bank_health.get('last_failure'))
        if state.get('reconcile_scotia') and failure and start <= failure <= end:
            weekly['scotia_reconciliation'] = {'enabled': True, 'exported': False}
    if run_id != weekly.get('collection_run_id'):
        return 0
    if not run_id:
        # Scotia-only refreshes have no store collection UUID.
        if not state.get('reconcile_scotia') or state.get('status') not in {'passed', 'partial', 'failed'}:
            return 0
        if not weekly.get('scotia_reconciliation', {}).get('enabled'):
            return 0
        run_id = 'scotia-' + str(state.get('started_at', ''))
    if not (root / 'data/expenses.sqlite').is_file():
        return 0
    backend = state.get('collector_backend_resolved', 'local')
    local_only = state.get('collector_local_only_platforms', [])
    windows = weekly.get('collection_windows', {})
    rows = []
    ranges = []
    health = read_json(root / '.codex/collector-health.json').get('platforms', {})

    def record_range(platform, source, start, end):
        if source == 'local' and start and end and platform == 'uber_eats':
            from scripts.importers.uber_eats import parse_orders
            for path in sorted((root / 'raw').glob('*_uber_eats_*.json'), reverse=True)[:1]:
                snapshot = read_json(path)
                captured = _utc_time(snapshot.get('capturedAt'))
                if captured and start <= captured <= end:
                    dates = [o['transaction']['date'] for o in parse_orders(snapshot, windows.get(platform) or '')]
                    if dates:
                        ranges.append((run_id, platform, source, min(dates), max(dates)))
            return
        info = health.get('scotia' if platform == 'scotiabank' else platform, {})
        success = _utc_time(info.get('last_success'))
        if source != 'local' or not success or not start or not end or not start <= success <= end:
            return
        observed = (info.get('summary') or {}).get('observed_dates') or {}
        try:
            first, last = (date.fromisoformat(observed[k]) for k in ('first','last'))
        except (ValueError, TypeError, KeyError):
            return
        if first <= last:
            ranges.append((run_id, platform, source, first.isoformat(), last.isoformat()))
    runs = weekly.get('collector_runs', [])
    imports = {row.get('collector_id'): row.get('status') for row in runs if row.get('phase') == 'import'}
    for row in runs:
        if row.get('phase') != 'collect' or row.get('collector_id') not in LABELS:
            continue
        platform = row['collector_id']
        checked = _utc_time(row.get('completed_at'))
        if not checked:
            continue
        status = row.get('status', 'unknown')
        if status == 'passed' and imports.get(platform) != 'passed':
            status = 'import_failed' if platform in imports else 'import_unknown'
        rows.append((run_id, platform, 'local' if platform in local_only else backend,
                     status, checked, windows.get(platform, ''), 'bounded', ''))
        if row.get('status') == 'passed':
            record_range(platform, rows[-1][2], _utc_time(row.get('started_at')), checked)
    if state.get('reconcile_scotia') and weekly.get('scotia_reconciliation') and _utc_time(state.get('completed_at')):
        rows.append((run_id, 'scotiabank', 'local',
                     'passed' if weekly.get('scotia_reconciliation', {}).get('exported') else 'failed',
                     _utc_time(state['completed_at']), windows.get('scotia', ''), 'bounded', ''))
        if rows[-1][3] == 'passed':
            record_range('scotiabank', 'local', _utc_time(state.get('started_at')), rows[-1][4])
    with closing(sqlite3.connect(root / 'data/expenses.sqlite', timeout=10)) as conn:
        create_schema(conn)
        conn.executemany('INSERT OR IGNORE INTO collection_observations VALUES (?,?,?,?,?,?,?,?)', rows)
        conn.executemany('INSERT OR IGNORE INTO observed_date_ranges VALUES (?,?,?,?,?)', ranges)
        conn.commit()
    return len(rows)


def _utc_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def data_status(conn, root, today=None):
    today = today or datetime.now().astimezone().date()
    on_demand = read_json(Path(root) / 'config/workflow.json').get('on_demand_collectors', [])
    ledger_ranges = {row[0]: (row[1], row[2]) for row in conn.execute(
        'SELECT platform, MIN(date), MAX(date) FROM main.orders GROUP BY platform')}
    last_orders = {key: dates[1] for key,dates in ledger_ranges.items()}
    # Scotia rows come from the collector; other banks from imported statements.
    for key, operator in (('scotiabank', 'NOT IN'), ('bank', 'IN')):
        bank_range = conn.execute(
            "SELECT MIN(date),MAX(date) FROM bank_transactions "
            f"WHERE COALESCE(source,'') {operator} ('statement_ofx','statement_csv')").fetchone()
        if bank_range[0]:
            ledger_ranges[key] = tuple(bank_range)
    missing = {row['platform']: dict(row) for row in conn.execute("""
        SELECT o.platform, COUNT(DISTINCT CASE WHEN i.id IS NULL AND o.platform NOT IN ('scotia','scotiabank','bank') THEN o.id END) AS no_items,
               COUNT(DISTINCT CASE WHEN i.is_product=0 THEN o.id END) AS placeholder_orders,
               COUNT(CASE WHEN i.is_product=1 AND i.price IS NULL THEN 1 END) AS no_price,
               COUNT(CASE WHEN i.is_product=1 AND trim(COALESCE(i.quantity,''))='' THEN 1 END) AS no_quantity,
               COUNT(CASE WHEN i.is_product=1 AND i.effective_category='uncategorized' THEN 1 END) AS no_category,
               COUNT(CASE WHEN i.is_product=1 AND i.currency<>o.currency THEN 1 END) AS other_currency
        FROM main.orders o LEFT JOIN main.classified_order_items i ON i.order_pk=o.id GROUP BY o.platform
    """)}
    observations = [dict(row) for row in conn.execute(
        '''SELECT a.*,r.first_date,r.last_date FROM collection_observations a
           LEFT JOIN observed_date_ranges r USING(run_id,platform,backend) ORDER BY checked_at DESC,a.rowid DESC''')]
    result = []
    for platform, label in LABELS.items():
        attempts = [row for row in observations if row['platform'] == platform]
        latest = attempts[0] if attempts else {}
        success = next((row for row in attempts if row['status'] == 'passed'), {})
        checked = success.get('checked_at')
        age = None
        if checked:
            try:
                age = max(0, (today - datetime.fromisoformat(checked.replace('Z', '+00:00')).astimezone().date()).days)
            except ValueError:
                pass
        result.append({
            'platform': platform, 'label': label, 'on_demand': platform in on_demand,
            'last_order_date': last_orders.get(platform),
            'ledger_to': ledger_ranges.get(platform, (None,None))[1],
            'ledger_from': ledger_ranges.get(platform, (None,None))[0],
            'observed_from': latest.get('first_date'), 'observed_to': latest.get('last_date'),
            'last_attempt_at': latest.get('checked_at'),
            'last_success_at': checked, 'days_since_check': age,
            'backend': latest.get('backend'), 'status': latest.get('status', 'not_checked'),
            'requested_from': latest.get('requested_from'),
            'missing_fields': {k: v for k, v in missing.get(platform, {}).items() if k != 'platform'},
            'coverage': 'bounded' if success else 'unknown',
            'complete_through': None,
        })
    return result


if __name__ == '__main__':
    print(f"collection_observations_recorded={record_run(Path(__file__).resolve().parents[1])}")
