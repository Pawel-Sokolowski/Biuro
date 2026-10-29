"""Bezpieczne potwierdzanie rozliczeń i wybór właściwego wiersza w GUI."""
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox

from app.db.dao_conn import get_conn
from app.db.dao_invoices import create_or_update_invoice, get_invoice_by_id, mark_exported
from app.db.dao_payments import record_cash_payment
from app.invoices.payments_ui import BankPaymentReview, record_cash_in_dialog
from tests.base import DbTestCase


class BankReviewSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["payment-safety-tests"])

    def setUp(self):
        self.invoices = [{"id": 11, "numer": "FV/1/2026", "buyer": "Klient", "balance": Decimal("40.00")}]
        self.transfers = [
            {"id": 1, "data_operacji": "2026-09-07", "kwota": Decimal("123"), "remaining": Decimal("123"), "tytul": "FV/1/2026"},
            {"id": 2, "data_operacji": "2026-09-08", "kwota": Decimal("123"), "remaining": Decimal("123"), "tytul": "FV/1/2026"},
        ]
        self.choices_patch = patch("app.invoices.payments_ui.list_invoice_choices", return_value=self.invoices)
        self.transfers_patch = patch("app.invoices.payments_ui.list_unsettled_transfers", return_value=self.transfers)
        self.choices_patch.start()
        self.transfers_patch.start()
        self.addCleanup(self.choices_patch.stop)
        self.addCleanup(self.transfers_patch.stop)
        self.dialog = BankPaymentReview(1)
        self.addCleanup(self.dialog.close)

    def test_editing_invoice_selects_its_transfer_and_caps_split_amount(self):
        self.dialog.table.cellWidget(1, 4).setCurrentIndex(1)
        self.assertEqual(self.dialog.table.currentRow(), 1)
        self.assertEqual(self.dialog.table.cellWidget(1, 5).text(), "40.00")
        with patch("app.invoices.payments_ui.confirm_bank_payment") as confirm, \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox, "information"):
            self.dialog._confirm()
        confirm.assert_called_once_with(2, 11, Decimal("40.00"), 1)

    def test_editing_amount_selects_its_transfer(self):
        self.dialog.table.setCurrentCell(0, 0)
        editor = self.dialog.table.cellWidget(1, 5)
        editor.clear()
        QTest.keyClicks(editor, "20")
        self.assertEqual(self.dialog.table.currentRow(), 1)

    def test_unselected_invoice_and_declined_confirmation_never_write(self):
        with patch("app.invoices.payments_ui.confirm_bank_payment") as confirm, \
             patch.object(QMessageBox, "warning"):
            self.dialog._confirm()
            confirm.assert_not_called()
        self.dialog.table.cellWidget(0, 4).setCurrentIndex(1)
        with patch("app.invoices.payments_ui.confirm_bank_payment") as confirm, \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.dialog._confirm()
            confirm.assert_not_called()

    def test_invalid_amounts_are_rejected_before_confirmation(self):
        self.dialog.table.cellWidget(0, 4).setCurrentIndex(1)
        for amount in ("NaN", "Infinity", "0", "-1", "1.001", "41.00"):
            with self.subTest(amount=amount):
                self.dialog.table.cellWidget(0, 5).setText(amount)
                with patch("app.invoices.payments_ui.confirm_bank_payment") as confirm, \
                     patch.object(QMessageBox, "question") as question, \
                     patch.object(QMessageBox, "warning") as warning:
                    self.dialog._confirm()
                confirm.assert_not_called()
                question.assert_not_called()
                warning.assert_called_once()


class InvoicePaymentActionSafetyTests(DbTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["payment-safety-tests"])

    def setUp(self):
        super().setUp()
        self.company_id = self.make_company("Klient UI")
        positions = [{"nazwa": "Księgowość", "ilosc": 1, "netto": 100, "brutto": 123, "vat": 23}]
        self.invoice_id = create_or_update_invoice(self.company_id, "2026-09", positions, 100, 123)
        conn = get_conn()
        try:
            conn.execute("UPDATE faktury SET numer = 'FV/001/09/2026' WHERE id = %s", (self.invoice_id,))
            conn.commit()
        finally:
            conn.close()
        mark_exported([self.invoice_id], 1)

    def page(self):
        from app.invoices.faktury_ui import FakturyPage
        page = FakturyPage(1)
        self.addCleanup(page.close)
        page.issued_table.selectRow(0)
        return page

    def test_cash_dialog_defaults_to_remaining_balance(self):
        record_cash_payment(self.invoice_id, "2026-09-06", "23.00", 1)
        captured = []

        def cancel(form):
            captured.append(form.findChild(QLineEdit).text())
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", cancel):
            self.assertFalse(record_cash_in_dialog(get_invoice_by_id(self.invoice_id), 1))
        self.assertEqual(captured, ["100.00"])

    def test_fully_paid_invoice_does_not_open_cash_form(self):
        record_cash_payment(self.invoice_id, "2026-09-07", "123.00", 1)
        with patch.object(QDialog, "exec") as show_form, patch.object(QMessageBox, "information"):
            self.assertFalse(record_cash_in_dialog(get_invoice_by_id(self.invoice_id), 1))
        show_form.assert_not_called()

    def test_issued_is_default_and_correction_opens_drafts_by_widget(self):
        page = self.page()
        self.assertIs(page.tabs.currentWidget(), page.tab_wystawione)
        with patch("app.invoices.faktury_ui.QInputDialog.getText", return_value=("Korekta", True)), \
             patch.object(page, "_open_editor") as open_editor:
            page._create_correction()
        self.assertIs(page.tabs.currentWidget(), page.tab_robocze)
        open_editor.assert_called_once()

    def test_stale_draft_selection_cannot_trigger_cash_action(self):
        page = self.page()
        conn = get_conn()
        try:
            conn.execute("UPDATE faktury SET eksportowana=0 WHERE id=%s", (self.invoice_id,))
            conn.commit()
        finally:
            conn.close()
        with patch("app.invoices.faktury_ui.record_cash_in_dialog") as cash, patch.object(QMessageBox, "warning"):
            page._mark_cash_payment()
        cash.assert_not_called()

    def test_financial_permission_is_required_before_opening_cash_dialog(self):
        page = self.page()
        with patch("app.invoices.faktury_ui.is_administrator", return_value=False), \
             patch("app.invoices.faktury_ui.can", return_value=False), \
             patch("app.invoices.faktury_ui.record_cash_in_dialog") as cash, \
             patch.object(QMessageBox, "warning"):
            page._mark_cash_payment()
        cash.assert_not_called()

    def test_ksef_transmission_reports_processing_without_acceptance(self):
        page = self.page()
        with patch("app.invoices.faktury_ui.ksef_configured", return_value=True), \
             patch("app.invoices.faktury_ui.QFileDialog.getOpenFileName", return_value=("invoice.xml", "")), \
             patch("app.invoices.faktury_ui.send_invoice_xml", return_value={"session_reference": "SES", "invoice_reference": "INV"}) as send, \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox, "information") as information:
            page._send_selected_to_ksef()
        send.assert_called_once_with(self.invoice_id, "invoice.xml", 1)
        message = information.call_args.args[2]
        self.assertIn("przetwarzania", message)
        self.assertNotIn("przyjęta", message)


if __name__ == "__main__":
    unittest.main()
