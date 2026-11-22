import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.analysis import AnalysisDataBuilder, AnalysisError
from app.data_status import data_status, record_run
from app.ledger_review import confirm_match, set_product_rule
from app.repository import LedgerRepository
from scripts.reconciliation import scotia
from scripts.importers import fantuan
from scripts.ledger import db as ledger_db
from test_expense_app import create_test_ledger


class LedgerTrustTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = create_test_ledger(self.root)
        self.repo = LedgerRepository(self.db, self.root / 'exports/current')
        self.bank = dict(transaction_id='bank-a', account_id='test', date='2026-05-21',
                         description='Pos Purchase Walmart.ca', amount=11.30, direction='debit', currency='CAD')
        self.order = dict(platform='walmart_ca', order_id='order-1', date='2026-05-20',
                          total='11.30', currency='CAD', merchant='Walmart')

    def execute(self, sql, args=()):
        with closing(sqlite3.connect(self.db)) as conn:
            cursor = conn.execute(sql,args)
            conn.commit()
            return cursor.lastrowid

    def stage_bank(self, status='unmatched_platform_review', linked_pk=None):
        self.execute("INSERT INTO bank_accounts VALUES ('test','test','test','0000','now')")
        pk = self.execute("""INSERT INTO bank_transactions(transaction_id,account_id,date,description,amount,
            direction,currency,updated_at) VALUES (:transaction_id,:account_id,:date,:description,:amount,
            :direction,:currency,'now')""", self.bank)
        self.execute("""INSERT INTO bank_reconciliations(bank_transaction_pk,order_pk,match_status,updated_at)
            VALUES (?,?,?,'now')""", (pk,linked_pk,status))
        return pk

    def test_candidate_conflict_currency_reference_and_competing_debits(self):
        orders = [self.order,{**self.order, 'order_id':'order-2'}, {**self.order,'currency':'USD','order_id':'usd'}]
        candidates = scotia.review_candidates(self.bank,orders)
        self.assertEqual([o['order_id'] for o in candidates], ['order-1','order-2'])
        plans, _ = scotia.plan_existing_order_matches([{**self.bank, 'description':'Pos Purchase Walmart.ca order-2'}], orders)
        self.assertEqual(plans['bank-a']['order']['order_id'], 'order-2')
        plans, attempts = scotia.plan_existing_order_matches([self.bank,{**self.bank,'transaction_id':'bank-b'}], [self.order])
        self.assertEqual(plans,{})
        self.assertTrue(all(reason=='ambiguous_candidates' for reason,_ in attempts.values()))
        self.assertFalse(scotia.find_reversals([self.bank,{**self.bank,'currency':'USD','direction':'credit',
                                                           'transaction_id':'other'}])[0])

    def test_missing_price_stays_null_and_free_line_stays_zero_across_imports(self):
        self.execute("INSERT OR IGNORE INTO cards(card_id,display_name) VALUES ('unassigned','Unassigned')")
        orders = {'orders': [dict(order_id='sample', date='2026-07-01', total=10, goods=[
            dict(name='No price'), dict(name='Unreadable',price='—'), dict(name='Free sample',price=0)])]}
        tx, items, _, _ = fantuan.normalize_payload(orders)
        for _ in range(2):
            ledger_db.import_orders_with_csv(tx,items,root=self.root,item_filename='delivery_items.csv')
        with closing(self.repo.connect()) as conn:
            rows = conn.execute("SELECT product_name,price FROM order_items WHERE order_pk="
                                "(SELECT id FROM orders WHERE order_id='sample')")
            self.assertEqual(dict(rows), {'No price':None, 'Unreadable':None, 'Free sample':0})

    def test_confirm_bank_placeholder_then_rerun_twice(self):
        synthetic = {**self.order,'platform':'scotiabank','order_id':'scotia-bank-a','fulfillment':'bank_reconciled'}
        pk = self.execute("""INSERT INTO orders(platform,order_id,date,month,total,currency,fulfillment,updated_at)
                            VALUES ('scotiabank','scotia-bank-a','2026-05-21','2026-05',11.30,'CAD','bank_reconciled','now')""")
        self.stage_bank('bank_only_imported',pk)
        files = dict(ROOT=self.root, DB_PATH=self.db, TRANSACTIONS_PATH=self.root/'data/transactions.csv',
                     DELIVERY_ITEMS_PATH=self.root/'data/delivery_items.csv', BANK_ITEMS_PATH=self.root/'data/bank_items.csv',
                     ASSIGNMENTS_PATH=self.root/'data/payment_assignments.csv', SCOTIA_PATH=self.root/'data/scotia.json',
                     REPORT_DIR=self.root/'exports/reconciliation',REPORT_CSV=self.root/'exports/reconciliation/scotia.csv',
                     REPORT_MD=self.root/'exports/reconciliation/scotia.md')
        scotia.write_csv(files['TRANSACTIONS_PATH'],scotia.TRANSACTION_FIELDS,[self.order,synthetic])
        self.assertEqual(self.repo.overview('2026-05')['summary']['total'],22.60)
        self.assertEqual(len(self.repo.reconciliation_review()[0]['candidates']),1)
        for _ in range(2):
            confirm_match(self.repo,'bank-a','walmart_ca','order-1')
        self.assertEqual(self.repo.overview('2026-05')['summary']['total'],11.30)
        self.assertEqual(self.repo.reconciliation_review(),[])
        self.assertEqual(len(scotia.read_csv(files['TRANSACTIONS_PATH'],recover=False)),1)
        files['SCOTIA_PATH'].write_text(json.dumps({'account_id':'test','transactions':[self.bank]}), encoding='utf8')
        account = dict(account_id='test',display_name='test',last4='0000')
        with patch.multiple(scotia,**files), patch.object(scotia,'account_details',return_value=account):
            for _ in range(2):
                self.assertEqual(scotia.reconcile()['matched'],1)
        detail = self.repo.order_detail(1)
        self.assertEqual(detail['bank_matches'][0]['match_method'],'manual_confirmed')
        self.assertEqual(detail['bank_matches'][0]['match_id'],'bank-a')
        self.assertEqual(detail['order']['evidence_status'],'bank_matched')
        self.assertEqual(self.repo.overview('2026-05')['summary']['order_count'],1)

    def test_stale_confirmation_and_missing_candidate_remain_held(self):
        self.stage_bank()
        self.execute("UPDATE orders SET currency='USD' WHERE id=1")
        self.assertEqual(self.repo.reconciliation_review()[0]['candidates'],[])
        with self.assertRaises(ValueError):
            confirm_match(self.repo,'bank-a','walmart_ca','order-1')

    def test_mixed_receipt_refund_split_unknown_price_and_reimport(self):
        self.execute("UPDATE orders SET total=22.11,category='mixed' WHERE id=1")
        insert = """INSERT INTO order_items(order_pk,line_key,product_name,category,price,updated_at)
                    VALUES (1,?,?,?,?, 'now')"""
        key = self.execute(insert, ('second','Plate','uncategorized',8.50))
        self.execute(insert, ('third','Unknown','uncategorized',None))
        set_product_rule(self.repo,1,key,'Plate','kitchenware')
        self.execute("DELETE FROM order_items WHERE line_key='second'")
        self.execute(insert, ('reimport','Plate','uncategorized',8.50))
        self.execute("""INSERT INTO order_payments(order_pk,payment_index,amount,currency,updated_at)
                        VALUES(1,2,4.00,'CAD','now')""")
        self.bank.update(direction='credit',amount=5,date='2026-06-01')
        credit = self.stage_bank('credit')
        self.repo.link_refund(1,credit)
        result = self.repo.overview('2026-05')
        self.assertEqual({r['key']:r['total'] for r in result['categories']},
                         dict(groceries=10,kitchenware=8.50,unallocated=-1.39))
        self.assertEqual(result['summary']['total'],17.11)
        self.assertEqual(self.repo.overview('2026-06')['summary']['total'],38.81)
        builder = AnalysisDataBuilder(self.repo)
        data = builder.build('trend')
        self.assertEqual(data['data_quality']['unknown_price_rows'],1)
        scoped = builder.build('trend',categories=['kitchenware'])
        self.assertEqual(scoped['summary']['total'],8.50)
        self.assertEqual(scoped['orders'][0]['items'][0]['name'],'Plate')
        self.execute("UPDATE orders SET currency='CNY' WHERE id=2")
        self.assertEqual(self.repo.overview('all')['summary']['total'],17.11)
        with self.assertRaises(AnalysisError):
            builder.build('trend')

    def test_partial_receipt_does_not_attribute_missing_subtotal_to_the_only_visible_item(self):
        self.execute("UPDATE orders SET item_total=20,total=21.30 WHERE id=1")
        allocations = self.repo.order_detail(1)['allocations']
        self.assertEqual({r['category']:r['cents'] for r in allocations},
                         {'groceries':1000,'unallocated':1130})

    def test_coverage_records_only_current_run_dates(self):
        def save(name,payload):
            path = self.root/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(payload), encoding='utf8')
        start,end='2026-07-01T10:00:00+00:00','2026-07-01T10:05:00+00:00'
        row=dict(collector_id='walmart_ca',phase='collect',started_at=start,completed_at=end,status='passed')
        save('exports/current/run-state.json',dict(collection_run_id='1',status='passed'))
        save('exports/current/weekly-status.json',dict(collection_run_id='1',collector_runs=[row,{**row,'phase':'import'}]))
        save('.codex/collector-health.json',dict(platforms={'walmart_ca':dict(last_success=end,
             summary={'observed_dates':dict(first='2026-06-03',last='2026-06-21')})}))
        record_run(self.root)
        with closing(self.repo.connect()) as conn:
            result=data_status(conn,self.root)[0]
        self.assertEqual((result['observed_from'],result['observed_to']),('2026-06-03','2026-06-21'))
        self.assertIsNone(result['complete_through'])
        self.assertNotEqual(result['ledger_from'],result['observed_from'])
        row.update(started_at='2026-07-04T10:00:00Z',completed_at='2026-07-04T10:05:00Z',status='failed')
        save('exports/current/run-state.json',dict(collection_run_id='2',status='failed',
             started_at=row['started_at'],completed_at=row['completed_at'],collector_platforms=['walmart_ca']))
        save('.codex/collector-runs/walmart_ca-collect.json',row)
        record_run(self.root)
        with closing(self.repo.connect()) as conn:
            result=data_status(conn,self.root)[0]
        self.assertEqual(result['status'],'failed')
        self.assertIsNone(result['observed_from'])
        self.assertEqual(result['last_success_at'],end)
