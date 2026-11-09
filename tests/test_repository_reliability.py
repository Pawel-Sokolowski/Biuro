"""Regressions found by the repository-wide review, with no external servers."""
from contextlib import nullcontext
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase, mock

from hypothesis import given, strategies as st

from app.email import email_sync
from app.reports.import_parse import compute_employee_report
from app.reports.reports_ui import ReportsPage
from app.utils.money import allocate_money, money
from tests.base import DbTestCase


class MailMoveSafetyTests(TestCase):
    def connection(self, capabilities, fail=None):
        conn = mock.Mock()
        conn.capabilities = capabilities
        conn.uid.side_effect = lambda command, *args: ('NO' if command == fail else 'OK', [b''])
        session = SimpleNamespace(conn=conn, select=lambda *_: True)
        self.enterContext(mock.patch.object(email_sync, '_shared_session', return_value=nullcontext(session)))
        self.enterContext(mock.patch.object(email_sync, '_assert_selected_rows'))
        return conn

    def test_byte_move_capability_uses_atomic_move(self):
        conn = self.connection((b'IMAP4rev1', b'MOVE', b'UIDPLUS'))
        self.assertTrue(email_sync.move_message(1, '1', 'INBOX', 'Archive'))
        self.assertEqual([c.args[0] for c in conn.uid.call_args_list], ['MOVE'])
        conn.expunge.assert_not_called()

    def test_fallback_expunges_only_selected_uid(self):
        conn = self.connection((b'UIDPLUS',))
        self.assertTrue(email_sync.move_message(1, '1', 'INBOX', 'Archive'))
        self.assertEqual([c.args[0] for c in conn.uid.call_args_list], ['COPY', 'STORE', 'EXPUNGE'])
        self.assertEqual(conn.uid.call_args.args, ('EXPUNGE', '1'))
        conn.expunge.assert_not_called()

    def test_without_uidplus_leaves_deleted_flags_without_folder_expunge(self):
        conn = self.connection(())
        self.assertTrue(email_sync.delete_message(1, '1', 'INBOX'))
        self.assertEqual([c.args[0] for c in conn.uid.call_args_list], ['STORE'])
        conn.expunge.assert_not_called()

    def test_failed_move_is_not_retried_as_copy(self):
        conn = self.connection((b'MOVE',), fail='MOVE')
        self.assertFalse(email_sync.move_message(1, '1', 'INBOX', 'Archive'))
        self.assertEqual(conn.uid.call_count, 1)

    def test_failed_store_after_copy_reports_partial_operation(self):
        conn = self.connection((b'UIDPLUS',), fail='STORE')
        with self.assertRaisesRegex(RuntimeError, 'skopiowana'):
            email_sync.move_message(1, '1', 'INBOX', 'Archive')
        self.assertEqual([c.args[0] for c in conn.uid.call_args_list], ['COPY', 'STORE'])
        conn.expunge.assert_not_called()


class ExactReportTests(TestCase):
    @given(st.integers(-10000000, 10000000), st.lists(st.integers(0, 10000), min_size=1, max_size=15))
    def test_allocation_preserves_amount_and_is_stable(self, cents, weights):
        result = allocate_money(Decimal(cents) / 100, dict(enumerate(weights)))
        self.assertEqual(sum(result.values()), Decimal(cents) / 100)
        self.assertEqual(result, allocate_money(Decimal(cents) / 100, dict(enumerate(weights))))
        self.assertTrue(all(value == money(value) for value in result.values()))

    def test_duplicate_company_labels_remain_separate(self):
        rows = [dict(firma_id=i, klient='Kowalski', miesiac='2026-10', sekcja='ksiegowosc', cena=100*i, koszt=0) for i in (1, 2)]
        self.assertEqual(len(ReportsPage._aggregate_profit(rows)), 2)

    def test_three_employees_preserve_invoice_total(self):
        work = [dict(firma_id=1, firma_nazwa='Example', miesiac='2026-10', dzial='ksiegowosc', pracownik_nazwa=name,
                     pracownik_id=i, sekundy=3600) for i, name in enumerate(('A', 'B', 'C'), 1)]
        invoices = [dict(firma_id=1, kontrahent='Example', miesiac='2026-10', rodzaj='ksiegowosc', wartosc_netto=100.01)]
        rows = compute_employee_report(work, invoices, {})
        self.assertEqual(sum(Decimal(str(r['cena'])) for r in rows), Decimal('100.01'))

    def test_duplicate_employee_names_keep_ids_and_rates(self):
        work = [dict(firma_id=1, firma_nazwa='Example', miesiac='2026-10', dzial='ksiegowosc', pracownik_nazwa='Jan Kowalski',
                     pracownik_id=i, sekundy=3600) for i in (1, 2)]
        rows = compute_employee_report(work, [], {1: 50, 2: 80})
        self.assertEqual({r['pracownik_id']: r['koszt'] for r in rows}, {1: 50, 2: 80})


