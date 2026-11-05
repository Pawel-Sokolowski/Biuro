"""Seller fields must be editable on the actual navigated settings page."""
from unittest import mock
from PyQt6.QtWidgets import QApplication, QScrollArea
from app.admin.settings.settings_ui import GlobalSettingsPage
from app.admin.settings.invoice_settings import InvoiceSettings
from app.db.dao_app_settings import get_setting
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(['seller-settings'])


class SellerSettingsNavigationTests(DbTestCase):
    def test_seller_contact_fields_are_visible_on_top_and_saved(self):
        page = GlobalSettingsPage(1, True)
        self.addCleanup(page.close)
        page.resize(1100, 800)
        page.show()
        row = next(i for i in range(page.nav.count()) if page.nav.item(i).text().strip() == 'Faktury i KSeF')
        page.nav.setCurrentRow(row)
        _app.processEvents()
        area = page.stack.currentWidget()
        self.assertIsInstance(area, QScrollArea)
        settings = area.widget()
        self.assertIsInstance(settings, InvoiceSettings)
        for key, text in [('faktury_sprzedawca_regon', '123456789'),
                          ('faktury_sprzedawca_email', 'faktury@biuro.example')]:
            field = settings.seller_fields[key]
            self.assertTrue(field.isVisibleTo(area.viewport()))
            self.assertLess(field.y(), settings.number_pattern.y())
            field.setText(text)
        with mock.patch('app.admin.settings.invoice_settings.QMessageBox.information'):
            settings._save()
        self.assertEqual(get_setting('faktury_sprzedawca_regon'), '123456789')
        self.assertEqual(get_setting('faktury_sprzedawca_email'), 'faktury@biuro.example')
