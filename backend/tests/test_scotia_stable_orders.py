import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.repository import LedgerRepository
from scripts.reconciliation import scotia
import db


class ScotiaStableOrdersTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'data').mkdir()
        (self.root / 'config').mkdir()
        self.database = self.root / 'data/expenses.sqlite'
        cards = {'cards': {key: {'display_name': key, 'kind': 'unknown'}
                           for key in ('unassigned', 'account_debit')}}
        (self.root / 'config/payment_cards.json').write_text(json.dumps(cards), encoding='utf-8')
        paths = {'ROOT': self.root, 'DB_PATH': self.database,
                 'SCOTIA_PATH': self.root / 'data/scotia_transactions.json',
                 'TRANSACTIONS_PATH': self.root / 'data/transactions.csv',
                 'DELIVERY_ITEMS_PATH': self.root / 'data/delivery_items.csv',
                 'BANK_ITEMS_PATH': self.root / 'data/bank_items.csv',
                 'ASSIGNMENTS_PATH': self.root / 'data/payment_assignments.csv',
                 'PAYMENT_CONFIG_PATH': self.root / 'config/payment_cards.json',
                 'REPORT_DIR': self.root / 'exports/reconciliation',
                 'REPORT_CSV': self.root / 'exports/reconciliation/scotia.csv',
                 'REPORT_MD': self.root / 'exports/reconciliation/scotia.md',
                 '_MERCHANT_RULES': copy.deepcopy(scotia.DEFAULT_MERCHANT_RULES)}
        self.patcher = patch.multiple(scotia, **paths)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.rows = [self.bank('purchase', 30), self.bank('refund', 5, direction='credit')]
        self.save()
        scotia.reconcile()

    @staticmethod
    def bank(key, amount, direction='debit', description='Pos Purchase Apos Example Shop'):
        return {'transaction_id': key, 'account_id': 'test_account', 'date': '2026-08-19',
                'description': description, 'amount': str(amount), 'direction': direction, 'currency': 'CAD'}

    def save(self):
        scotia.SCOTIA_PATH.write_text(json.dumps({'account_id': 'test_account', 'transactions': self.rows}), encoding='utf-8')

    def query(self, sql):
        with closing(sqlite3.connect(self.database)) as conn:
            return conn.execute(sql).fetchall()

    def files(self):
        return {path.name: path.read_bytes() for path in (self.root / 'data').glob('*.csv')}

    def state(self):
        with closing(sqlite3.connect(self.database)) as conn:
            return {name: conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
                    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    def link_refund(self):
        order = self.query("SELECT id FROM orders WHERE order_id='scotia-purchase'")[0][0]
        bank = self.query("SELECT id FROM bank_transactions WHERE transaction_id='refund'")[0][0]
        LedgerRepository(self.database, self.root / 'exports/current').link_refund(order, bank)
        return order

    def test_repeat_preserves_order_and_bank_links_without_audit_churn(self):
        ids = self.query('SELECT id,platform,order_id FROM orders')
        links = self.query('SELECT id,bank_transaction_pk,order_pk FROM bank_reconciliations')
        payments = self.query('SELECT id,order_pk FROM order_payments')
        events = self.query('SELECT * FROM order_events')
        for _ in range(3):
            scotia.reconcile()
        self.assertEqual(self.query('SELECT id,platform,order_id FROM orders'), ids)
        self.assertEqual(self.query('SELECT id,bank_transaction_pk,order_pk FROM bank_reconciliations'), links)
        self.assertEqual(self.query('SELECT id,order_pk FROM order_payments'), payments)
        self.assertEqual(self.query('SELECT * FROM order_events'), events)

    def test_real_amount_update_keeps_id_and_records_only_change(self):
        pk = self.query('SELECT id FROM orders')[0][0]
        self.rows[0]['amount'] = '35'
        self.save()
        scotia.reconcile()
        self.assertEqual(self.query('SELECT id,total FROM orders'), [(pk, 35)])
        self.assertEqual(self.query('SELECT event_type FROM order_events'), [('recorded',), ('changed',)])
        self.assertEqual(self.query('SELECT amount FROM order_payments'), [(35,)])

    def test_refund_and_evidence_survive_unchanged_reconciliation(self):
        order = self.link_refund()
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("INSERT INTO payment_evidence(order_pk,source,status,updated_at) VALUES (?, 'test', 'confirmed', 'now')", (order,))
            conn.commit()
        refunds = self.query('SELECT * FROM order_refunds')
        evidence = self.query('SELECT * FROM payment_evidence')
        events = self.query('SELECT * FROM order_events')
        scotia.reconcile()
        self.assertEqual(self.query('SELECT * FROM order_refunds'), refunds)
        self.assertEqual(self.query('SELECT * FROM payment_evidence'), evidence)
        self.assertEqual(self.query('SELECT * FROM order_events'), events)
        self.assertEqual(self.query('SELECT id,total FROM expense_orders'), [(order, 25)])

    def test_refund_conflict_rolls_back_all_database_and_csv_changes(self):
        self.link_refund()
        state, files = self.state(), self.files()
        self.rows[0]['amount'] = '1'
        self.save()
        with self.assertRaises(sqlite3.IntegrityError):
            scotia.reconcile()
        self.assertEqual(self.state(), state)
        self.assertEqual(self.files(), files)

    def test_late_bank_update_failure_rolls_back_earlier_order_update(self):
        self.link_refund()
        state, files = self.state(), self.files()
        self.rows[0]['amount'] = '35'
        self.rows[1]['amount'] = '7'  # Invalidates the confirmed bank-credit refund.
        self.save()
        with self.assertRaises(sqlite3.IntegrityError):
            scotia.reconcile()
        self.assertEqual(self.state(), state)
        self.assertEqual(self.files(), files)

    def test_partial_export_preserves_absent_historical_orders(self):
        pk = self.query('SELECT id FROM orders')[0][0]
        self.rows = [self.bank('new-purchase', 12)]
        self.save()
        scotia.reconcile()
        self.assertIn((pk, 'scotia-purchase', 30), self.query('SELECT id,order_id,total FROM orders'))
        self.assertEqual(len(db.read_csv(scotia.TRANSACTIONS_PATH)), 2)
        self.assertEqual(len(db.read_csv(scotia.BANK_ITEMS_PATH)), 2)
        self.assertEqual(len(db.read_csv(scotia.ASSIGNMENTS_PATH)), 2)
        self.assertEqual(self.query("SELECT COUNT(*) FROM order_events WHERE event_type='removed'"), [(0,)])

    def test_new_correction_removes_only_the_confirmed_reversed_order(self):
        self.rows.append(self.bank('correction', 30, 'credit', 'Correction Apos Example Shop'))
        self.save()
        scotia.reconcile()
        self.assertEqual(self.query('SELECT id FROM orders'), [])
        self.assertEqual(self.query('SELECT event_type FROM order_events'), [('recorded',), ('removed',)])
        self.assertEqual(self.query("SELECT match_status FROM bank_reconciliations WHERE match_status='reversed'"), [('reversed',), ('reversed',)])
        self.assertEqual(db.read_csv(scotia.TRANSACTIONS_PATH), [])
        self.assertEqual(db.read_csv(scotia.BANK_ITEMS_PATH), [])

    def test_reclassification_to_platform_order_relinks_bank_record(self):
        transaction = {'date': '2026-08-19', 'platform': 'amazon_ca', 'order_id': 'real-order',
                       'merchant': 'Amazon', 'total': '30', 'item_total': '30', 'currency': 'CAD'}
        db.import_orders_with_csv([transaction], [], root=self.root, item_filename='amazon_items.csv')
        target = self.query("SELECT id FROM orders WHERE order_id='real-order'")[0][0]
        scotia._MERCHANT_RULES['platforms']['amazon_ca'] = {
            'terms': ['example shop'], 'merchant': 'Amazon', 'requires_order_match': True}
        scotia.reconcile()
        self.assertEqual(self.query('SELECT id,order_id FROM orders'), [(target, 'real-order')])
        self.assertEqual(self.query("SELECT order_pk,match_status FROM bank_reconciliations WHERE match_status='matched_order'"), [(target, 'matched_order')])
        self.assertEqual(db.read_csv(scotia.BANK_ITEMS_PATH), [])

    def test_reclassification_of_refunded_order_requires_review(self):
        self.link_refund()
        state, files = self.state(), self.files()
        scotia._MERCHANT_RULES['platforms']['amazon_ca'] = {
            'terms': ['example shop'], 'merchant': 'Amazon', 'requires_order_match': True}
        with self.assertRaises(sqlite3.IntegrityError):
            scotia.reconcile()
        self.assertEqual(self.state(), state)
        self.assertEqual(self.files(), files)

    def test_csv_failure_keeps_recoverable_committed_order_and_bank_state(self):
        self.rows[0]['amount'] = '35'
        self.save()
        with patch.object(db, 'atomic_write_text', side_effect=OSError('locked CSV')):
            with self.assertRaises(OSError):
                scotia.reconcile()
        self.assertEqual(self.query('SELECT total FROM orders'), [(35,)])
        self.assertEqual(self.query('SELECT COUNT(*) FROM pending_csv_publications'), [(4,)])
        self.assertEqual(db.recover_csv_publications(self.root), 4)
        self.assertEqual(db.read_csv(scotia.TRANSACTIONS_PATH)[0]['total'], '35.00')
        self.assertEqual(self.query('PRAGMA foreign_key_check'), [])


if __name__ == '__main__':
    unittest.main()