class DraftRevisionTests(DbTestCase):
    def test_stale_save_is_rejected_without_overwriting_first_employee(self):
        from app.db.dao_invoices import create_or_update_invoice, get_invoice_by_id, update_invoice_draft, InvoiceConflictError
        company = self.make_company()
        positions = [dict(nazwa='Service', ilosc=1, cena_netto=100, vat=23)]
        invoice = create_or_update_invoice(company, '2026-10', positions, 100, 123)
        version = get_invoice_by_id(invoice)['revision']
        update_invoice_draft(invoice, [dict(positions[0], cena_netto=200)], expected_revision=version)
        with self.assertRaises(InvoiceConflictError):
            update_invoice_draft(invoice, positions, expected_revision=version)
        self.assertEqual(get_invoice_by_id(invoice)['kwota_netto'], 200)
        self.assertEqual(get_invoice_by_id(invoice)['revision'], version + 1)
        # Background regeneration must not merge positions from a stale copy.
        with self.assertRaises(InvoiceConflictError):
            create_or_update_invoice(company, '2026-10', positions, 100, 123,
                                     existing_invoice_id=invoice, expected_revision=version)
        self.assertEqual(get_invoice_by_id(invoice)['kwota_netto'], 200)



class DurableTimerTests(DbTestCase):
    def test_recovery_excludes_closed_gap_and_rejects_old_owner(self):
        from datetime import datetime
        from app.db import dao_active_timers as timers
        from app.db.dao_conn import get_conn
        employee = self.make_employee()
        company = self.make_company()
        with get_conn() as conn:
            original = timers.start(conn, employee, company, '2026-10', 'glowny', {}, datetime.now())
        timers.checkpoint([(original.session_id, original.owner, 123)])
        self.assertEqual(timers.recoverable(employee), [])
        with get_conn() as conn:
            conn.execute("UPDATE active_timer_sessions SET checkpoint_at=now()-interval '2 minutes'")
        other = self.make_employee('other@example.com')
        with self.assertRaises(ValueError):
            timers.claim(other, original.session_id)
        row, resumed = timers.claim(employee, original.session_id)
        self.assertEqual(row['elapsed_seconds'], 123)
        self.assertGreaterEqual(resumed.elapsed(), 123)
        self.assertLess(resumed.elapsed(), 126)
        with self.assertRaises(ValueError), get_conn() as conn:
            timers.finish(conn, original)
        with self.assertRaises(ValueError):
            timers.claim(employee, original.session_id)
        # A delayed heartbeat from the old process must not change ownership.
        timers.checkpoint([(original.session_id, original.owner, 999)])
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT elapsed_seconds FROM active_timer_sessions').fetchone()[0], 123)
            timers.finish(conn, resumed)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM active_timer_sessions').fetchone()[0], 0)

    def test_elapsed_uses_monotonic_clock(self):
        from datetime import datetime
        from app.db import dao_active_timers as timers
        with mock.patch.object(timers.time, 'monotonic', return_value=100):
            session = timers.ActiveTimer('a', 'b', datetime(2026, 1, 1), 7, monotonic_start=100)
        with mock.patch.object(timers.time, 'monotonic', return_value=125):
            self.assertEqual(session.elapsed(), 32)
        with mock.patch.object(timers.time, 'monotonic', return_value=99):
            self.assertEqual(session.elapsed(), 7)


