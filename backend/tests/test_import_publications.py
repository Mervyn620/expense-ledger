import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from app.repository import LedgerRepository
import test_fantuan_import as fixtures
from scripts.reporting import validate

# Importers load the ledger module through their existing CLI import path.
import db
fantuan_import = fixtures.fantuan_import


class ImportPublicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixtures.FantuanImportTest.prepare_root(self.root)
        self.source = self.root / 'data/fantuan_orders.json'
        self.database = self.root / 'data/expenses.sqlite'
        self.order = fixtures.FantuanImportTest.order('example', '已完成', 'Example Shop', 30)
        self.save()
        fantuan_import.import_file(self.root)

    def save(self):
        self.source.write_text(json.dumps({'orders': [self.order]}), encoding='utf-8')

    def state(self):
        with closing(sqlite3.connect(self.database)) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {table: conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                    for table in tables}

    def files(self):
        return {name: (self.root / 'data' / name).read_bytes()
                for name in ('transactions.csv', 'delivery_items.csv')}

    def pending(self):
        with closing(sqlite3.connect(self.database)) as conn:
            return conn.execute('SELECT COUNT(*) FROM pending_csv_publications').fetchone()[0]

    def update(self):
        self.order.update(total='40.00', item_total='40.00')
        self.order['goods'][0].update(name='Changed item', price='40.00')
        self.save()

    def link_refund(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("INSERT INTO bank_accounts VALUES ('test','Example','Example','0000','now')")
            bank = conn.execute('''INSERT INTO bank_transactions
                (transaction_id,account_id,date,description,amount,direction,currency,updated_at)
                VALUES ('credit', 'test', '2026-08-20', 'Example credit', 5, 'credit', 'CAD', 'now')''').lastrowid
            order = conn.execute('SELECT id FROM orders').fetchone()[0]
            conn.commit()
        LedgerRepository(self.database, self.root / 'exports/current').link_refund(order, bank)

    def test_refund_cancellation_preserves_database_and_both_csvs(self):
        self.link_refund()
        before, files = self.state(), self.files()
        self.order['status'] = '已取消'
        self.save()
        with self.assertRaises(sqlite3.IntegrityError):
            fantuan_import.import_file(self.root)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.files(), files)

    def test_refund_amount_conflict_preserves_old_items_and_payments(self):
        self.link_refund()
        before, files = self.state(), self.files()
        self.order.update(total='1.00', item_total='1.00')
        self.order['goods'] = []
        self.save()
        with self.assertRaises(sqlite3.IntegrityError):
            fantuan_import.import_file(self.root)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.files(), files)

    def test_precommit_failure_rolls_back_audit_events_and_csvs(self):
        before, files = self.state(), self.files()
        self.update()
        with patch.object(db, 'ensure_payments_for_orders', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                fantuan_import.import_file(self.root)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.files(), files)

    def test_sqlite_commit_failure_does_not_publish_files(self):
        before, files = self.state(), self.files()
        self.update()
        original = sqlite3.connect

        class FailedCommit(sqlite3.Connection):
            def __exit__(self, kind, value, traceback):
                if kind is None and self.execute('SELECT COUNT(*) FROM pending_csv_publications').fetchone()[0]:
                    self.rollback()
                    raise sqlite3.OperationalError('simulated commit failure')
                return super().__exit__(kind, value, traceback)

        with patch.object(db.sqlite3, 'connect', side_effect=lambda *a, **kw: original(*a, **kw, factory=FailedCommit)):
            with self.assertRaises(sqlite3.OperationalError):
                fantuan_import.import_file(self.root)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.files(), files)

    def fail_publication(self, filename='transactions.csv'):
        original = db.atomic_write_text

        def fail(path, text, **kwargs):
            if Path(path).name == filename:
                raise OSError('simulated locked destination')
            return original(path, text, **kwargs)

        return patch.object(db, 'atomic_write_text', side_effect=fail)

    def test_second_csv_failure_is_recoverable_without_reimport(self):
        old = self.files()
        self.update()
        with self.fail_publication(), self.assertRaisesRegex(RuntimeError, 'committed.*pending'):
            fantuan_import.import_file(self.root)
        self.assertEqual(self.pending(), 2)
        self.assertEqual(self.files()['transactions.csv'], old['transactions.csv'])
        self.assertNotEqual(self.files()['delivery_items.csv'], old['delivery_items.csv'])
        before = self.state()
        self.assertEqual(db.recover_csv_publications(self.root), 2)
        after = self.state()
        for table in before:
            if table != 'pending_csv_publications':
                self.assertEqual(before[table], after[table], table)
        rows = db.read_csv(self.root / 'data/transactions.csv')
        self.assertEqual(rows[0]['total'], '40.00')
        self.assertEqual(self.pending(), 0)
        self.assertEqual(db.recover_csv_publications(self.root), 0)

    def test_sync_repairs_pending_files_before_loading_them(self):
        self.update()
        with self.fail_publication(), self.assertRaises(RuntimeError):
            fantuan_import.import_file(self.root)
        result = db.sync_all(root=self.root, db_path=self.database)
        self.assertEqual(result['total'], 40)
        self.assertEqual(self.pending(), 0)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT product_name FROM order_items').fetchone()[0], 'Changed item')

    def test_failed_recovery_blocks_stale_csv_consumption_and_sync(self):
        self.update()
        with self.fail_publication():
            with self.assertRaises(RuntimeError):
                fantuan_import.import_file(self.root)
            before = self.state()
            with self.assertRaises(OSError):
                db.read_csv(self.root / 'data/transactions.csv')
            with self.assertRaises(OSError):
                db.sync_all(root=self.root, db_path=self.database)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.pending(), 2)

    def test_process_exit_between_files_recovers_from_durable_outbox(self):
        self.update()
        code = '''
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts/ledger'))
from scripts.importers import fantuan
import db
original = db.atomic_write_text
def interrupted(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(85)
db.atomic_write_text = interrupted
fantuan.import_file(Path(sys.argv[1]))
'''
        completed = subprocess.run([sys.executable, '-B', '-c', code, str(self.root)],
                                   cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=20)
        self.assertEqual(completed.returncode, 85, completed.stderr)
        self.assertEqual(self.pending(), 2)
        rows = db.read_csv(self.root / 'data/transactions.csv')
        self.assertEqual(rows[0]['total'], '40.00')
        self.assertEqual(self.pending(), 0)

    def test_reimport_with_empty_items_removes_stale_item_rows(self):
        self.order['goods'] = []
        self.save()
        fantuan_import.import_file(self.root)
        self.assertEqual(db.read_csv(self.root / 'data/delivery_items.csv'), [])
        self.assertEqual(self.state()['order_items'], [])

    def test_two_imports_preserve_each_others_csv_and_ledger_changes(self):
        def run(index):
            order = fixtures.FantuanImportTest.order(f'parallel-{index}', '已完成', 'Example', 10)
            transactions, items, _, _ = fantuan_import.normalize_payload({'orders': [order]})
            db.import_orders_with_csv(transactions, items, root=self.root, item_filename='delivery_items.csv')
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(run, (1, 2)))
        self.assertEqual(len(db.read_csv(self.root / 'data/transactions.csv')), 3)
        self.assertEqual(len(self.state()['orders']), 3)
        self.assertEqual(self.pending(), 0)

    def test_validation_reports_pending_without_repairing_or_mutating_database(self):
        self.update()
        with self.fail_publication(), self.assertRaises(RuntimeError):
            fantuan_import.import_file(self.root)
        before = self.database.read_bytes()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = validate.main(self.root)
        status = json.loads((self.root / 'exports/current/validation-status.json').read_text(encoding='utf-8'))
        self.assertEqual(result, 1)
        self.assertEqual(status['metrics']['pending_csv_publications'], 2)
        self.assertEqual(self.database.read_bytes(), before)

    def test_schema_five_upgrade_preserves_every_existing_row(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute('DROP TABLE pending_csv_publications')
            conn.execute('PRAGMA user_version=5')
            conn.commit()
        before = self.state()
        with closing(db.connect(self.database)) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 9)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        after = self.state()
        for table in before:
            self.assertEqual(before[table], after[table], table)
        self.assertEqual(after['pending_csv_publications'], [])

    def test_recovery_rejects_paths_outside_managed_csv_set(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute('INSERT INTO pending_csv_publications VALUES (?,?)', ('../outside.txt', 'invalid'))
            conn.commit()
        with self.assertRaises(ValueError):
            db.recover_csv_publications(self.root)
        self.assertFalse((self.root / 'outside.txt').exists())
        self.assertEqual(self.pending(), 1)


if __name__ == '__main__':
    unittest.main()
