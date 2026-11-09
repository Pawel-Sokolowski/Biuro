"""Slow reads must leave Qt responsive and preserve the selected data scope."""
import threading
import unittest
from unittest import mock

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QAbstractItemView

from app.monthly_view import monthly_view_ui
from app.year_end import year_end_ui
from tests.test_review_optimizations import wait_until


_app = QApplication.instance() or QApplication(['review-responsiveness'])


def monthly_row(company_id, name):
    return {'id': company_id, 'nazwa': name, 'ksiegowa_id': 7, 'kadrowa_id': 7, 'ilosc_dok': 4}


def annual_company(company_id, name, filed=False):
    return {'id': company_id, 'nazwa': name, 'deklaracje': [
        {'kod': 'PIT-36', 'owner_key': 0, 'zlozona': filed},
    ]}


class ReadResponsivenessTests(unittest.TestCase):
    def monthly_page(self, read):
        logic = mock.Mock(aktualny_miesiac='2026-11', pracownik_id=7)
        logic.get_employee_filter_list.return_value = [(0, 'Wszyscy'), (7, 'Jan')]
        logic.get_monthly_rows.side_effect = read
        logic.format_seconds.side_effect = str
        logic.get_timer_total_seconds.side_effect = lambda company, department, seconds: seconds
        with mock.patch.object(monthly_view_ui, 'MonthlyViewLogic', return_value=logic), \
                mock.patch.object(monthly_view_ui.MonthlyViewPage, '_setup_timer'):
            page = monthly_view_ui.MonthlyViewPage(7)
        self.addCleanup(page.close)
        self.addCleanup(lambda: self.assertTrue(wait_until(lambda: not page._monthly_reads._jobs)))
        return page, logic

    def test_slow_monthly_read_does_not_block_the_gui(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        worker_ids = []
        def read(*args):
            worker_ids.append(threading.get_ident())
            started.set()
            if not release.wait(5):
                raise TimeoutError('Unreleased test read')
            return [monthly_row(1, 'Alpha')]
        page, _ = self.monthly_page(read)
        self.addCleanup(release.set)
        self.assertTrue(started.wait(2))
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        self.assertTrue(wait_until(lambda: bool(heartbeat)))
        self.assertNotEqual(worker_ids, [threading.get_ident()])
        self.assertEqual(page.model_ks.rowCount(), 0)
        release.set()
        self.assertTrue(wait_until(lambda: page.model_ks.rowCount() == 1))

    def test_changing_month_discards_a_late_previous_month_read(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def read(month, *_):
            if month == '2026-11':
                started.set()
                if not release.wait(5):
                    raise TimeoutError('Unreleased old-month read')
                return [monthly_row(1, 'Old month')]
            return [monthly_row(2, 'New month')]
        page, _ = self.monthly_page(read)
        self.addCleanup(release.set)
        self.assertTrue(started.wait(2))
        page.month_combo._choose(12)
        self.assertTrue(wait_until(lambda: page._loaded_context == ('2026-12', 7, 'Aktywne')))
        release.set()
        self.assertTrue(wait_until(lambda: not page._monthly_reads._jobs))
        self.assertEqual(page.model_ks.get_row(0)['nazwa'], 'New month')

    def test_monthly_stage_filter_uses_cached_rows_and_preserves_selection_on_refresh(self):
        rows = [monthly_row(1, 'Alpha'), monthly_row(2, 'Beta')]
        page, logic = self.monthly_page(lambda *_: [dict(r) for r in rows])
        self.assertTrue(wait_until(lambda: page.model_ks.rowCount() == 2))
        page.table_ks.selectRow(1)
        before = logic.get_monthly_rows.call_count
        page.stage_combo.setCurrentText('Brak')
        self.assertEqual(logic.get_monthly_rows.call_count, before)
        rows.insert(0, monthly_row(3, 'Added'))
        page.refresh_background()
        self.assertTrue(wait_until(lambda: page.model_ks.rowCount() == 3))
        self.assertEqual(page.model_ks.get_row(page.table_ks.currentIndex().row())['id'], 2)

    def test_monthly_result_waits_for_an_active_editor(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def read(*args):
            started.set()
            if not release.wait(5):
                raise TimeoutError('Unreleased editing read')
            return [monthly_row(1, 'Alpha')]
        page, _ = self.monthly_page(read)
        self.addCleanup(release.set)
        self.assertTrue(started.wait(2))
        with mock.patch.object(page.table_ks, 'state', return_value=QAbstractItemView.State.EditingState):
            release.set()
            self.assertTrue(wait_until(lambda: getattr(page, '_background_refresh_timer', None) is not None))
            self.assertEqual(page.model_ks.rowCount(), 0)
        page.refresh_background()
        self.assertTrue(wait_until(lambda: page.model_ks.rowCount() == 1))

    def test_year_end_filters_use_cached_rows_and_keep_selected_company_identity(self):
        data = [annual_company(1, 'Alpha'), annual_company(2, 'Beta', filed=True)]
        with mock.patch.object(year_end_ui, 'list_year_end', return_value=data) as read:
            page = year_end_ui.YearEndPage(7)
            self.addCleanup(page.close)
            self.assertTrue(wait_until(lambda: page.table.rowCount() == 2))
            page.table.selectRow(1)
            page.search.setText('Beta')
            self.assertEqual(page.table.rowCount(), 1)
            self.assertEqual(page._selected()[0]['id'], 2)
            page.search.clear()
            self.assertEqual(page._selected()[0]['id'], 2)
            page.only_open.setChecked(True)
            self.assertEqual(page.table.rowCount(), 1)
            self.assertIsNone(page._selected())
            self.assertEqual(read.call_count, 1)

    def test_year_end_slow_old_year_cannot_replace_new_year(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        initial_year = year_end_ui.date.today().year - 1
        worker_ids = []
        def read(year):
            worker_ids.append(threading.get_ident())
            if year == initial_year:
                started.set()
                if not release.wait(5):
                    raise TimeoutError('Unreleased old-year read')
                return [annual_company(1, 'Old year')]
            return [annual_company(2, 'New year')]
        with mock.patch.object(year_end_ui, 'list_year_end', side_effect=read):
            page = year_end_ui.YearEndPage(7)
            self.addCleanup(page.close)
            self.assertTrue(started.wait(2))
            page.year.setValue(initial_year + 1)
            self.assertTrue(wait_until(lambda: page._cache_year == initial_year + 1))
            release.set()
            self.assertTrue(wait_until(lambda: not page._reads._jobs))
        self.assertEqual(page.table.item(0, 0).text(), 'New year')
        self.assertNotIn(threading.get_ident(), worker_ids)

    def test_embedded_year_end_does_not_load_until_opened(self):
        with mock.patch.object(year_end_ui, 'list_year_end', return_value=[]) as read:
            page = year_end_ui.YearEndPage(7, embedded=True)
            self.addCleanup(page.close)
            _app.processEvents()
            read.assert_not_called()
            page.set_year(2026)
            self.assertTrue(wait_until(lambda: page._cache_year == 2026))
            read.assert_called_once_with(2026)