class SharedMailboxLeaseTests(DbTestCase):
    def test_other_context_cannot_sync_same_account_and_cooldown_is_shared(self):
        from contextvars import Context
        from app.db.dao_conn import get_conn
        from app.config import get_schema, schema_context
        from app.utils.sync_lock import account_sync_lock
        with get_conn() as conn:
            conn.execute("INSERT INTO konta_email(id,email) VALUES(1,'test@example.com')")
        schema = get_schema()
        def contender():
            with schema_context(schema), account_sync_lock(1) as acquired:
                return acquired
        with account_sync_lock(1, cooldown=90) as acquired:
            self.assertTrue(acquired)
            self.assertFalse(Context().run(contender))
            with account_sync_lock(1) as nested:
                self.assertTrue(nested)
        with account_sync_lock(1, cooldown=90) as acquired:
            self.assertFalse(acquired)
        self.assertTrue(Context().run(contender))

    def test_database_failure_releases_lock_and_does_not_publish_success(self):
        from contextvars import Context
        from app.config import get_schema, schema_context
        from app.db.dao_conn import get_conn
        from app.utils.sync_lock import account_sync_lock
        schema = get_schema()
        with get_conn() as conn:
            conn.execute("INSERT INTO konta_email(id,email) VALUES(1,'test@example.com')")
        with self.assertRaises(Exception):
            with account_sync_lock(1, cooldown=90):
                # Simulate failure in another database request during sync.
                with get_conn() as conn:
                    conn.execute('SELECT missing_column FROM account_sync_state')
        def retry():
            with schema_context(schema), account_sync_lock(1, cooldown=90) as acquired:
                return acquired
        self.assertTrue(Context().run(retry))
        # The failure can also occur in the lease connection itself, while
        # publishing cooldown for an account deleted during synchronization.
        with self.assertRaises(Exception):
            with account_sync_lock(999, cooldown=90) as acquired:
                self.assertTrue(acquired)
        def retry_deleted():
            with schema_context(schema), account_sync_lock(999) as acquired:
                return acquired
        self.assertTrue(Context().run(retry_deleted))


class LibraryAndCacheTests(TestCase):
    def test_settings_cache_is_request_local_and_invalidated_by_write(self):
        from app.utils.request_cache import request_scope
        from app.db import dao_app_settings as settings
        conn = mock.Mock()
        conn.cursor.return_value.fetchone.return_value = ['old']
        with mock.patch.object(settings, 'get_conn', return_value=conn):
            with request_scope():
                self.assertEqual(settings.get_setting('example'), 'old')
                self.assertEqual(settings.get_setting('example'), 'old')
                self.assertEqual(conn.cursor.return_value.execute.call_count, 1)
                settings.set_setting('example', 'new')
                conn.cursor.return_value.fetchone.return_value = ['new']
                self.assertEqual(settings.get_setting('example'), 'new')
            conn.cursor.return_value.fetchone.return_value = ['other computer']
            with request_scope():
                self.assertEqual(settings.get_setting('example'), 'other computer')

    def test_google_token_refresh_uses_library_and_cache_isolated_by_schema(self):
        from datetime import datetime, timedelta
        from app.kalendarz import google_sync as google
        from app.config import schema_context
        credentials = mock.Mock(token='access', expiry=datetime.utcnow() + timedelta(hours=1))
        google._token_cache.clear()
        self.addCleanup(google._token_cache.clear)
        with mock.patch.object(google, 'get_setting', return_value='encrypted'), \
             mock.patch.object(google, 'get_client_credentials', return_value=('client','secret')), \
             mock.patch.object(google, 'decrypt_password', return_value='refresh'), \
             mock.patch('google.oauth2.credentials.Credentials', return_value=credentials) as factory:
            with schema_context('first'):
                self.assertEqual(google._access_token(), 'access')
                self.assertEqual(google._access_token(), 'access')
            with schema_context('second'):
                self.assertEqual(google._access_token(), 'access')
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(credentials.refresh.call_count, 2)

    def test_log_exception_and_message_redact_credentials(self):
        import logging
        import os
        from app.diagnostics import RedactingFormatter
        with mock.patch.dict(os.environ, {'DB_URL':'postgresql://name:p%40ss@example/db', 'BIO_ENCRYPTION_KEY':'secret-key'}):
            formatter = RedactingFormatter()
        try:
            raise ValueError('p@ss secret-key')
        except ValueError:
            import sys
            record = logging.LogRecord('test', logging.ERROR, '', 1, 'failed %s', ('p%40ss',), sys.exc_info())
        rendered = formatter.format(record)
        self.assertNotIn('p@ss', rendered)
        self.assertNotIn('p%40ss', rendered)
        self.assertNotIn('secret-key', rendered)
        self.assertIn('ValueError', rendered)

    def test_identifiers_warn_without_rejecting_legacy_rows(self):
        from app.utils.identifiers import identifier_warnings
        self.assertEqual(identifier_warnings({'nip':'8522605060'}), [])
        self.assertEqual(len(identifier_warnings({'nip':'1234567890'})), 1)


class ExactStorageTests(DbTestCase):
    def test_invoice_headers_are_stored_as_exact_numeric(self):
        from app.db.dao_conn import get_conn
        from app.db.dao_invoices import create_or_update_invoice
        company = self.make_company()
        invoice = create_or_update_invoice(company, '2026-10',
            [dict(nazwa='Service', ilosc=1, cena_netto=98.4, vat=0)], 98.4, 98.4)
        with get_conn() as conn:
            row = conn.execute('SELECT kwota_brutto, pg_typeof(kwota_brutto)::text AS storage FROM faktury WHERE id=%s', (invoice,)).fetchone()
        self.assertEqual(row['kwota_brutto'], Decimal('98.40'))
        self.assertEqual(row['storage'], 'numeric')


