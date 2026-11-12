import json
from unittest import mock

from PyQt6.QtWidgets import QApplication

from app.db.dao_conn import get_conn
from app.db.dao_global_settings import add_role, assign_role
from app.security.authorization import can
from app.db.dao_invoices import (
    create_correction, create_or_update_invoice, delete_draft_invoice,
    get_invoice_by_id, mark_exported, set_invoice_number,
)
from app.invoices.saldeo_correction import confirm_saldeo_correction
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(['saldeo-correction-tests'])


class SaldeoCorrectionWorkflowTests(DbTestCase):
    def setUp(self):
        super().setUp()
        company = self.make_company()
        self.original = create_or_update_invoice(company, '2026-09', [
            {'nazwa': 'Usługa', 'ilosc': 1, 'netto': 100, 'brutto': 123, 'vat': 23}
        ], 100, 123)
        with get_conn() as conn:
            conn.execute("UPDATE faktury SET numer = 'FV/1/09/2026' WHERE id = %s", (self.original,))
        mark_exported([self.original], 1)
        self.correction = create_correction(self.original, 'Zmiana ceny')

    def test_confirm_external_issuance_preserves_negative_document(self):
        before = get_invoice_by_id(self.correction)
        confirm_saldeo_correction(self.correction, 'KOR/1/2026', 1)
        after = get_invoice_by_id(self.correction)
        self.assertEqual(after['numer'], 'KOR/1/2026')
        self.assertEqual(after['eksportowana'], 1)
        self.assertEqual(after['kwota_brutto'], -123)
        self.assertEqual(after['pozycje_json'], before['pozycje_json'])
        self.assertLess(json.loads(after['pozycje_json'])[0]['ilosc'], 0)
        confirm_saldeo_correction(self.correction, 'KOR/1/2026', 1)
        with self.assertRaises(ValueError):
            confirm_saldeo_correction(self.correction, 'KOR/2/2026', 1)

    def test_invalid_number_permission_and_ordinary_invoice_leave_draft_unchanged(self):
        with self.assertRaises(ValueError):
            confirm_saldeo_correction(self.correction, '', 1)
        employee = self.make_employee()
        role = add_role('Bez obsługi faktur', can_view_invoices=False)
        assign_role(employee, role)
        with self.assertRaises(PermissionError):
            confirm_saldeo_correction(self.correction, 'KOR/1', employee)
        with self.assertRaises(ValueError):
            confirm_saldeo_correction(self.original, 'KOR/1', 1)
        self.assertFalse(get_invoice_by_id(self.correction)['eksportowana'])

    def test_invoice_module_alone_can_confirm_external_correction(self):
        employee = self.make_employee('invoice-only@example.test')
        role = add_role('Obsługa korekt', can_view_invoices=True)
        assign_role(employee, role)
        self.assertFalse(can(employee, 'can_export_data'))
        self.assertFalse(can(employee, 'can_view_finances'))
        confirm_saldeo_correction(self.correction, 'KOR/UPRAWNIENIA/2026', employee)
        invoice = get_invoice_by_id(self.correction)
        self.assertEqual(invoice['numer'], 'KOR/UPRAWNIENIA/2026')
        self.assertTrue(invoice['eksportowana'])
        self.assertEqual(invoice['kwota_brutto'], -123)

    def test_external_confirmation_can_reuse_deleted_draft_but_not_live_number(self):
        abandoned = create_correction(self.original, 'Roboczy numer')
        set_invoice_number(abandoned, 'KOR/REZERWACJA/2026')
        delete_draft_invoice(abandoned)
        confirm_saldeo_correction(self.correction, 'KOR/REZERWACJA/2026', 1)
        self.assertTrue(get_invoice_by_id(self.correction)['eksportowana'])
        other = create_correction(self.original, 'Inna korekta')
        with self.assertRaisesRegex(ValueError, 'zajęty|używany'):
            confirm_saldeo_correction(other, 'KOR/REZERWACJA/2026', 1)
        self.assertFalse(get_invoice_by_id(other)['eksportowana'])

    def test_dialog_requires_explicit_confirmation_of_external_issuance(self):
        from app.invoices import saldeo_correction_dialog as ui
        dialog = ui.SaldeoCorrectionDialog(get_invoice_by_id(self.correction), 1)
        dialog.number_input.setText('KOR/1')
        self.assertIn('FV/1/09/2026', dialog.details.toPlainText())
        self.assertIn('-123', dialog.details.toPlainText())
        with mock.patch.object(ui.QMessageBox, 'warning'), mock.patch.object(ui, 'confirm_saldeo_correction') as confirm:
            dialog._save()
            confirm.assert_not_called()
            dialog.confirmed.setChecked(True)
            dialog._save()
            confirm.assert_called_once_with(self.correction, 'KOR/1', 1)

    def test_export_routes_correction_before_filename_or_marking_it_issued(self):
        from app.invoices import faktury_ui as ui
        page = ui.FakturyPage(1)
        with mock.patch.object(page, '_prepare_saldeo_correction') as route, \
                mock.patch.object(ui.QFileDialog, 'getSaveFileName') as filename, \
                mock.patch.object(ui, 'mark_exported') as mark:
            page._export_ids([self.correction], mark=True)
            route.assert_called_once_with(self.correction)
            filename.assert_not_called()
            mark.assert_not_called()
