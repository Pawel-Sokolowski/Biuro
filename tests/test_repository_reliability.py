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
