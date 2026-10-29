"""Sprawdzenie wyboru terminu płatności bez kalendarza."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.admin.settings.invoice_settings import InvoiceSettings
from app.utils.payment_terms import DAY_OF_MONTH_KEY, MODE_DAY_OF_MONTH, MODE_KEY


class FakeSettings:
    def __init__(self):
        self.settings = {MODE_KEY: MODE_DAY_OF_MONTH, DAY_OF_MONTH_KEY: "07"}

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value


class InvoiceSettingsDayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_07_is_selected_and_saved_without_calendar_date(self):
        logic = FakeSettings()
        with patch("app.admin.settings.invoice_settings.GlobalSettingsLogic", return_value=logic), \
             patch("app.admin.settings.invoice_settings.is_administrator", return_value=False), \
             patch("app.admin.settings.invoice_settings.QMessageBox.information"):
            widget = InvoiceSettings(pracownik_id=123)
            try:
                self.assertEqual(widget.term_mode.currentData(), MODE_DAY_OF_MONTH)
                self.assertEqual(widget.term_day_of_month.currentText(), "07")
                self.assertEqual(widget.term_day_of_month.count(), 31)
                widget._save()
                self.assertEqual(logic.settings[DAY_OF_MONTH_KEY], "07")

                widget.term_day_of_month.setCurrentText("09")
                widget._save()
                self.assertEqual(logic.settings[DAY_OF_MONTH_KEY], "09")
            finally:
                widget.close()


if __name__ == "__main__":
    unittest.main()
