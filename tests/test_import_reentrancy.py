"""Import workflows stay exclusive across modal dialogs and worker handoff."""
import threading
from unittest import mock

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from app.reports import import_dao
from app.reports.reports_ui import ReportsPage
from tests.base import DbTestCase
from tests.test_review_optimizations import wait_until


RESULT = {"wiersze": 1, "dodane": 1, "pominiete": 0, "bez_firmy": 0}


class ImportReentrancyTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.page = ReportsPage(1)
        self.addCleanup(self.page.deleteLater)
        for patcher in (
            mock.patch.object(self.page, "_can_import", return_value=True),
            *(mock.patch.object(QMessageBox, name, return_value=QMessageBox.StandardButton.Yes)
              for name in ("warning", "information", "critical", "question")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_file_dialog_reentry_cannot_submit_a_second_import(self):
        choices, busy = [], []

        def choose(*args):
            index = len(choices)
            choices.append(index)
            busy.append(self.page._import_busy)
            if index == 0:
                self.page._run_import("czas pracy")
            return f"work-{index}.xlsx", ""

        with mock.patch.object(QFileDialog, "getOpenFileName", side_effect=choose), \
                mock.patch.object(self.page._import_jobs, "submit") as submit:
            self.page._run_import("czas pracy")
        self.assertEqual(choices, [0])
        self.assertEqual(busy, [True])
        submit.assert_called_once()
        self.assertTrue(self.page._import_busy)

    def test_month_dialog_reentry_cannot_replace_the_invoice_import(self):
        busy = []

        def month(*args):
            busy.append(self.page._import_busy)
            self.page._run_import("czas pracy")
            return "2026-01", True

        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("invoices.xlsx", "")) as choose, \
                mock.patch.object(QInputDialog, "getItem", side_effect=month), \
                mock.patch.object(self.page._import_jobs, "submit") as submit, \
                mock.patch.object(import_dao, "import_invoices_file", return_value=RESULT) as invoices:
            self.page._run_import("faktury")
            work, _, _ = submit.call_args.args
            work()
        self.assertEqual(busy, [True])
        choose.assert_called_once()
        submit.assert_called_once()
        invoices.assert_called_once_with("invoices.xlsx", "2026-01", self.page.chk_overwrite.isChecked())

    def test_cancelled_file_dialog_releases_the_workflow_for_retry(self):
        with mock.patch.object(QFileDialog, "getOpenFileName", side_effect=[("", ""), ("work.xlsx", "")]), \
                mock.patch.object(self.page._import_jobs, "submit") as submit:
            self.page._run_import("czas pracy")
            self.assertFalse(self.page._import_busy)
            submit.assert_not_called()
            self.page._run_import("czas pracy")
            submit.assert_called_once()
            self.assertTrue(self.page._import_busy)

    def test_cancelled_month_dialog_releases_the_workflow_for_retry(self):
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("file.xlsx", "")), \
                mock.patch.object(QInputDialog, "getItem", return_value=("", False)), \
                mock.patch.object(self.page._import_jobs, "submit") as submit:
            self.page._run_import("faktury")
            self.assertFalse(self.page._import_busy)
            submit.assert_not_called()
            self.page._run_import("czas pracy")
            submit.assert_called_once()

    def test_file_dialog_error_releases_the_workflow_and_reports_failure(self):
        with mock.patch.object(QFileDialog, "getOpenFileName", side_effect=RuntimeError("chooser failed")), \
                mock.patch.object(self.page._import_jobs, "submit") as submit, \
                mock.patch.object(QMessageBox, "critical") as error:
            self.page._run_import("czas pracy")
        self.assertFalse(self.page._import_busy)
        submit.assert_not_called()
        error.assert_called_once()

    def test_month_dialog_error_releases_the_workflow_and_reports_failure(self):
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("invoices.xlsx", "")), \
                mock.patch.object(QInputDialog, "getItem", side_effect=RuntimeError("month failed")), \
                mock.patch.object(self.page._import_jobs, "submit") as submit, \
                mock.patch.object(QMessageBox, "critical") as error:
            self.page._run_import("faktury")
        self.assertFalse(self.page._import_busy)
        submit.assert_not_called()
        error.assert_called_once()

    def test_submission_error_keeps_the_error_dialog_exclusive_and_allows_retry(self):
        busy = []

        def error(*args):
            busy.append(self.page._import_busy)
            if len(busy) == 1:
                self.page._run_import("czas pracy")

        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("work.xlsx", "")) as choose, \
                mock.patch.object(QMessageBox, "critical", side_effect=error), \
                mock.patch.object(self.page._import_jobs, "submit", side_effect=RuntimeError("submit failed")) as submit:
            self.page._run_import("czas pracy")
            self.assertEqual(busy, [True])
            self.assertFalse(self.page._import_busy)
            choose.assert_called_once()
            submit.assert_called_once()
            submit.side_effect = None
            self.page._run_import("czas pracy")
            self.assertTrue(self.page._import_busy)
            self.assertEqual(submit.call_count, 2)

    def check_notification_exclusive(self, failed):
        self.page._import_busy = True
        busy = []

        def notify(*args):
            busy.append(self.page._import_busy)
            self.page._run_import("czas pracy")

        name = "critical" if failed else "information"
        with mock.patch.object(QMessageBox, name, side_effect=notify), \
                mock.patch.object(QFileDialog, "getOpenFileName", return_value=("work.xlsx", "")) as choose, \
                mock.patch.object(self.page._import_jobs, "submit") as submit:
            if failed:
                self.page._import_failed(RuntimeError("worker failed"))
            else:
                self.page._import_finished(RESULT)
        self.assertEqual(busy, [True])
        choose.assert_not_called()
        submit.assert_not_called()
        self.assertFalse(self.page._import_busy)

    def test_success_notification_keeps_the_workflow_reserved_until_dismissed(self):
        self.check_notification_exclusive(False)

    def test_failure_notification_keeps_the_workflow_reserved_until_dismissed(self):
        self.check_notification_exclusive(True)

    def test_notification_errors_do_not_leave_the_workflow_reserved(self):
        for failed in (False, True):
            self.page._import_busy = True
            name = "critical" if failed else "information"
            with mock.patch.object(QMessageBox, name, side_effect=RuntimeError("dialog failed")), \
                    self.assertRaises(RuntimeError):
                if failed:
                    self.page._import_failed(RuntimeError("worker failed"))
                else:
                    self.page._import_finished(RESULT)
            self.assertFalse(self.page._import_busy)

    def test_deletion_is_blocked_while_the_import_file_dialog_is_open(self):
        def choose(*args):
            self.page._delete_imported()
            return "", ""

        with mock.patch.object(QFileDialog, "getOpenFileName", side_effect=choose), \
                mock.patch.object(QInputDialog, "getItem", return_value=("Faktury", False)) as delete_dialog, \
                mock.patch.object(import_dao, "delete_imported") as delete:
            self.page._run_import("czas pracy")
        delete_dialog.assert_not_called()
        delete.assert_not_called()
        self.assertFalse(self.page._import_busy)

    def test_nested_dialog_events_start_and_complete_exactly_one_real_worker(self):
        started, release = threading.Event(), threading.Event()
        events, choices = [], []

        def nested_import():
            self.page._run_import("czas pracy")
            events.append(True)

        def choose(*args):
            index = len(choices)
            choices.append(index)
            if index == 0:
                QTimer.singleShot(0, nested_import)
                self.assertTrue(wait_until(lambda: events))
            return ("outer.xlsx" if index == 0 else "nested.xlsx"), ""

        def work(*args):
            started.set()
            if not release.wait(3):
                raise TimeoutError("test did not release the import worker")
            return RESULT

        with mock.patch.object(QFileDialog, "getOpenFileName", side_effect=choose), \
                mock.patch.object(import_dao, "import_work_file", side_effect=work) as imported:
            try:
                self.page._run_import("czas pracy")
                self.assertTrue(wait_until(started.is_set))
                self.page._run_import("czas pracy")
            finally:
                release.set()
                self.assertTrue(wait_until(lambda: not self.page._import_jobs._jobs))
        self.assertEqual(choices, [0])
        imported.assert_called_once_with("outer.xlsx")
        self.assertFalse(self.page._import_busy)