class ImportBoundaryTests(TestCase):
    def test_duplicate_employee_names_and_initials_require_explicit_email(self):
        from app.utils.parsing import resolve_employee_ids
        from app.utils.request_cache import request_scope
        cursor = mock.Mock()
        cursor.fetchall.return_value = [(1, 'one@example.com', 'Jan', 'Kowalski'),
                                       (2, 'two@example.com', 'Jan', 'Kowalski')]
        with request_scope():
            self.assertEqual(resolve_employee_ids(cursor, 'Jan Kowalski, JK'), ([], ['Jan Kowalski', 'JK']))
            self.assertEqual(resolve_employee_ids(cursor, 'two@example.com'), ([2], []))
        self.assertEqual(cursor.execute.call_count, 1)

    def test_numeric_excel_bank_account_is_not_silently_accepted(self):
        from app.utils.importers import _build_payload_from_row
        headers = ['Nazwa Firmy', 'Rachunek bankowy']
        with self.assertRaisesRegex(ValueError, 'utracone cyfry'):
            _build_payload_from_row(headers, dict(zip(headers, range(2))), ['Example', 46105015591000009081362718])

    def test_csv_rows_are_streamed_and_closed_on_early_exit(self):
        import tempfile
        from pathlib import Path
        from app.utils.importers import _load_rows_from_file
        from app.utils.request_cache import request_scope
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.csv'
            path.write_text('Nazwa Firmy;NIP\nExample;8522605060\nOther;123\n', encoding='utf-8')
            with request_scope():
                headers, rows = _load_rows_from_file(str(path))
                self.assertEqual(headers, ['Nazwa Firmy', 'NIP'])
                self.assertIs(iter(rows), rows)
                self.assertEqual(next(rows), ['Example', '8522605060'])
            self.assertEqual(list(rows), [])


class ImportPreviewTests(DbTestCase):
    def test_plan_counts_changes_and_conflicts_without_writing(self):
        import tempfile
        from pathlib import Path
        from app.utils.importers import preview_company_import
        from app.db.dao_conn import get_conn
        self.make_company('Existing', nip='8522605060')
        with get_conn() as conn:
            before = conn.execute('SELECT count(*) FROM firmy').fetchone()[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'companies.csv'
            path.write_text('Nazwa Firmy;NIP;Rachunki walutowe\nExisting;8522605060;EUR\nNew;;\nNew;;\n', encoding='utf-8')
            plan = preview_company_import(str(path))
        self.assertEqual((plan['created'], plan['updated'], plan['conflicts']), (1, 1, 1))
        self.assertTrue(any('Rachunki walutowe' in warning for warning in plan['warnings']))
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM firmy').fetchone()[0], before)


class ImportWorkerTests(TestCase):
    def test_import_keeps_gui_responsive_and_runs_work_in_another_thread(self):
        import threading
        from PyQt6.QtCore import QTimer
        from app.utils.import_progress import run_import
        from tests.base import QT_APP
        self.assertIsNotNone(QT_APP)
        ready = threading.Event()
        main_thread = threading.get_ident()
        QTimer.singleShot(0, ready.set)
        def work(progress):
            progress('Próba')
            self.assertTrue(ready.wait(3), 'GUI did not process the timer during import')
            return threading.get_ident()
        self.assertNotEqual(run_import(work), main_thread)


class VirtualTableTests(TestCase):
    def test_same_record_updates_emit_data_changed_without_reset(self):
        from PyQt6.QtTest import QSignalSpy
        from PyQt6.QtWidgets import QTableWidgetItem
        from app.utils.item_table import ItemTable
        table = ItemTable(['ID', 'Value'])
        table.begin_batch()
        table.setRowCount(1)
        table.setItem(0, 0, QTableWidgetItem('7'))
        table.setItem(0, 1, QTableWidgetItem('before'))
        table.end_batch()
        reset = QSignalSpy(table.model().modelReset)
        changed = QSignalSpy(table.model().dataChanged)
        table.begin_batch()
        table.setItem(0, 1, QTableWidgetItem('after'))
        table.end_batch()
        self.assertEqual(len(reset), 0)
        self.assertEqual(len(changed), 1)
        self.assertEqual(table.item(0, 1).text(), 'after')
        self.assertIsNone(table.cellWidget(0, 1))
        table.close()
