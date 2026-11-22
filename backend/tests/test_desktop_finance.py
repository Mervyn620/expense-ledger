import sqlite3
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi.testclient import TestClient
from pathlib import Path
from contextlib import closing
from app.repository import CashNoteSupersededError, LedgerRepository
from tests.test_expense_app import create_test_ledger

class DesktopFinanceFiltersTest(unittest.TestCase):
    def test_cash_only_months_exclude_transfer_and_credit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = create_test_ledger(root)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("INSERT INTO bank_accounts(account_id,institution,display_name,last4,updated_at) VALUES('fixture','Scotiabank','Fixture','0000','now')")
                conn.executemany("""INSERT INTO bank_transactions(transaction_id,account_id,date,description,amount,direction,currency,source,updated_at)
                  VALUES(?,'fixture',?,?,50,?,'CAD','fixture','now')""", [
                    ('cash-a','2026-09-01','ATM Cash Withdrawal','debit'),
                    ('cash-b','2026-09-02','ATM Cash Withdrawal','debit'),
                    ('cash-c','2026-08-01','ATM Cash Withdrawal','debit'),
                    ('transfer','2026-07-01','Withdrawal Free Interac E-Transfer','debit'),
                    ('credit','2026-10-01','ATM Cash Withdrawal','credit')])
                conn.commit()
            repository = LedgerRepository(db, root/'exports/current')
            filters = repository.filters()
            self.assertEqual(filters['cash_months'], ['2026-09','2026-08'])
            self.assertNotIn('2026-09', filters['months'])
            self.assertEqual(repository.cash_withdrawals('2026-09')['summary']['withdrawal_count'], 2)

    def test_no_withdrawals_preserves_existing_filter_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            filters = LedgerRepository(create_test_ledger(root), root/'exports/current').filters()
            self.assertEqual(filters['cash_months'], [])
            self.assertEqual(filters['months'], ['2026-06','2026-05'])
            self.assertEqual(len(filters['cards']), 1)


class CashNoteWriteOrderingTest(unittest.TestCase):
    def test_superseded_sqlite_waiter_cannot_overwrite_newer_note_or_clear(self):
        # Hold a real SQLite writer lock, then force the newer request to finish
        # before the older waiter resumes. No sleeps or production ledger access.
        for newest_note in ("newest note", ""):
            with self.subTest(newest_note=newest_note), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                db = create_test_ledger(root)
                with closing(sqlite3.connect(db)) as conn:
                    conn.execute("INSERT INTO bank_accounts(account_id,institution,display_name,last4,updated_at) VALUES('fixture','Scotiabank','Fixture','0000','now')")
                    cursor = conn.execute("INSERT INTO bank_transactions(transaction_id,account_id,date,description,amount,direction,currency,source,updated_at) VALUES('cash','fixture','2026-09-01','ATM Withdrawal',50,'debit','CAD','fixture','now')")
                    cash_id = cursor.lastrowid
                    conn.commit()
                repository = LedgerRepository(db, root/'exports/current')
                repository.update_cash_withdrawal_note(cash_id, 'original note')
                old_blocked, new_entered, release_old = (threading.Event() for _ in range(3))
                role = threading.local()
                real_connect = sqlite3.connect

                class ControlledConnection(sqlite3.Connection):
                    def execute(connection, sql, *args, **kwargs):
                        if sql == 'BEGIN IMMEDIATE':
                            if role.value == 'old':
                                super(ControlledConnection, connection).execute('PRAGMA busy_timeout = 1')
                                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                                    super(ControlledConnection, connection).execute(sql)
                                old_blocked.set()
                                if not release_old.wait(5):
                                    raise TimeoutError('older waiter was not released')
                                super(ControlledConnection, connection).execute('PRAGMA busy_timeout = 10000')
                            else:
                                new_entered.set()
                        return super(ControlledConnection, connection).execute(sql, *args, **kwargs)

                def connect(*args, **kwargs):
                    return real_connect(*args, **kwargs, factory=ControlledConnection)

                def update(name, note):
                    role.value = name
                    return repository.update_cash_withdrawal_note(cash_id, note)

                with closing(real_connect(db)) as blocker:
                    blocker.execute('BEGIN IMMEDIATE')
                    with patch('app.repository.sqlite3.connect', side_effect=connect), patch('app.repository.create_schema'), ThreadPoolExecutor(max_workers=2) as pool:
                        older = pool.submit(update, 'old', 'obsolete note')
                        try:
                            self.assertTrue(old_blocked.wait(3), 'old request must encounter SQLite contention')
                            newer = pool.submit(update, 'new', newest_note)
                            self.assertTrue(new_entered.wait(3), 'new request must register while old request is pending')
                            blocker.rollback()
                            self.assertEqual(newer.result(timeout=3)['note'], newest_note)
                        finally:
                            blocker.rollback()
                            release_old.set()
                        with self.assertRaises(CashNoteSupersededError):
                            older.result(timeout=3)
                final = repository.cash_withdrawals('2026-09')['rows'][0]
                self.assertEqual(final['note'], newest_note)
                self.assertEqual(repository.overview('2026-06')['summary']['total'], 38.81)

    def test_superseded_cash_note_api_returns_sanitized_conflict(self):
        from app import main
        with patch.object(main.repository, 'update_cash_withdrawal_note', side_effect=CashNoteSupersededError('private database detail')):
            client = TestClient(main.app, base_url='http://127.0.0.1')
            response = client.patch('/api/cash-withdrawals/1', json={'note':'fixture'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'detail':'A newer cash-note edit superseded this request.'})
        self.assertNotIn('private', response.text)
