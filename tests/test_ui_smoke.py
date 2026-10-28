import os
import traceback
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.db.dao_calendar import save_event
from app.db.dao_companies import set_pelnomocnictwa
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(["biuro-tests"])

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

    def test_company_form_full_save_succeeds(self):
        from unittest import mock
        from app.companies.company_form import CompanyForm
        from app.db.dao_companies import save_contact
        save_contact(self.firma, {"imie": "Jan", "nazwisko": "K", "email": "jan@x.pl"})
        form = CompanyForm(self.firma)
        form.inputs["pesel"].setText("44051401359")
        with mock.patch.object(form, "accept") as accept, mock.patch("app.companies.company_form.logger.exception", side_effect=lambda *a, **k: traceback.print_exc()):
            form._on_save()
        self.assertTrue(accept.called)

    def test_ceidg_addresses_survive_save_and_reopening_company_form(self):
        from app.companies.company_form import CompanyForm
        from app.companies.company_logic import ceidg_apply
        from app.db.dao_companies import get_company

        form = CompanyForm(self.firma)
        form.inputs["pesel"].setText("44051401359")
        addresses = {
            "dz": {
                "miasto": "Szczecin", "kod": "70-001", "ulica": "Kwiatowa",
                "budynek": "4", "lokal": "12", "wojewodztwo": "zachodniopomorskie",
                "powiat": "Szczecin", "gmina": "Szczecin", "kraj": "Polska",
            },
            "ko": {
                "miasto": "Police", "kod": "72-010", "ulica": "Szkolna",
                "budynek": "2", "lokal": "3", "wojewodztwo": "zachodniopomorskie",
                "powiat": "policki", "gmina": "Police", "kraj": "Polska",
            },
            "zam": {
                "miasto": "Gryfino", "kod": "74-100", "ulica": "Ogrodowa",
                "budynek": "15", "lokal": "7", "wojewodztwo": "zachodniopomorskie",
                "powiat": "gryfinski", "gmina": "Gryfino", "kraj": "Polska",
            },
        }

        def ceidg_address(values):
            return {("kodPocztowy" if key == "kod" else key): value
                    for key, value in values.items()}

        ceidg_apply(form, {
            "nazwa": "Alfa",
            "adresDzialalnosci": ceidg_address(addresses["dz"]),
            "adresKorespondencyjny": ceidg_address(addresses["ko"]),
        })
        # Adres zamieszkania jest edytowalny w formularzu, ale nie jest
        # obecnie automatycznie wypełniany w ceidg_apply.
        for key, value in addresses["zam"].items():
            form.inputs[f"zam_{key}"].setText(value)

        form._on_save()

        def database_field(prefix, field):
            if prefix == "dz":
                return {"kod": "kod_pocztowy", "budynek": "adres_budynek",
                        "lokal": "adres_lokal"}.get(field, field)
            suffix = "kod_pocztowy" if field == "kod" else field
            return f"adres_{'koresp' if prefix == 'ko' else 'zam'}_{suffix}"

        def assert_addresses_in_database_and_form(company_form):
            stored = get_company(self.firma)
            for prefix, values in addresses.items():
                for key, expected in values.items():
                    with self.subTest(address=prefix, field=key):
                        db_key = database_field(prefix, key)
                        self.assertEqual(stored[db_key], expected)
                        self.assertEqual(
                            company_form.inputs[f"{prefix}_{key}"].text(), expected
                        )

        # Wszystkie 9 pól każdego z 3 adresów w bazie i po ponownym otwarciu.
        reopened = CompanyForm(self.firma)
        assert_addresses_in_database_and_form(reopened)

        # Ponowny zapis bez zmian nie może wyczyścić żadnego z 27 pól.
        reopened._on_save()
        reopened_again = CompanyForm(self.firma)
        assert_addresses_in_database_and_form(reopened_again)

    def test_month_picker_switches_year_and_month(self):
        from app.monthly_view.month_picker import MonthPicker
        from app.reports.import_dao import _prev_month
        picker = MonthPicker("2026-10")
        got = []
        picker.monthChanged.connect(got.append)
        picker._shift_year(-1)
        picker._choose(8)
        self.assertEqual((picker.currentText(), got), ("2025-08", ["2025-08"]))
        self.assertEqual((_prev_month("2026-09"), _prev_month("2026-01")), ("2026-08", "2025-12"))

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

    def test_monthly_tabs_split_by_role_and_activity_sum(self):
        from app.db.dao_companies import save_company
        from app.monthly_view.monthly_view_ui import MonthlyViewPage
        kadrowa = self.make_employee("kadry@example.com", "Ola", "Kadrowa")
        save_company({"nazwa": "Tylko Kadry", "kadrowa_id": kadrowa})
        save_company({"nazwa": "Tylko Ksiegowosc", "ksiegowa_id": kadrowa})
        page = MonthlyViewPage(kadrowa)
        idx = page.employee_combo.findData(kadrowa)
        page.employee_combo.setCurrentIndex(idx)
        page._reload_rows()
        self.assertEqual([page.model_kadry.get_row(i)["nazwa"] for i in range(page.model_kadry.rowCount())],
                         ["Tylko Kadry"])
        self.assertEqual([page.model_ks.get_row(i)["nazwa"] for i in range(page.model_ks.rowCount())],
                         ["Tylko Ksiegowosc"])
        self.assertTrue(page.activity_label.text().startswith("Aktywność pracownika:"))

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

