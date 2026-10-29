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
        self.assertEqual(page.table.item(0, 4).text(), "Do wykonania")
        self.assertEqual(page.table.item(0, 5).text(), "Wniosek do urzędu")
        page.table.selectRow(0)
        task_id = page.rows[0]["zadanie_id"]
        page._complete()
        self.assertEqual(page.table.item(0, 4).text(), "Wykonane")
        self.assertEqual(next(task for task in list_tasks(viewer_id=1) if task["id"] == task_id)["status"], "Zakończone")

    def test_cancel_does_not_create_activity_or_task(self):
        count_before = len(list_tasks(viewer_id=1))
        self.dialog().reject()
        self.assertEqual(dao_services.list_extra_activities(1, miesiac="2026-09"), [])
        self.assertEqual(len(list_tasks(viewer_id=1)), count_before)

    def test_state_and_month_filters_show_only_requested_activities(self):
        self.create_activity("Nowe")
        self.create_activity("Zakończone")
        page = self.page()
        page.state_filter.setCurrentIndex(page.state_filter.findData("done"))
        self.assertEqual(page.table.rowCount(), 1)
        self.assertEqual(page.table.item(0, 4).text(), "Wykonane")
        page.month.setCurrentText("2026-10")
        page.refresh()
        self.assertEqual(page.table.rowCount(), 0)

    def test_existing_unlinked_activity_offers_only_same_client_tasks(self):
        dao_services.add_company_extra_service(self.company_id, "2026-09", self.service_id, 1, 100, True)
        right_task = save_task("Właściwe zadanie", firma_id=self.company_id, created_by=1, actor_id=1)
        other_task = save_task("Inny klient", firma_id=self.other_company_id, created_by=1, actor_id=1)
        page = self.page()
        page.table.selectRow(0)
        self.assertEqual(page.table.item(0, 4).text(), "Bez zadania")

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


if __name__ == "__main__":
    unittest.main()
