from unittest.mock import patch
from PyQt6.QtWidgets import QMessageBox
from app.db.dao_conn import get_conn
from app.db.dao_global_settings import add_role, assign_role, get_role, get_role_tabs, set_role_tabs, update_role
from app.db.dao_kontrahenci import save_kontrahent
from app.db.dao_invoices import (can_access_invoice, create_manual_invoice, create_correction, mark_exported,
    list_invoices, get_exported_invoices, get_invoice_by_id, update_invoice_draft, InvoiceLockedError, trash_invoice)
from app.security.authorization import allowed_tabs, can, can_manage_invoices, get_permissions
from app.utils.request_cache import request_scope
from tests.base import DbTestCase

POSITIONS = [{'nazwa': 'Usługa testowa', 'ilosc': 1, 'cena_netto': 100, 'vat': 23}]


class InvoiceModulePermissionsTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.employee = self.make_employee('invoices@example.test')
        self.role = add_role('Obsługa faktur', can_view_invoices=False)
        assign_role(self.employee, self.role)
        set_role_tabs(self.role, ['Faktury'])
        self.contractor = save_kontrahent({'typ': 'osoba_fizyczna', 'nazwa': 'Jan Testowy'})

    def draft(self, actor=None):
        return create_manual_invoice('kontrahent', self.contractor, POSITIONS,
            '2026-11-01', '2026-11-01', '2026-11-15', actor_id=self.employee if actor is None else actor)

    def test_one_invoice_module_permission_covers_manual_invoices_and_corrections(self):
        self.assertIn('Faktury', allowed_tabs(self.employee))
        self.assertTrue(get_permissions(self.employee)['can_view_invoices'])
        self.assertFalse(can(self.employee, 'can_view_finances'))
        self.assertFalse(can(self.employee, 'can_export_data'))
        original = self.draft()
        self.assertTrue(can_access_invoice(self.employee, None))
        self.assertIn(original, {row['id'] for row in list_invoices(self.employee, False)})
        mark_exported([original], self.employee)
        self.assertIn(original, {row['id'] for row in get_exported_invoices(self.employee)})
        correction = create_correction(original, 'Częściowa korekta', actor_id=self.employee)
        update_invoice_draft(correction, POSITIONS, actor_id=self.employee)
        mark_exported([correction], self.employee)
        self.assertEqual(get_invoice_by_id(correction)['korygowana_faktura_id'], original)
        self.assertTrue(get_invoice_by_id(correction)['eksportowana'])
        from app.invoices.ksef_submission import _authorized_invoice
        self.assertEqual(_authorized_invoice(original, self.employee)['id'], original)
        with self.assertRaises(PermissionError):
            trash_invoice(original, self.employee)

    def test_explicit_tab_denial_wins_over_stale_legacy_flags(self):
        invoice = self.draft()
        # Simulate an old, inconsistent role saved by a previous desktop version.
        with get_conn() as conn:
            conn.execute("UPDATE role SET zakladki='Pulpit',can_view_invoices=1,can_view_finances=1,can_export_data=1 WHERE id=%s", (self.role,))
        self.assertFalse(can_manage_invoices(self.employee))
        self.assertFalse(can(self.employee, 'can_view_invoices'))
        self.assertEqual(list_invoices(self.employee, None), [])
        self.assertEqual(get_exported_invoices(self.employee), [])
        with self.assertRaises(PermissionError):
            mark_exported([invoice], self.employee)
        with self.assertRaises(PermissionError):
            self.draft()
        with self.assertRaises(PermissionError):
            update_invoice_draft(invoice, POSITIONS, actor_id=self.employee)
        from app.invoices.ksef_submission import _authorized_invoice
        with self.assertRaises(PermissionError):
            _authorized_invoice(invoice, self.employee)
        self.assertFalse(get_invoice_by_id(invoice)['eksportowana'])

    def test_role_dialog_and_matrix_update_the_same_invoice_permission(self):
        update_role(self.role, 'Obsługa faktur', can_view_invoices=False)
        self.assertNotIn('Faktury', get_role_tabs(self.role))
        self.assertFalse(can_manage_invoices(self.employee))
        update_role(self.role, 'Obsługa faktur', can_view_invoices=True)
        self.assertIn('Faktury', get_role_tabs(self.role))
        self.assertTrue(can_manage_invoices(self.employee))
        set_role_tabs(self.role, ['Pulpit'])
        self.assertFalse(get_role(self.role)['can_view_invoices'])
        set_role_tabs(self.role, ['Faktury'])
        self.assertTrue(get_role(self.role)['can_view_invoices'])
        self.assertFalse(get_role(self.role)['can_view_finances'])
        self.assertFalse(get_role(self.role)['can_export_data'])

    def test_revocation_is_applied_in_the_same_request_scope(self):
        with request_scope():
            self.assertTrue(can_manage_invoices(self.employee))
            set_role_tabs(self.role, ['Pulpit'])
            self.assertFalse(can_manage_invoices(self.employee))
        self.assertFalse(can_manage_invoices(None))
        self.assertFalse(can_manage_invoices(999999))
        with get_conn() as conn:
            conn.execute('UPDATE pracownicy SET aktywny=0 WHERE id=%s', (self.employee,))
        self.assertFalse(can_manage_invoices(self.employee))

    def test_business_locks_and_finance_addon_remain_independent(self):
        invoice = self.draft()
        with self.assertRaises(ValueError):
            create_correction(invoice, actor_id=self.employee)  # A draft is edited, not corrected.
        mark_exported([invoice], self.employee)
        with self.assertRaises(InvoiceLockedError):
            update_invoice_draft(invoice, POSITIONS, actor_id=self.employee)
        from app.db.dao_payments import _allowed
        self.assertFalse(_allowed(self.employee))
        update_role(self.role, 'Obsługa faktur', can_view_finances=True)
        self.assertTrue(_allowed(self.employee))
        self.assertFalse(can(self.employee, 'can_export_data'))

    def test_invoice_page_allows_issuance_but_hides_finance_without_addon(self):
        from app.invoices.faktury_ui import FakturyPage
        with patch.object(FakturyPage, 'refresh_async'):
            page = FakturyPage(self.employee)
        try:
            self.assertTrue(page._can_issue())
            self.assertFalse(page.tabs.isTabVisible(page.tabs.indexOf(page.tab_rozliczenia)))
            self.assertFalse(page.btn_bank.isEnabled())
            with patch.object(QMessageBox, 'warning'):
                self.assertFalse(page._can_export())
        finally:
            page.close()
