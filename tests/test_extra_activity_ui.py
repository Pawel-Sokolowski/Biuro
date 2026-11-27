"""Praca z czynnościami dodatkowymi i powiązanymi zadaniami w GUI."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from app.db.dao_companies import list_companies
from app.db.dao_conn import get_conn
from app.db.dao_employees import list_employees
from app.db import dao_services
from app.db.dao_tasks import list_tasks, save_task
from app.tasks.extra_activities_ui import ExtraActivitiesPage, ExtraActivityDialog
from tests.base import DbTestCase


class ExtraActivityUiTests(DbTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["extra-activity-ui-tests"])

    def setUp(self):
        super().setUp()
        self.company_id = self.make_company("Klient czynności")
        self.other_company_id = self.make_company("Inny klient")
        conn = get_conn()
        try:
            self.service_id = conn.execute(
                "INSERT INTO uslugi_dodatkowe (nazwa,cena_netto,stawka_vat) VALUES ('Wniosek dodatkowy',100,'23%') RETURNING id"
            ).fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        self.companies = list_companies(1)
        self.employees = list_employees()

    def dialog(self):
        dialog = ExtraActivityDialog(1, self.companies, self.employees, "2026-09", self.company_id)
        dialog.service.setCurrentIndex(dialog.service.findData(self.service_id))
        self.addCleanup(dialog.close)
        return dialog

    def page(self):
        page = ExtraActivitiesPage(1, self.companies, self.employees)
        page.month.setCurrentText("2026-09")
        page.refresh()
        self.addCleanup(page.close)
        return page

    def create_activity(self, status="Nowe"):
        return dao_services.create_extra_activity(
            self.company_id, "2026-09", self.service_id, 1, 100, True,
            actor_id=1, task_title="Wniosek do urzędu", task_employee_id=1, task_status=status,
        )

    def test_creating_activity_saves_linked_task_and_completion_updates_list(self):
        dialog = self.dialog()
        dialog.task_inputs.title.setText("Wniosek do urzędu")
        dialog._save()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        page = self.page()
        self.assertEqual(page.table.rowCount(), 1)
        self.assertEqual(page.table.item(0, 5).text(), "Do wykonania")
        self.assertEqual(page.table.item(0, 6).text(), "Wniosek do urzędu")
        page.table.selectRow(0)
        task_id = page.rows[0]["zadanie_id"]
        page._complete()
        self.assertEqual(page.table.item(0, 5).text(), "Wykonane")
        self.assertEqual(next(task for task in list_tasks(viewer_id=1) if task["id"] == task_id)["status"], "Zakończone")

    def test_cancel_does_not_create_activity_or_task(self):
        count_before = len(list_tasks(viewer_id=1))
        self.dialog().reject()
        self.assertEqual(dao_services.list_extra_activities(1, miesiac="2026-09"), [])
        self.assertEqual(len(list_tasks(viewer_id=1)), count_before)

    def test_payroll_monthly_dialog_records_billable_service_without_invoice_access(self):
        from app.db.dao_global_settings import add_role, assign_role, set_role_tabs
        from app.monthly_view.monthly_view_ui import InlineExtraServiceWindow

        employee = self.make_employee('kadry@example.test')
        role = add_role('Kadry bez faktur', can_view_invoices=False)
        assign_role(employee, role)
        set_role_tabs(role, ['Widok miesięczny'])
        dialog = InlineExtraServiceWindow(self.company_id, '2026-09', employee)
        self.addCleanup(dialog.close)
        dialog.cb_usluga.setCurrentIndex(dialog.cb_usluga.findData(self.service_id))
        self.assertEqual(dialog.input_cena.value(), 100)
        dialog.input_ilosc.setValue(1.5)
        dialog.cb_jednostka.setCurrentText('usługa')
        dialog.chk_fakturowac.setChecked(True)
        with patch.object(QMessageBox, 'information') as saved, patch.object(QMessageBox, 'warning') as warning:
            dialog._on_add()
        warning.assert_not_called()
        saved.assert_called_once()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        with get_conn() as conn:
            entries = conn.execute(
                'SELECT miesiac, usluga_id, ilosc, cena_netto, jednostka, fakturowac '
                'FROM uslugi_dodatkowe_firma WHERE firma_id = %s', (self.company_id,),
            ).fetchall()
            self.assertEqual([tuple(entry) for entry in entries], [('2026-09', self.service_id, 1.5, 100, 'usługa', 1)])
            self.assertIsNone(conn.execute('SELECT id FROM faktury WHERE firma_id = %s', (self.company_id,)).fetchone())

    def test_state_and_month_filters_show_only_requested_activities(self):
        self.create_activity("Nowe")
        self.create_activity("Zakończone")
        page = self.page()
        page.state_filter.setCurrentIndex(page.state_filter.findData("done"))
        self.assertEqual(page.table.rowCount(), 1)
        self.assertEqual(page.table.item(0, 5).text(), "Wykonane")
        page.month.setCurrentText("2026-10")
        page.refresh()
        self.assertEqual(page.table.rowCount(), 0)

    def test_existing_unlinked_activity_offers_only_same_client_tasks(self):
        dao_services.add_company_extra_service(self.company_id, "2026-09", self.service_id, 1, 100, True)
        right_task = save_task("Właściwe zadanie", firma_id=self.company_id, created_by=1, actor_id=1)
        other_task = save_task("Inny klient", firma_id=self.other_company_id, created_by=1, actor_id=1)
        page = self.page()
        page.table.selectRow(0)
        self.assertEqual(page.table.item(0, 5).text(), "Bez zadania")

        def choose(_parent, _title, _label, choices, *_args):
            self.assertTrue(any(f"#{right_task} —" in choice for choice in choices))
            self.assertFalse(any(f"#{other_task} —" in choice for choice in choices))
            return next(choice for choice in choices if f"#{right_task} —" in choice), True

        with patch("app.tasks.extra_activities_ui.QInputDialog.getItem", side_effect=choose):
            page._link()
        self.assertEqual(page.rows[0]["zadanie_id"], right_task)

    def test_tasks_page_opens_related_task_and_keeps_normal_tasks_tab(self):
        from app.tasks.tasks_ui import TasksPage
        self.create_activity()
        page = TasksPage(1)
        self.addCleanup(page.close)
        self.assertEqual(page.tabs.tabText(0), "Zadania")
        self.assertEqual(page.tabs.tabText(1), "Czynności dodatkowe")
        activities = page.extra_activities
        activities.month.setCurrentText("2026-09")
        activities.refresh()
        activities.table.selectRow(0)
        with patch("app.tasks.tasks_ui.TaskDialog") as task_dialog:
            task_dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            activities._open_related_task()
        self.assertEqual(task_dialog.call_args.args[4]["id"], activities.rows[0]["zadanie_id"])

    def test_permission_error_keeps_unsaved_activity_dialog_open(self):
        dialog = self.dialog()
        with patch("app.tasks.extra_activities_ui.dao_services.create_extra_activity", side_effect=PermissionError("Brak uprawnień")), \
             patch.object(QMessageBox, "warning") as warning:
            dialog._save()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertIn("Brak uprawnień", warning.call_args.args[2])
        self.assertEqual(dao_services.list_extra_activities(1, miesiac="2026-09"), [])

    def test_invoice_shortcut_has_activity_list_without_overwriting_draft_table(self):
        from app.invoices.faktury_ui import FakturyPage
        from app.invoices.faktury_logic import FakturyLogic
        from app.db.dao_invoices import mark_exported
        self.create_activity("Zakończone")
        generated = FakturyLogic().generate_and_save_invoice(self.company_id, "2026-09")
        with get_conn() as conn:
            conn.execute("UPDATE faktury SET numer = 'DOD/09/2026' WHERE id = %s", (generated['id'],))
        page = FakturyPage(1)
        self.addCleanup(page.close)
        self.assertIsNone(page.extra_activities)
        page.month_button.setCurrentText("2026-09")
        page.tabs.setCurrentWidget(page.tab_dodatkowe)
        page.extra_activities.month.setCurrentText("2026-09")
        page.extra_activities.refresh()
        page.tabs.setCurrentWidget(page.tab_dodatkowe)
        self.assertIsNot(page.draft_table, page.extra_activities.table)
        self.assertEqual(page.extra_activities.table.item(0, 10).text(), "100.00 PLN")
        self.assertEqual(page.extra_activities.table.item(0, 2).text(), "DOD/09/2026 (robocza)")
        mark_exported([generated['id']], 1)
        page.extra_activities.refresh()
        self.assertEqual(page.extra_activities.table.item(0, 2).text(), "DOD/09/2026")


if __name__ == "__main__":
    unittest.main()
