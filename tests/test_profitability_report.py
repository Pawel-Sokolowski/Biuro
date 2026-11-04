import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.reports.import_parse import SEKCJA_LABELS
from app.reports.reports_ui import PROFIT_MODE, ReportsPage
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(["biuro-tests"])


def row(klient, miesiac, sekcja, cena, koszt, sekundy=3600):
    return {
        "klient": klient, "miesiac": miesiac, "sekcja": sekcja, "pracownik": "Jan Kowalski", "ilosc": 1,
        "sek_glowny": sekundy, "cena_glowna": cena, "sek_dodatkowy": 0, "cena_dodatkowa": 0.0,
        "sekundy": sekundy, "cena": cena, "koszt": koszt, "zysk": (cena or 0) - (koszt or 0),
    }


ROWS = [
    row("Alfa", "2026-01", "ksiegowosc", 100.0, 40.0),
    row("Alfa", "2026-01", "ksiegowosc", 50.0, 10.0),
    row("Alfa", "2026-02", "ksiegowosc", 200.0, 250.0),
    row("Alfa", "2026-01", "kadry", 30.0, 10.0),
    row("Beta", "2026-01", "kadry", 80.0, 20.0),
]


def cells(table):
    return [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]


class ProfitabilityReportTests(DbTestCase):
    def setUp(self):
        super().setUp()
        for name in ("warning", "information", "critical", "question"):
            patcher = mock.patch.object(QMessageBox, name, return_value=QMessageBox.StandardButton.Yes)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.emp = self.make_employee()
        self.make_company("Alfa")
        self.page = ReportsPage(self.emp)
        self.addCleanup(self.page.deleteLater)

    def test_aggregate_profit_sums_each_company_month_and_section(self):
        agg = ReportsPage._aggregate_profit(ROWS)
        self.assertEqual(agg[("Alfa", "2026-01", "ksiegowosc")], [150.0, 50.0])
        self.assertEqual(agg[("Alfa", "2026-02", "ksiegowosc")], [200.0, 250.0])
        self.assertEqual(agg[("Alfa", "2026-01", "kadry")], [30.0, 10.0])
        self.assertEqual(agg[("Beta", "2026-01", "kadry")], [80.0, 20.0])
        self.assertEqual(len(agg), 4)

    def test_aggregate_profit_treats_missing_values_as_zero(self):
        agg = ReportsPage._aggregate_profit([row("Alfa", "2026-01", "kadry", None, None)])
        self.assertEqual(agg[("Alfa", "2026-01", "kadry")], [0.0, 0.0])

    def test_common_table_has_section_totals_with_profit(self):
        self.page._render_import_report(ROWS)
        self.assertEqual(cells(self.page.common_table), [
            [SEKCJA_LABELS["ksiegowosc"], "350.00 zł", "300.00 zł", "50.00 zł"],
            [SEKCJA_LABELS["kadry"], "110.00 zł", "30.00 zł", "80.00 zł"],
        ])

    def test_company_table_lists_every_company_and_section_separately(self):
        self.page._render_import_report(ROWS)
        self.assertEqual(cells(self.page.company_table), sorted([
            ["Alfa", SEKCJA_LABELS["ksiegowosc"], "350.00 zł", "300.00 zł", "50.00 zł"],
            ["Alfa", SEKCJA_LABELS["kadry"], "30.00 zł", "10.00 zł", "20.00 zł"],
            ["Beta", SEKCJA_LABELS["kadry"], "80.00 zł", "20.00 zł", "60.00 zł"],
        ], key=lambda r: (r[0], r[1])))

    def test_negative_profit_is_marked_red(self):
        self.page._render_import_report([row("Alfa", "2026-01", "ksiegowosc", 10.0, 50.0)])
        item = self.page.common_table.item(0, 3)
        self.assertEqual(item.text(), "-40.00 zł")
        self.assertEqual(item.foreground().color().name(), "#c62828")

    def test_empty_result_clears_tables_and_shows_message(self):
        self.page._render_import_report(ROWS)
        self.page._render_import_report([])
        self.assertEqual(self.page.common_table.rowCount(), 0)
        self.assertEqual(self.page.company_table.rowCount(), 0)
        self.assertIn("Brak", self.page.report_status.text())

    def test_excel_export_has_one_row_per_company_month_and_section(self):
        import openpyxl
        self.page.mode_combo.setCurrentText(PROFIT_MODE)
        self.page.firm_list.selectAll()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "rentownosc.xlsx")
            with mock.patch.object(ReportsPage, "_profit_rows", return_value=ROWS), \
                    mock.patch.object(QFileDialog, "getSaveFileName", return_value=(path, "")):
                self.page._export_to_excel()
            sheet = openpyxl.load_workbook(path).active
            data = [list(r) for r in sheet.iter_rows(values_only=True)]
        self.assertEqual(sheet.title, "Rentowność")
        self.assertEqual(data[0], ["Firma", "Miesiąc", "Dział", "Sprzedaż", "Koszt", "Zysk"])
        self.assertEqual(data[1:], [
            ["Alfa", "2026-01", SEKCJA_LABELS["kadry"], 30.0, 10.0, 20.0],
            ["Alfa", "2026-01", SEKCJA_LABELS["ksiegowosc"], 150.0, 50.0, 100.0],
            ["Alfa", "2026-02", SEKCJA_LABELS["ksiegowosc"], 200.0, 250.0, -50.0],
            ["Beta", "2026-01", SEKCJA_LABELS["kadry"], 80.0, 20.0, 60.0],
        ])

    def test_excel_export_requires_selected_company(self):
        self.page.mode_combo.setCurrentText(PROFIT_MODE)
        with mock.patch.object(QFileDialog, "getSaveFileName") as dialog:
            self.page._export_to_excel()
        dialog.assert_not_called()
        QMessageBox.warning.assert_called()


if __name__ == "__main__":
    unittest.main()
