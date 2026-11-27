"""Default task view is the logged-in employee's active work."""
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication
from app.db.dao_tasks import list_tasks, save_task
from app.tasks.tasks_ui import TasksPage
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(['task-defaults'])


class TaskDefaultFiltersTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.other = self.make_employee('other-tasks@example.com')
        self.mine = {}
        for status in ('Nowe', 'W toku', 'Zakończone', 'Anulowane'):
            self.mine[status] = save_task('Mine '+status, status=status, pracownik_id=1, actor_id=1)
        save_task('Other new', pracownik_id=self.other, actor_id=1)
        save_task('Unassigned', actor_id=1)

    def titles(self, page):
        return {page.table.item(row, 0).text() for row in range(page.table.rowCount())}

    def test_admin_also_starts_with_only_own_new_and_in_progress_tasks(self):
        page = TasksPage(1)
        self.addCleanup(page.close)
        self.assertEqual(page.assignee_filter.currentData(), 1)
        self.assertEqual(self.titles(page), {'Mine Nowe', 'Mine W toku'})
        page.status_filter.setCurrentIndex(page.status_filter.findData(None))
        self.assertEqual(self.titles(page), {'Mine '+status for status in self.mine})
        page.assignee_filter.setCurrentIndex(page.assignee_filter.findData(self.other))
        self.assertEqual(self.titles(page), {'Other new'})

    def test_another_employee_starts_with_their_own_active_tasks(self):
        page = TasksPage(self.other)
        self.addCleanup(page.close)
        self.assertEqual(page.assignee_filter.currentData(), self.other)
        self.assertEqual(self.titles(page), {'Other new'})

    def test_notification_can_still_open_completed_task(self):
        page = TasksPage(1)
        self.addCleanup(page.close)
        self.assertTrue(page.show_assignment_notification(self.mine['Zakończone']))
        self.assertEqual(page._selected_id(), self.mine['Zakończone'])

    def test_navigation_loads_tasks_before_filters_and_defers_activities(self):
        with patch('app.tasks.tasks_ui.list_companies', return_value=[]) as companies, \
                patch('app.db.dao_services.list_extra_activities', return_value=[]) as activities:
            page = TasksPage(1, defer_load=True)
            self.addCleanup(page.close)
            with patch.object(page._task_reads, 'submit') as tasks, \
                    patch.object(page._reference_reads, 'submit') as references:
                page.refresh_async()
                companies.assert_not_called()
                read_tasks, render_tasks, _ = tasks.call_args.args
                render_tasks(read_tasks())
                self.assertEqual(self.titles(page), {'Mine Nowe', 'Mine W toku'})
                read_references, render_references, _ = references.call_args.args
                render_references(read_references())
            self.assertIsNone(page.extra_activities)
            activities.assert_not_called()
            companies.assert_called_once_with(1)
            self.assertTrue(page.show_assignment_notification(self.mine['Zakończone']))

    def test_empty_status_selection_never_returns_all_tasks(self):
        self.assertEqual(list_tasks(status=[], viewer_id=1), [])
