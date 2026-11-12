from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QDialog
from app.main_ui import OfficeManagerApp
from app.monthly_view.monthly_view_logic import MonthlyViewLogic


class HeaderTimerActionTests(TestCase):
    def window(self):
        logic = MagicMock()
        logic.is_company_timer_running.return_value = True
        logic.is_general_timer_running.return_value = False
        page = SimpleNamespace(logic=logic, _reload_rows=MagicMock())
        return SimpleNamespace(page_monthly=page, pracownik_id=5, _update_header_timer=MagicMock()), logic

    def test_company_timer_is_stopped_without_opening_general_timer_dialog(self):
        for department in ('ksiegowosc', 'kadry'):
            window, logic = self.window()
            with self.subTest(department=department), patch('app.monthly_view.general_timer_dialog.GeneralTimerDialog') as dialog:
                OfficeManagerApp._toggle_general_timer(window)
                logic.stop_company_timers.assert_called_once()
                logic.start_general_timer.assert_not_called()
                logic.stop_general_timer.assert_not_called()
                dialog.assert_not_called()
                window.page_monthly._reload_rows.assert_called_once()

    def test_only_the_next_click_can_start_a_general_timer(self):
        window, logic = self.window()
        logic.is_company_timer_running.side_effect = [True, False]
        with patch('app.monthly_view.general_timer_dialog.GeneralTimerDialog') as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.activity.return_value = {'task_title': 'Ogólne'}
            OfficeManagerApp._toggle_general_timer(window)
            dialog.assert_not_called()
            OfficeManagerApp._toggle_general_timer(window)
            logic.start_general_timer.assert_called_once_with({'task_title': 'Ogólne'})
            dialog.assert_called_once()

    def test_save_failure_never_starts_another_timer(self):
        window, logic = self.window()
        logic.stop_company_timers.side_effect = RuntimeError('save failed')
        with patch('app.main_ui.QMessageBox.warning') as warning, \
             patch('app.monthly_view.general_timer_dialog.GeneralTimerDialog') as dialog:
            OfficeManagerApp._toggle_general_timer(window)
            warning.assert_called_once()
            dialog.assert_not_called()
            logic.start_general_timer.assert_not_called()

    def test_running_general_timer_still_stops_normally(self):
        window, logic = self.window()
        logic.is_company_timer_running.return_value = False
        logic.is_general_timer_running.return_value = True
        OfficeManagerApp._toggle_general_timer(window)
        logic.stop_general_timer.assert_called_once()
        logic.start_general_timer.assert_not_called()

    def test_company_running_state_requires_both_company_and_start_time(self):
        logic = MonthlyViewLogic.__new__(MonthlyViewLogic)
        for fields in logic._TIMER_FIELDS.values():
            setattr(logic, fields[0], None)
            setattr(logic, fields[1], None)
        self.assertFalse(logic.is_company_timer_running())
        for fields in logic._TIMER_FIELDS.values():
            setattr(logic, fields[0], 1)
            self.assertFalse(logic.is_company_timer_running())
            setattr(logic, fields[1], datetime(2026, 11, 12))
            self.assertTrue(logic.is_company_timer_running())
            setattr(logic, fields[0], None)
            setattr(logic, fields[1], None)
