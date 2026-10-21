import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.db.dao_calendar import save_event
from app.db.dao_companies import set_pelnomocnictwa
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication([])

ENGLISH_MARKERS = {"Dashboard", "Save", "Cancel", "Delete", "Settings", "Password", "Login"}


def visible_texts(widget):
    from PyQt6.QtWidgets import QAbstractButton, QGroupBox, QLabel, QTabWidget
    texts = []
    for child in widget.findChildren(QAbstractButton):
        texts.append(child.text())
    for child in widget.findChildren(QLabel):
        texts.append(child.text())
    for child in widget.findChildren(QGroupBox):
        texts.append(child.title())
    for tabs in widget.findChildren(QTabWidget):
        texts.extend(tabs.tabText(i) for i in range(tabs.count()))
    return [t.replace("&", "").strip() for t in texts if t]


class UiSmokeTests(DbTestCase):
    def setUp(self):
        super().setUp()
        from unittest import mock
        from PyQt6.QtWidgets import QMessageBox
        for name in ("warning", "information", "critical", "question"):
            patcher = mock.patch.object(QMessageBox, name, return_value=QMessageBox.StandardButton.Yes)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.emp = self.make_employee()
        self.firma = self.make_company("Alfa")

    def test_dashboard_shows_live_data(self):
        from app.dashboard.dashboard_ui import DashboardPage
        page = DashboardPage(self.emp)
        self.assertEqual(page.cards["firmy_aktywne"].text(), "2")
        self.make_company("Beta")
        page.refresh()
        self.assertEqual(page.cards["firmy_aktywne"].text(), "3")
        self.assertFalse(ENGLISH_MARKERS & set(visible_texts(page)))

    def test_backup_box_only_for_admin(self):
        from app.dashboard.dashboard_ui import DashboardPage
        admin = DashboardPage(1)
        user = DashboardPage(self.emp)
        self.assertTrue(admin.is_admin)
        self.assertFalse(admin.backup_box.isHidden())
        self.assertFalse(user.is_admin)
        self.assertTrue(user.backup_box.isHidden())

    def test_calendar_page_and_event_dialog(self):
        from PyQt6.QtCore import QDate
        from app.kalendarz.calendar_ui import CalendarPage, EventDialog
        other = self.make_employee("o@x.pl", "Olga", "Inna")
        page = CalendarPage(self.emp)
        dlg = EventDialog(self.emp, QDate(2026, 3, 5))
        names = [dlg.access.item(i).text() for i in range(dlg.access.count())]
        self.assertIn("Olga Inna", names)
        self.assertNotIn("Jan Kowalski", names)
        row = names.index("Olga Inna")
        dlg.access.item(row).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dlg.access_ids(), [other])
        self.assertFalse(ENGLISH_MARKERS & set(visible_texts(page)))

    def test_company_form_partner_delete_works(self):
        from app.companies.company_form import CompanyForm
        from app.db.dao_companies import get_partners
        form = CompanyForm(self.firma)
        form.partner_imie1.setText("Jan")
        form.partner_nazwisko.setText("Nowak")
        form.partner_procent.setValue(50)
        form._add_partner()
        self.assertEqual(len(get_partners(self.firma)), 1)
        form.partners_table.selectRow(0)
        form._delete_partner()
        self.assertEqual(get_partners(self.firma), [])

    def test_company_form_saves_pelnomocnictwa_table(self):
        from app.companies.company_form import CompanyForm
        from app.db.dao_companies import get_pelnomocnictwa
        form = CompanyForm(self.firma)
        rows = [form.pel_table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(form.pel_table.rowCount())]
        row = rows.index(self.emp)
        form.pel_table.item(row, 1).setCheckState(Qt.CheckState.Checked)
        form._save_pelnomocnictwa()
        self.assertEqual(get_pelnomocnictwa(self.firma), {"PEL": [self.emp], "UPL": []})

    def test_monthly_view_has_delivered_column(self):
        from app.monthly_view.monthly_view_ui import MonthlyViewPage
        page = MonthlyViewPage(self.emp)
        self.assertTrue(any("Komplet" in t for t in visible_headers(page)))

    def test_main_window_sidebar_is_polish(self):
        from app.main_ui import OfficeManagerApp
        win = OfficeManagerApp(self.emp)
        items = [win.sidebar.item(i).text() for i in range(win.sidebar.count())]
        self.assertIn("Pulpit", items)
        self.assertIn("Kalendarz", items)
        self.assertNotIn("Dashboard", items)

    def test_polish_translations_load(self):
        from app.utils.i18n import install_polish_translations
        self.assertTrue(install_polish_translations(_app))


def visible_headers(page):
    from PyQt6.QtWidgets import QTableView
    headers = []
    for table in page.findChildren(QTableView):
        model = table.model()
        if model is None:
            continue
        for col in range(model.columnCount()):
            headers.append(str(model.headerData(col, Qt.Orientation.Horizontal) or ""))
    return headers


if __name__ == "__main__":
    unittest.main()

