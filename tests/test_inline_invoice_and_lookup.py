"""Invoice row editing, registry form lookup and real PDF export."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget, QTableWidgetItem, QLineEdit, QFileDialog, QMessageBox, QPushButton
from app.invoices import faktury_ui as ui
from app.invoices.manual_invoice_dialog import KontrahentDialog
from app.invoices.invoice_preview import InvoicePreviewDialog
from app.invoices.positions import normalize_position, totals


class InlineInvoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.invoice = {"id": 5, "firma_id": 2, "eksportowana": 0, "numer": "FV/5", "data_wystawienia": "2026-11-01"}
        self.positions = [normalize_position({"nazwa": "Księgowość", "ilosc": 2, "cena_netto": 100,
                                              "vat": 23, "usluga_id": 12, "generated": True})]
        self.updates = []
        self.page = ui.FakturyPage.__new__(ui.FakturyPage)
        QWidget.__init__(self.page)
        self.addCleanup(self.page.close)
        self.page.pracownik_id = 1
        self.page.tab_robocze = QWidget(self.page)
        self.page.btn_generate_all = QPushButton("Generuj faktury", self.page)
        self.page._build_tab_robocze()
        self.page.refresh = self.page._show_draft_positions
        mocks = [patch.object(ui, "get_invoice_by_id", side_effect=lambda _: dict(self.invoice)),
                 patch.object(ui, "get_invoice_positions", side_effect=lambda _: deepcopy(self.positions)),
                 patch.object(ui, "can_access_invoice", return_value=True),
                 patch.object(ui, "update_invoice_draft", side_effect=self._update),
                 patch.object(ui, "get_extra_services", return_value=[
                     {"id": 8, "nazwa": "Konsultacja", "cena_netto": 50, "stawka_vat": "8%", "kategoria": "inne"}])]
        for mock in mocks:
            mock.start()
            self.addCleanup(mock.stop)
        self.page.draft_table.setRowCount(1)
        self.page.draft_table.setItem(0, 0, QTableWidgetItem("5"))
        self.page.draft_table.selectRow(0)

    def _update(self, invoice_id, positions, **header):
        self.updates.append((invoice_id, deepcopy(positions), header))
        self.positions = [normalize_position(p) for p in positions]

    def edit(self, column, text):
        editor = QLineEdit(text)
        self.page.positions_table.itemDelegate().setModelData(
            editor, self.page.positions_table.model(), self.page.positions_table.model().index(0, column))

    def test_all_editable_columns_save_and_preserve_tracking_and_header(self):
        for column, text in ((0, "Nowa nazwa"), (1, "3"), (2, "godz."), (3, "25,50"), (4, "8"), (7, "doradztwo")):
            self.edit(column, text)
        position = self.positions[0]
        self.assertEqual(position["nazwa"], "Nowa nazwa")
        self.assertEqual((position["netto"], position["brutto"]), (76.5, 82.62))
        self.assertEqual(position["jednostka"], "godz.")
        self.assertEqual(position["kategoria"], "doradztwo")
        self.assertEqual(position["usluga_id"], 12)
        self.assertTrue(position["generated"])
        self.assertEqual(self.updates[-1][2]["numer"], "FV/5")
        self.assertFalse(self.page.positions_table.item(0, 5).flags() & Qt.ItemFlag.ItemIsEditable)

    def test_header_form_is_not_shown_inline_and_header_survives_position_edit(self):
        self.assertFalse(hasattr(self.page, "inline_invoice_number"))
        self.edit(0, "Usługa po zmianie")
        self.assertEqual(self.updates[-1][2]["numer"], "FV/5")
        self.assertEqual(self.updates[-1][2]["data_wystawienia"], "2026-11-01")

    def test_catalogue_is_reloaded_when_new_service_is_added(self):
        self.page._show_draft_positions()
        self.assertEqual(self.page.service_choice.count(), 2)
        with patch.object(ui, "get_extra_services", return_value=[
                {"id": 8, "nazwa": "Konsultacja", "cena_netto": 50, "stawka_vat": "8%", "kategoria": "inne"},
                {"id": 9, "nazwa": "Nowa usługa", "cena_netto": 10, "stawka_vat": "23%", "kategoria": "inne"}]):
            self.page._show_draft_positions()
        self.assertEqual(self.page.service_choice.count(), 3)
    def test_invalid_numbers_and_blank_name_do_not_write(self):
        for column, text in ((1, "NaN"), (3, "abc"), (4, "101"), (0, "")):
            with patch.object(QMessageBox, "warning") as warning:
                self.edit(column, text)
            warning.assert_called_once()
        self.assertEqual(self.updates, [])

    def test_catalogue_selection_adds_service_inline_and_recalculates(self):
        self.page.service_choice.setCurrentIndex(1)
        self.page.service_quantity.setValue(2)
        self.page._add_inline_service()
        self.assertEqual(len(self.positions), 2)
        self.assertEqual(self.positions[1]["nazwa"], "Konsultacja")
        self.assertEqual(self.positions[1]["brutto"], 108)
        self.assertIn("354.00", self.page.positions_total.text())

    def test_last_position_cannot_be_removed_and_issued_document_cannot_change(self):
        self.page.positions_table.setCurrentCell(0, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, "warning"):
            self.page._remove_inline_position()
        self.assertEqual(self.updates, [])
        self.invoice["eksportowana"] = 1
        self.edit(1, "4")
        self.assertEqual(self.updates, [])
        self.assertFalse(self.page.positions_table.isEnabled())

    def test_edit_action_focuses_existing_panel_without_opening_editor_window(self):
        with patch.object(self.page, "_open_editor") as modal:
            self.page._edit_selected_draft()
        modal.assert_not_called()
        self.assertEqual(self.page.positions_table.currentColumn(), 0)


class ContractorLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_lookup_populates_business_fields_and_preserves_missing_contact(self):
        dialog = KontrahentDialog()
        dialog.nip.setText("123-456-78-90")
        dialog.email.setText("existing@example.com")
        data = {"nazwa": "Przykład sp. z o.o.", "nip": "1234567890", "regon": "123456789",
                "dz_ulica": "Testowa", "dz_budynek": "7", "dz_lokal": "2", "dz_kod": "00-001", "dz_miasto": "Warszawa"}
        with patch("app.invoices.manual_invoice_dialog.lookup_company_by_nip", return_value=data) as lookup, \
             patch("app.invoices.manual_invoice_dialog.QThreadPool.globalInstance") as pool:
            pool.return_value.start.side_effect = lambda worker: worker.run()
            dialog._lookup_company()
        lookup.assert_called_once_with("1234567890")
        values = dialog.values()
        self.assertEqual(values["nazwa"], data["nazwa"])
        self.assertEqual(values["adres"], "Testowa 7/2")
        self.assertEqual(values["kod"], "00-001")
        self.assertEqual(values["email"], "existing@example.com")
        self.assertEqual(values["typ"], "firma")
        self.assertTrue(dialog.btn_lookup.isEnabled())

    def test_invalid_nip_and_wrong_registry_nip_do_not_replace_data(self):
        dialog = KontrahentDialog({"nazwa": "Ręczna nazwa"})
        with patch.object(QMessageBox, "warning"), patch("app.invoices.manual_invoice_dialog.QThreadPool.globalInstance") as pool:
            dialog.nip.setText("123")
            dialog._lookup_company()
            pool.assert_not_called()
            dialog._lookup_nip = "1234567890"
            dialog._lookup_result((dialog, {"nip": "9876543210", "nazwa": "Wrong company"}))
        self.assertEqual(dialog.nazwa.text(), "Ręczna nazwa")


class InvoicePdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @unittest.skipUnless(shutil.which("pdftotext") and shutil.which("pdfinfo"), "PDF inspection tools unavailable")
    def test_short_and_multipage_exports_keep_rows_and_a4_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            for count in (3, 60):
                positions = [normalize_position({"nazwa": f"Consultation {i+1}", "ilosc": 1, "cena_netto": 100, "vat": 23}) for i in range(count)]
                invoice = {"id": 1, "numer": "FV/1/2026", "nabywca_pelna": "Example Buyer", "kwota_netto": count*100, "kwota_brutto": count*123}
                destination = Path(folder) / f"invoice-{count}.pdf"
                with patch("app.invoices.invoice_preview.get_setting", return_value=None), \
                     patch("app.invoices.invoice_preview.get_setting_first", return_value="Example Seller"), \
                     patch("app.invoices.invoice_preview.get_invoice_positions", return_value=positions), \
                     patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")), \
                     patch.object(QMessageBox, "information"):
                    dialog = InvoicePreviewDialog(invoice)
                    dialog._save_pdf()
                    dialog.close()
                text = subprocess.check_output(["pdftotext", "-layout", str(destination), "-"], text=True)
                self.assertIn("Example Seller", text)
                self.assertIn("Example Buyer", text)
                self.assertIn(f"Consultation {count}", text)
                self.assertIn(f"{count*123:.2f}", text)
                info = subprocess.check_output(["pdfinfo", str(destination)], text=True)
                self.assertIn("A4", info)
                if count == 3:
                    self.assertRegex(info, r"Pages:\s+1\b")
                else:
                    self.assertGreater(text.count("\f"), 1)
