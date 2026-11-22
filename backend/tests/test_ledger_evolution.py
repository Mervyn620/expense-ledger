import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.analysis import AnalysisDataBuilder, AnalysisError, DeepSeekAnalysisService
from app.data_status import data_status, record_run
from app.jobs import JobManager, atomic_write_json
from app.ledger_questions import query_plan, answer_plan
from app.repository import LedgerRepository
from scripts.ledger.schema import create_schema
from test_expense_app import create_test_ledger


@contextmanager
def database(path):
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            yield conn


class LedgerEvolutionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = create_test_ledger(self.root)
        self.repo = LedgerRepository(self.db, self.root / 'exports/current')

    def bank(self, amount=5, direction='credit', currency='CAD', bank_date='2026-07-02'):
        with database(self.db) as conn:
            conn.execute("INSERT OR IGNORE INTO bank_accounts VALUES ('test','Example','Example','0000','now')")
            n = conn.execute('SELECT COUNT(*) FROM bank_transactions').fetchone()[0]
            return conn.execute('''INSERT INTO bank_transactions
                (transaction_id,account_id,date,description,amount,direction,currency,updated_at)
                VALUES (?, 'test', ?, 'Example credit', ?, ?, ?, 'now')''',
                (f'credit-{n}', bank_date, amount, direction, currency)).lastrowid

    def test_refund_changes_net_not_original_order_or_payment(self):
        bank = self.bank()
        detail = self.repo.link_refund(2, bank)
        self.assertEqual(detail['order']['total'], 33.81)
        self.assertEqual(detail['order']['gross_total'], 38.81)
        self.assertEqual(self.repo.overview('2026-06')['summary']['total'], 33.81)
        self.assertEqual(AnalysisDataBuilder(self.repo).build('month')['summary']['total'], 33.81)
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT amount FROM order_payments WHERE order_pk=2').fetchone()[0], 38.81)
            self.assertEqual(conn.execute('SELECT total FROM powerbi_orders WHERE order_pk=2').fetchone()[0], 38.81)
        self.assertEqual(self.repo.link_refund(2, bank)['order']['total'], 33.81)
        self.assertEqual(len(self.repo.order_detail(2)['refunds']), 1)
        self.repo.unlink_refund(2, detail['refunds'][0]['id'])
        self.assertEqual(self.repo.order_detail(2)['order']['total'], 38.81)
        self.assertEqual(self.repo.order_detail(2)['history'][0]['event_type'], 'refund_unlinked')

    def test_refund_rejects_debit_currency_early_and_excess(self):
        for kwargs in ({'direction': 'debit'}, {'currency': 'USD'},
                       {'bank_date': '2026-05-01'}, {'amount': 50}, {'amount': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.repo.link_refund(2, self.bank(**kwargs))

    def test_multiple_partial_refunds_cannot_exceed_order(self):
        self.repo.link_refund(2, self.bank(20))
        self.repo.link_refund(2, self.bank(18.81))
        self.assertEqual(self.repo.order_detail(2)['order']['total'], 0)
        with self.assertRaises(ValueError):
            self.repo.link_refund(2, self.bank(0.01))

    def test_refund_credit_cannot_be_reused(self):
        bank = self.bank()
        self.repo.link_refund(2, bank)
        with self.assertRaises(ValueError):
            self.repo.link_refund(1, bank)

    def test_existing_reconciliation_is_not_refund_candidate(self):
        bank = self.bank()
        with database(self.db) as conn:
            conn.execute("INSERT INTO bank_reconciliations(bank_transaction_pk,order_pk,match_status,updated_at) VALUES (?,1,'matched_order','now')", (bank,))
        self.assertNotIn(bank, [r['id'] for r in self.repo.order_detail(2)['refund_candidates']])
        with self.assertRaises(ValueError):
            self.repo.link_refund(2, bank)

    def test_later_import_cannot_invalidate_refund(self):
        bank = self.bank()
        self.repo.link_refund(2, bank)
        for sql, params in [
            ("UPDATE orders SET total=1 WHERE id=2", ()),
            ("UPDATE orders SET currency='USD' WHERE id=2", ()),
            ("UPDATE bank_transactions SET amount=9 WHERE id=?", (bank,)),
            ("UPDATE bank_transactions SET direction='debit' WHERE id=?", (bank,)),
            ("INSERT INTO bank_reconciliations(bank_transaction_pk,order_pk,match_status,updated_at) VALUES (?,2,'matched','now')", (bank,)),
        ]:
            with self.subTest(sql=sql), database(self.db) as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql, params)
        with database(self.db) as conn:
            conn.execute("UPDATE orders SET updated_at='later' WHERE id=2")
            conn.execute("UPDATE bank_transactions SET updated_at='later' WHERE id=?", (bank,))
        self.assertEqual(self.repo.order_detail(2)['order']['total'], 33.81)

    def test_audit_ignores_touch_and_preserves_deleted_source(self):
        with database(self.db) as conn:
            before = conn.execute('SELECT COUNT(*) FROM order_events').fetchone()[0]
            conn.execute("UPDATE orders SET updated_at='later' WHERE id=1")
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM order_events').fetchone()[0], before)
            conn.execute("UPDATE orders SET total=12,fulfillment='delivered' WHERE id=1")
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('DELETE FROM orders WHERE id=1')
        events = self.repo.recent_changes()
        self.assertEqual(events[0]['event_type'], 'removed')
        self.assertEqual(events[0]['order_id'], 'order-1')
        self.assertIsNone(events[0]['order_pk'])
        changed = next(e for e in events if e['event_type'] == 'changed')
        self.assertEqual(changed['before']['total'], 11.3)
        self.assertEqual(changed['after']['total'], 12)

    def test_migration_baseline_idempotent_and_rollback_has_no_event(self):
        with database(self.db) as conn:
            conn.execute('DELETE FROM order_events')
            conn.execute('PRAGMA user_version=4')
            conn.commit()
            create_schema(conn)
            conn.commit()
            create_schema(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM order_events WHERE event_type='baseline'").fetchone()[0], 2)
            conn.execute('UPDATE orders SET total=99 WHERE id=1')
            conn.rollback()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM order_events').fetchone()[0], 2)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_observation_is_not_last_purchase_or_completeness(self):
        with database(self.db) as conn:
            conn.executemany('INSERT INTO collection_observations VALUES (?,?,?,?,?,?,?,?)', [
                ('a','walmart_ca','vps','passed','2026-09-07T12:00:00+00:00','2026-08-01','bounded',''),
                ('b','walmart_ca','local','failed','2026-09-08T12:00:00+00:00','2026-08-01','bounded','')])
        with closing(self.repo.connect()) as conn:
            row = next(r for r in data_status(conn, self.root, date(2026,9,8)) if r['platform']=='walmart_ca')
        self.assertEqual(row['last_order_date'], '2026-05-20')
        self.assertEqual(row['days_since_check'], 1)
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['backend'], 'local')
        self.assertIsNone(row['complete_through'])

    def test_record_run_idempotent_and_import_failure_not_success(self):
        state = {'collection_run_id': 'run-1', 'collector_backend_resolved': 'vps',
                 'collector_local_only_platforms': ['the_ordinary_ca']}
        weekly = {'collection_run_id': 'run-1', 'collector_runs': [
            {'collector_id':'the_ordinary_ca','phase':'collect','status':'passed','completed_at':'2026-09-08T10:00:00-04:00'},
            {'collector_id':'the_ordinary_ca','phase':'import','status':'failed'}]}
        atomic_write_json(self.root / 'exports/current/run-state.json', state)
        atomic_write_json(self.root / 'exports/current/weekly-status.json', weekly)
        record_run(self.root)
        record_run(self.root)
        with closing(self.repo.connect()) as conn:
            rows = conn.execute('SELECT * FROM collection_observations').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['backend'], 'local')
        self.assertEqual(rows[0]['status'], 'import_failed')
        self.assertEqual(rows[0]['checked_at'], '2026-09-08T14:00:00+00:00')
        weekly['collection_run_id'] = 'wrong-run'
        atomic_write_json(self.root / 'exports/current/weekly-status.json', weekly)
        self.assertEqual(record_run(self.root), 0)

    def test_unknown_freshness_disables_projection(self):
        payload = AnalysisDataBuilder(self.repo, today=date(2026,6,26)).build('month')
        self.assertIsNone(payload['computed_facts']['period_comparison']['projected_month_total'])

    def test_current_month_chat_never_substitutes_an_older_purchase_month(self):
        service = DeepSeekAnalysisService(self.repo, api_key='', cache_path=self.root / 'cache.json')
        service.builder.today = date(2026,9,8)
        with self.assertRaises(AnalysisError) as error:
            service.chat('本月外卖花销是多少')
        self.assertEqual(error.exception.status_code, 404)

    def test_query_plans_explicit_previous_month_and_evidence(self):
        plan = query_plan('2026年6月饭团花了多少', date(2026,9,8))
        result = answer_plan(self.repo, plan)
        self.assertEqual(result['meta']['total'], 38.81)
        self.assertEqual(sum(r['total'] for r in result['evidence']['rows']), 38.81)
        self.assertEqual(query_plan('上个月花了多少', date(2026,1,8))['month'], '2025-12')
        for text in ['2026-06-25饭团多少钱', '本月饭团退款多少钱', '本月饭团和上月比', '本月饭团小费', '本月饭团超过20加币的订单']:
            self.assertIsNone(query_plan(text, date(2026,9,8)), text)

    def test_comparison_uses_each_platform_and_correct_month(self):
        result = answer_plan(self.repo, query_plan('2026年6月饭团比Uber花多少', date(2026,9,8)))
        self.assertEqual(result['query_plan']['operation'], 'compare')
        self.assertIn('Uber Eats CAD 0.00', result['answer'])
        self.assertEqual(result['evidence']['total_count'], 1)

    def test_jobs_restore_and_interrupted_runs_do_not_restart(self):
        manager = JobManager(root=self.root)
        manager._jobs['0123456789ab'] = {'id':'0123456789ab','kind':'validate','status':'running',
            'label':'校验','output':'secret-output','message':'secret-message',
            'metrics':{'count': 1, 'secret':'must-not-persist'}}
        manager._persist_history()
        text = (self.root / 'data/job_history.json').read_text(encoding='utf-8')
        self.assertNotIn('secret', text)
        restored = JobManager(root=self.root).snapshot()
        self.assertIsNone(restored['active_job_id'])
        self.assertEqual(restored['jobs'][0]['status'], 'failed')

    def test_history_write_failure_does_not_leave_running_lock(self):
        manager = JobManager(root=self.root)
        with patch('app.jobs.atomic_write_json', side_effect=OSError('disk full')):
            with self.assertRaises(RuntimeError):
                manager._start('validate', '校验', ['not-launched'])
        self.assertIsNone(manager.snapshot()['active_job_id'])
        self.assertTrue(manager.snapshot()['history_warning'])

    def test_http_refunds_require_confirmation_and_respect_origin_and_job_guard(self):
        import app.main as main
        bank = self.bank()
        manager = JobManager(root=self.root)
        with patch.object(main, 'repository', self.repo), patch.object(main, 'jobs', manager):
            with TestClient(main.app, base_url='http://127.0.0.1') as client:
                path = '/api/orders/2/refunds'
                self.assertEqual(client.post(path,json={'bank_transaction_pk':bank}).status_code, 422)
                payload = {'bank_transaction_pk':bank,'confirmed':True}
                self.assertEqual(client.post(path,json=payload,headers={'origin':'https://example.invalid'}).status_code, 403)
                with patch.object(manager, 'snapshot', return_value={'active_job_id':'busy'}):
                    self.assertEqual(client.post(path,json=payload).status_code, 409)
                response = client.post(path,json=payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['order']['total'], 33.81)
                refund_id = response.json()['refunds'][0]['id']
                self.assertEqual(client.get('/api/order-changes').status_code, 200)
                self.assertEqual(client.delete(f'{path}/{refund_id}').json()['order']['total'], 38.81)

    def test_capped_evidence_does_not_cap_aggregate(self):
        with database(self.db) as conn:
            conn.executemany('''INSERT INTO orders(platform,order_id,date,month,total,currency,updated_at)
                VALUES ('amazon_ca',?,'2026-08-01','2026-08',1,'CAD','now')''',
                [(f'many-{n}',) for n in range(205)])
        result = answer_plan(self.repo, query_plan('2026年8月Amazon花了多少', date(2026,9,8)))
        self.assertEqual(result['meta']['total'], 205)
        self.assertEqual(len(result['evidence']['rows']), 200)
        self.assertTrue(result['evidence']['truncated'])


if __name__ == '__main__':
    unittest.main()
