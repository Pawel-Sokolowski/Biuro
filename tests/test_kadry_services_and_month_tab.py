"""Tests for invoice service labels and distinct accounting/payroll months."""
import datetime
import unittest
from unittest.mock import patch

from app.invoices.faktury_logic import FakturyLogic


class KadryInvoiceNamesTests(unittest.TestCase):
    def test_invoices_show_name_from_selected_pricing_entry(self):
        company = {
            "uop_ilosc": 2, "uop_cennik": "Usługa - UoP 100,00",
            "uz_ilosc": 3, "uz_cennik": "Usługa płacowa - UZ",
            "uod_ilosc": 0, "uod_cennik": None,
        }
        with patch.object(FakturyLogic, "_kadry_vat", return_value=23), \
             patch("app.invoices.faktury_logic.get_conn") as connect:
            cur = connect.return_value.cursor.return_value
            cur.fetchone.side_effect = [
                {"cena_netto_kadry": 100, "opis": "Usługa kadrowo-płacowa - UoP"},
                {"cena_netto_kadry": 30, "opis": "Usługa płacowa - UZ"},
            ]
            result = FakturyLogic()._kadry_positions(company, 0)
        self.assertEqual(
            [(p["nazwa"], p["ilosc"], p["netto"]) for p in result],
            [
                ("Usługa kadrowo-płacowa – UoP", 2, 200),
                ("Usługa płacowa – UZ", 3, 90),
            ],
        )
        self.assertEqual(cur.execute.call_count, 2)

    def test_missing_description_has_appropriate_fallback(self):
        with patch.object(FakturyLogic, "_kadry_vat", return_value=23), \
             patch("app.invoices.faktury_logic.get_conn") as connect:
            connect.return_value.cursor.return_value.fetchone.return_value = {
                "cena_netto_kadry": 40, "opis": None,
            }
            positions = FakturyLogic()._kadry_positions(
                {"uop_ilosc": 1, "uop_cennik": "UOP_40"}, 0
            )
        self.assertEqual(positions[0]["nazwa"], "Usługa kadrowo-płacowa – UoP")

    def test_fixed_monthly_fee_name(self):
        with patch.object(FakturyLogic, "_kadry_vat", return_value=23):
            positions = FakturyLogic()._kadry_positions({}, 210)
        self.assertEqual(positions[0]["nazwa"], "Usługa kadrowo-płacowa")
        self.assertEqual(positions[0]["netto"], 210)


class MonthlyTabsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(["kadry-tests"])

    def test_month_selection_is_independent_per_tab(self):
        from app.monthly_view.monthly_view_ui import MonthlyViewPage
        with patch("app.monthly_view.monthly_view_ui.MonthlyViewLogic") as factory, \
             patch.object(MonthlyViewPage, "_load_initial_data"), \
             patch.object(MonthlyViewPage, "_setup_timer"), \
             patch.object(MonthlyViewPage, "_reload_rows"):
            factory.return_value.aktualny_miesiac = "2026-09"
            page = MonthlyViewPage(1)
            try:
                self.assertEqual(page.month_combo.currentText(), "2026-09")
                page.tabs.setCurrentIndex(1)
                current = datetime.date.today().strftime("%Y-%m")
                self.assertEqual(page.month_combo.currentText(), current)
                self.assertEqual(factory.return_value.set_month.call_args.args[0], current)
                page.month_combo.setCurrentText("2026-08")
                page._on_filters_changed()
                page.tabs.setCurrentIndex(0)
                self.assertEqual(page.month_combo.currentText(), "2026-09")
                page.tabs.setCurrentIndex(1)
                self.assertEqual(page.month_combo.currentText(), "2026-08")
            finally:
                page.close()


if __name__ == "__main__":
    unittest.main()
