"""Personal preferences and invoice catalogue interactions without a live database."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QPalette, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QTableWidget, QTableWidgetItem, QPushButton, QWidget, QVBoxLayout, QCheckBox, QStyle, QStyleOptionButton
from app.preferences import appearance
from app.invoices.service_picker import ServicePicker
from app.invoices.manual_invoice_dialog import ManualInvoiceDialog
from app.invoices.invoice_editor import InvoiceEditorDialog
from app.admin.settings.settings_ui import GlobalSettingsPage

SERVICE = {"id": 8, "nazwa": "Rozliczenie roczne", "cena_netto": 120.50,
           "stawka_vat": "8%", "kategoria": "księgowość"}


class AppearanceAndCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat)
        patcher = patch.object(appearance, "QSettings", return_value=settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.app.setStyleSheet, "")
        self.addCleanup(self.app.setPalette, self.app.palette())

    def test_preferences_apply_immediately_persist_and_are_isolated_by_user(self):
        widget = appearance.AppearanceSettings(12)
        widget.mode.setCurrentIndex(widget.mode.findData("dark"))
        widget.accent.setCurrentIndex(widget.accent.findData("#047857"))
        self.assertEqual(appearance.preferences(12), ("dark", "#047857"))
        self.assertEqual(appearance.preferences(13), ("light", "#2563eb"))
        self.assertLess(self.app.palette().color(QPalette.ColorRole.Base).lightness(), 80)
        reopened = appearance.AppearanceSettings(12)
        self.assertEqual(reopened.mode.currentData(), "dark")
        self.assertEqual(reopened.accent.currentData(), "#047857")
        appearance.apply_appearance(13)
        self.assertGreater(self.app.palette().color(QPalette.ColorRole.Base).lightness(), 200)

    def test_normal_user_has_appearance_without_administrative_pages(self):
        logic = Mock()
        logic.get_user_permissions.return_value = {}
        with patch("app.admin.settings.settings_ui.GlobalSettingsLogic", return_value=logic):
            page = GlobalSettingsPage(12, False)
        labels = [page.nav.item(i).text().strip() for i in range(page.nav.count())]
        self.assertEqual(labels, ["MOJE USTAWIENIA", "Wygląd", "Synchronizacja poczty"])
        self.assertIsInstance(page._built[1], appearance.AppearanceSettings)

    def test_administrative_pages_are_preserved_for_authorized_users(self):
        logic = Mock()
        logic.get_user_permissions.return_value = {"can_access_admin_settings": True, "can_manage_users": True}
        with patch("app.admin.settings.settings_ui.GlobalSettingsLogic", return_value=logic):
            page = GlobalSettingsPage(1, True)
        labels = [page.nav.item(i).text().strip() for i in range(page.nav.count())]
        self.assertIn("Wygląd", labels)
        self.assertIn("Użytkownicy", labels)
        self.assertIn("Usługi dodatkowe", labels)

    def test_selected_row_stays_filled_after_focus_leaves_in_both_themes(self):
        for mode in ("light", "dark"):
            with self.subTest(mode=mode), patch.object(appearance, "preferences", return_value=(mode, "#2563eb")):
                appearance.apply_appearance(1)
                window = QWidget()
                layout = QVBoxLayout(window)
                table = QTableWidget(2, 3)
                table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                for row in range(2):
                    for column in range(3):
                        table.setItem(row, column, QTableWidgetItem("Firma"))
                table.item(1, 2).setBackground(QColor("#f4a6a6"))
                table.item(1, 2).setForeground(QColor("black"))
                layout.addWidget(table)
                button = QPushButton("Edytuj", window)
                layout.addWidget(button)
                window.resize(500, 220)
                window.show()
                self.addCleanup(window.close)
                self.app.processEvents()
                QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                                 pos=table.visualItemRect(table.item(1, 0)).center())
                button.setFocus()
                self.app.processEvents()
                self.assertEqual(len(table.selectedItems()), 3)
                rendered = table.viewport().grab().toImage()
                for column in range(3):
                    rect = table.visualItemRect(table.item(1, column))
                    self.assertEqual(rendered.pixelColor(rect.right() - 8, rect.center().y()).name(), "#2563eb")
                window.close()

    def test_checkbox_marks_remain_visible_in_both_themes_and_scaled_item_views(self):
        for mode in ("light", "dark"):
            with self.subTest(mode=mode), patch.object(appearance, "preferences", return_value=(mode, "#2563eb")):
                appearance.apply_appearance(1)
                checkbox = QCheckBox("Zdrowotna wliczona do ceny")
                checkbox.show()
                self.addCleanup(checkbox.close)
                self.app.processEvents()
                before = checkbox.grab().toImage()
                QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=checkbox.rect().center())
                self.assertTrue(checkbox.isChecked())
                self.assertTrue(checkbox.isVisible())
                self.assertNotEqual(before, checkbox.grab().toImage())
                # Exercise the same painter used for checkable table cells,
                # including disabled, partially checked and selected states at HiDPI.
                for scale in (1, 1.5, 2):
                    for enabled in (True, False):
                        for selected in (True, False):
                            images = []
                            for mark in (QStyle.StateFlag.State_Off, QStyle.StateFlag.State_On, QStyle.StateFlag.State_NoChange):
                                option = QStyleOptionButton()
                                option.rect.setRect(0, 0, 20, 20)
                                option.palette = self.app.palette()
                                option.state = mark
                                if enabled:
                                    option.state |= QStyle.StateFlag.State_Enabled
                                if selected:
                                    option.state |= QStyle.StateFlag.State_Selected
                                image = QImage(int(20*scale), int(20*scale), QImage.Format.Format_ARGB32)
                                image.setDevicePixelRatio(scale)
                                image.fill(QColor("#ff00ff"))
                                painter = QPainter(image)
                                self.app.style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck, option, painter)
                                painter.end()
                                self.assertNotEqual(image.pixelColor(int(10*scale), int(10*scale)), QColor("#ff00ff"))
                                images.append(image)
                            self.assertNotEqual(images[0], images[1])
                            self.assertNotEqual(images[1], images[2])
                checkbox.close()

    def test_catalogue_filter_quantity_price_vat_and_empty_selection(self):
        picker = ServicePicker([SERVICE])
        self.assertFalse(picker.add_button.isEnabled())
        picker.services.setCurrentRow(0)
        picker.quantity.setValue(2.5)
        position = picker.position()
        self.assertEqual(position["cena_netto"], 120.50)
        self.assertEqual(position["netto"], 301.25)
        self.assertEqual(position["brutto"], 325.35)
        self.assertEqual(position["vat"], 8)
        picker.search.setText("nieistniejąca")
        self.assertFalse(picker.add_button.isEnabled())
        self.assertIsNone(picker.position())
        self.assertIsNone(ServicePicker([]).position())

    def test_manual_invoice_uses_placeholder_and_retains_editable_catalogue_values(self):
        picker = ServicePicker([SERVICE])
        picker.services.setCurrentRow(0)
        position = picker.position()
        with patch("app.invoices.manual_invoice_dialog.default_due_date", return_value=date(2026, 11, 10)):
            dialog = ManualInvoiceDialog()
        with patch("app.invoices.manual_invoice_dialog.choose_service", return_value=position):
            dialog._add_catalogue_service()
        self.assertEqual(dialog.items.rowCount(), 1)
        dialog.items.item(0, 1).setText("2")
        self.assertEqual(dialog._positions()[0]["netto"], 241)
        self.assertEqual(dialog._positions()[0]["kategoria"], "księgowość")
        with patch("app.invoices.manual_invoice_dialog.choose_service", return_value=None):
            dialog._add_catalogue_service()
        self.assertEqual(dialog.items.rowCount(), 1)

    def test_draft_editor_recalculates_catalogue_and_issued_invoice_cannot_add(self):
        picker = ServicePicker([SERVICE])
        picker.services.setCurrentRow(0)
        invoice = {"id": 9, "eksportowana": 0}
        with patch("app.invoices.invoice_editor.get_invoice_positions", return_value=[]):
            editor = InvoiceEditorDialog(invoice)
            issued = InvoiceEditorDialog({**invoice, "eksportowana": 1})
        with patch("app.invoices.invoice_editor.choose_service", return_value=picker.position()) as choose:
            editor._add_catalogue_service()
            issued._add_catalogue_service()
        choose.assert_called_once()
        self.assertEqual(editor.positions()[0]["brutto"], 130.14)
        self.assertIn("130.14", editor.lbl_total.text())
        self.assertFalse(issued.btn_catalogue.isEnabled())
        self.assertEqual(issued.table.rowCount(), 0)
