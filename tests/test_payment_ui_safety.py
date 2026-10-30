"""Bezpieczne potwierdzanie rozliczeń i wybór właściwego wiersza w GUI."""
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_explicit_encoding_is_remembered_and_passed_to_import(self):
        self.dialog.settings = Mock()
        self.dialog.encoding.setCurrentIndex(self.dialog.encoding.findData("iso8859_2"))
        with patch("app.invoices.payments_ui.QFileDialog.getOpenFileName", return_value=("statement.sta", "")), \
             patch("app.invoices.payments_ui.import_mt940_file", return_value={"all": 1, "new": 1, "duplicates": 0}) as importer, \
             patch.object(QMessageBox, "information"):
            self.dialog._import_file()
        importer.assert_called_once_with("statement.sta", 1, encoding="iso8859_2")
        self.dialog.settings.setValue.assert_called_with("mt940/encoding", "iso8859_2")

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

    def test_contractor_filter_is_per_transfer_and_has_manual_override(self):
        self.invoices[0].update(firma_id=101, buyer="Klient A", nip="1111111111")
        self.invoices.extend([
            {"id": 12, "firma_id": 202, "numer": "FV/2/2026", "buyer": "Klient B", "nip": "2222222222", "balance": Decimal("40")},
            {"id": 13, "firma_id": 101, "numer": "FV/OLD/2025", "buyer": "Klient A", "nip": "1111111111", "balance": Decimal("50")},
        ])
        self.transfers[1]["tytul"] = "FV/2/2026"
        self.dialog.refresh()
        first, second = (self.dialog.table.cellWidget(row, 4) for row in (0, 1))
        self.assertEqual({first.itemData(i) for i in range(1, first.count())}, {11, 13})
        self.assertEqual({second.itemData(i) for i in range(1, second.count())}, {12})
        self.assertIsNone(first.currentData())
        override = self.dialog.table.cellWidget(0, 7)
        self.assertFalse(override.isChecked())
        override.setChecked(True)
        self.assertEqual({first.itemData(i) for i in range(1, first.count())}, {11, 12, 13})
        first.setCurrentIndex(first.findData(12))
        override.setChecked(False)
        self.assertIsNone(first.currentData())

    def test_bulk_confirmation_cannot_overallocate_same_invoice(self):
        self.dialog.table.cellWidget(0, 4).setCurrentIndex(1)
        self.dialog.table.cellWidget(1, 4).setCurrentIndex(1)
        with patch("app.invoices.payments_ui.confirm_bank_payment") as confirm, \
             patch.object(QMessageBox, "question") as question, \
             patch.object(QMessageBox, "warning"):
            self.dialog._confirm_all()
        confirm.assert_not_called()
        question.assert_not_called()

    def test_ambiguous_contractor_keeps_manual_invoice_choice_available(self):
        self.invoices[0].update(firma_id=101)
        self.invoices.append({"id": 12, "firma_id": 202, "numer": "FV/2/2026", "buyer": "Klient", "balance": Decimal("40")})
        self.transfers[0].update(tytul="Zapłata", kontrahent="Klient")
        self.dialog.refresh()
        override = self.dialog.table.cellWidget(0, 7)
        combo = self.dialog.table.cellWidget(0, 4)
        self.assertTrue(override.isChecked())
        self.assertFalse(override.isEnabled())
        self.assertEqual({combo.itemData(i) for i in range(1, combo.count())}, {11, 12})
        self.assertIsNone(combo.currentData())

    def test_deleting_transfer_requires_confirmation(self):
        with patch("app.invoices.payments_ui.delete_bank_transaction") as delete, \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.dialog._delete_selected()
        delete.assert_not_called()
        with patch("app.invoices.payments_ui.delete_bank_transaction") as delete, \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox, "information"):
            self.dialog._delete_selected()
        delete.assert_called_once_with(1, 1)

    def test_october_transfer_filters_september_invoice_despite_different_payer_name(self):
        self.invoices[0].update(
            firma_id=101, numer="FV/0091/09/2026", buyer="FIRMA SP.J.", miesiac="2026-09", balance=Decimal("1279.20"),
        )
        self.invoices.append({
            "id": 12, "firma_id": 202, "numer": "FV/INNA/10/2026", "buyer": "IMIE NAZWISKO",
            "miesiac": "2026-10", "balance": Decimal("1279.20"),
        })
        self.transfers[0].update(
            data_operacji="2026-10-13", kwota=Decimal("1279.20"), remaining=Decimal("1279.20"),
            tytul="FV/0091/09/2026 FIRMA SP.J.", kontrahent="IMIE NAZWISKO",
        )
        self.dialog.refresh()
        combo = self.dialog.table.cellWidget(0, 4)
        self.assertEqual({combo.itemData(i) for i in range(1, combo.count())}, {11})
        self.assertIn("FV/0091/09/2026", combo.itemText(1))
        self.assertEqual(self.dialog.table.item(0, 6).text(), "FIRMA SP.J.")
        self.assertIsNone(combo.currentData())

    def test_bulk_confirmation_lists_invoice_allocations_before_saving(self):
        for row in (0, 1):
            self.dialog.table.cellWidget(row, 4).setCurrentIndex(1)
            self.dialog.table.cellWidget(row, 5).setText("20.00")
        with patch("app.invoices.payments_ui.confirm_bank_payment") as confirm, \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
            self.dialog._confirm_all()
        confirm.assert_not_called()
        text = question.call_args.args[2]
        self.assertIn("Przelew #1", text)
        self.assertIn("Przelew #2", text)
        self.assertIn("FV/1/2026", text)


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
        page.month_button.setText("09-2026")
        page.refresh()
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
        self.assertEqual(page.issued_table.horizontalHeaderItem(5).text(), "Podgląd")
        self.assertIsNotNone(page.issued_table.cellWidget(0, 5))
        self.assertEqual(page.issued_table.columnWidth(3), 72)
        self.assertEqual(page.issued_table.columnWidth(6), 88)
        self.assertEqual(page.issued_table.columnWidth(7), 88)
        self.assertEqual(page.issued_table.columnWidth(page.ISSUED_KSEF_COLUMN), 360)
        with patch("app.invoices.faktury_ui.QInputDialog.getText", return_value=("Korekta", True)), \
             patch.object(page, "_open_editor") as open_editor:
            page._create_correction()
        self.assertIs(page.tabs.currentWidget(), page.tab_robocze)
        open_editor.assert_called_once()

    def test_invoice_number_opens_local_pdf_preview(self):
        page = self.page()
        with patch("app.invoices.faktury_ui.InvoicePreviewDialog") as preview:
            page._on_issued_cell_clicked(0, page.ISSUED_NUMBER_COLUMN)
        preview.assert_called_once()
        self.assertEqual(preview.call_args.args[0]["id"], self.invoice_id)
        preview.return_value.exec.assert_called_once()

    def test_invoice_preview_saves_a_real_pdf(self):
        from app.invoices.invoice_preview import InvoicePreviewDialog
        dialog = InvoicePreviewDialog(get_invoice_by_id(self.invoice_id))
        self.addCleanup(dialog.close)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "faktura.pdf"
            with patch("app.invoices.invoice_preview.QFileDialog.getSaveFileName",
                       return_value=(str(destination), "PDF (*.pdf)")), \
                 patch("app.invoices.invoice_preview.QMessageBox.information"):
                dialog._save_pdf()
            self.assertTrue(destination.read_bytes().startswith(b"%PDF"))

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
