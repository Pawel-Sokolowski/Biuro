from unittest import TestCase
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QWidget, QTabWidget, QVBoxLayout, QStackedWidget
from app.accounting.department_ui import DepartmentPage, navigation_tabs, department_sections, park_monthly_editor
from app.accounting.accounting_ui import AccountingPage
from app.monthly_view.monthly_view_ui import MonthlyViewPage
from app.integrations.ezla.report import Report, Certificate

_app = QApplication.instance() or QApplication(['department-navigation'])


class MonthlyEditor(QWidget):
    # Use the real department-switching method without a second database/timer instance.
    set_department = MonthlyViewPage.set_department

    def __init__(self):
        super().__init__()
        self.logic = object()
        self.tabs = QTabWidget(self)
        self.tabs.addTab(QWidget(), 'Księgowość')
        self.tabs.addTab(QWidget(), 'Kadry')
        self._reload_rows = MagicMock()
        self.tabs.currentChanged.connect(self._reload_rows)


class DepartmentNavigationTests(TestCase):
    def test_shared_monthly_editor_never_overlays_dashboard_or_other_pages(self):
        shell = QWidget()
        layout = QVBoxLayout(shell)
        stack = QStackedWidget()
        layout.addWidget(stack)
        editor = MonthlyEditor()
        parking = park_monthly_editor(editor, shell)
        checking = QWidget()
        checking.refresh_async = MagicMock()
        with patch('app.accounting.department_ui.allowed_tabs', return_value=['Widok miesięczny', 'Sprawdzanie']):
            accounting = DepartmentPage(5, 'accounting', editor, checking_factory=lambda *_: checking)
            payroll = DepartmentPage(5, 'payroll', editor)
            for page in (QWidget(), accounting, payroll, QWidget()):
                stack.addWidget(page)
            stack.currentChanged.connect(lambda index: getattr(stack.widget(index), 'refresh_async', lambda: None)())
            try:
                shell.show()
                _app.processEvents()
                self.assertIs(editor.parentWidget(), parking)
                self.assertTrue(parking.isHidden())
                self.assertFalse(editor.isVisible())
                stack.setCurrentIndex(1)
                self.assertIs(editor.parentWidget(), accounting.tabs.widget(0))
                self.assertTrue(editor.isVisible())
                self.assertTrue(editor.tabs.tabBar().isHidden())
                accounting.tabs.setCurrentIndex(1)
                self.assertFalse(editor.isVisible())
                stack.setCurrentIndex(2)
                self.assertIs(editor.parentWidget(), payroll.tabs.widget(0))
                self.assertEqual(editor.tabs.currentIndex(), 1)
                self.assertTrue(editor.isVisible())
                stack.setCurrentIndex(3)  # Tasks/calendar/chat must not display the monthly editor.
                self.assertFalse(editor.isVisible())
                stack.setCurrentIndex(0)
                self.assertFalse(editor.isVisible())
                stack.setCurrentIndex(1)
                self.assertFalse(editor.isVisible())  # Accounting still has its Checking subtab selected.
                accounting.tabs.setCurrentIndex(0)
                self.assertIs(editor.parentWidget(), accounting.tabs.widget(0))
                self.assertEqual(editor.tabs.currentIndex(), 0)
                self.assertTrue(editor.isVisible())
            finally:
                shell.close()

    def test_monthly_editor_and_timer_logic_are_shared_when_changing_departments(self):
        editor = MonthlyEditor()
        logic = editor.logic
        with patch('app.accounting.department_ui.allowed_tabs', return_value=['Widok miesięczny', 'Sprawdzanie']):
            accounting = DepartmentPage(5, 'accounting', editor)
            payroll = DepartmentPage(5, 'payroll', editor)
            try:
                accounting.refresh_async()
                self.assertEqual(editor.tabs.currentIndex(), 0)
                self.assertIs(editor.parentWidget(), accounting.tabs.widget(0))
                payroll.refresh_async()
                self.assertEqual(editor.tabs.currentIndex(), 1)
                self.assertIs(editor.parentWidget(), payroll.tabs.widget(0))
                self.assertTrue(editor.tabs.tabBar().isHidden())
                accounting.refresh_async()
                self.assertEqual(editor.tabs.currentIndex(), 0)
                self.assertIs(editor.logic, logic)
                self.assertEqual(accounting.tabs.tabText(0), 'Widok miesięczny')
                self.assertEqual(accounting.tabs.tabText(1), 'Sprawdzanie')
            finally:
                accounting.close()
                payroll.close()
                editor.close()

    def test_checking_is_lazy_and_preserves_the_report_page_on_return(self):
        checking = QWidget()
        checking.refresh_async = MagicMock()
        factory = MagicMock(return_value=checking)
        editor = MonthlyEditor()
        with patch('app.accounting.department_ui.allowed_tabs', return_value=['Widok miesięczny', 'Sprawdzanie']):
            page = DepartmentPage(5, 'payroll', editor, checking_factory=factory)
            try:
                page.refresh_async()
                factory.assert_not_called()
                page.tabs.setCurrentIndex(1)
                factory.assert_called_once_with(5, 'payroll')
                page.tabs.setCurrentIndex(0)
                page.tabs.setCurrentIndex(1)
                factory.assert_called_once()
                self.assertEqual(checking.refresh_async.call_count, 2)
            finally:
                page.close()
                editor.close()

    def test_legacy_module_permissions_do_not_grant_another_subtab(self):
        self.assertEqual(department_sections(['Sprawdzanie']), ['Sprawdzanie'])
        self.assertEqual(navigation_tabs(['Pulpit']), {'Pulpit'})
        self.assertEqual(navigation_tabs(['Pulpit', 'Widok miesięczny']), {'Pulpit', 'Księgowość', 'Kadry'})
        editor = MonthlyEditor()
        with patch('app.accounting.department_ui.allowed_tabs', return_value=['Sprawdzanie']):
            page = DepartmentPage(5, 'payroll', editor)
            try:
                self.assertEqual(page.tabs.count(), 1)
                self.assertEqual(page.tabs.tabText(0), 'Sprawdzanie')
                self.assertIsNone(editor.parentWidget())
            finally:
                page.close()
                editor.close()


class CheckingReportSelectionTests(TestCase):
    def setUp(self):
        self.page = AccountingPage(5, department='payroll')
        self.addCleanup(self.page.close)
        self.rows = [
            {'id': 1, 'nazwa': 'A', 'nip': '1112223344', 'history': [], 'checking': {'documents': {'payroll': {}},
             'declarations': [{'department': 'payroll', 'form': 'RCA', 'status': 'accepted', 'upo': 'REF'},
                              {'department': 'payroll', 'form': 'PIT-11', 'status': 'not_sent', 'upo': ''}]}},
            {'id': 2, 'nazwa': 'B', 'nip': '2223334455', 'history': [], 'checking': {'declarations': [
                {'department': 'payroll', 'form': 'PIT-8C', 'status': 'prepared', 'upo': ''}]}},
            {'id': 3, 'nazwa': 'C', 'nip': '3334445566', 'history': []}]
        self.page.render(self.rows, '2026-11')

    def select(self, key):
        selector = self.page.report_selectors['payroll']
        selector.setCurrentIndex(selector.findData(key))

    def test_rca_lists_every_company_without_other_form_rows(self):
        self.select('form:RCA')
        table = self.page.declaration_tables['payroll']
        self.assertEqual(table.rowCount(), 3)
        self.assertEqual(table.item(0, 3).text(), 'RCA')
        self.assertEqual(table.item(0, 6).text(), 'Tak')
        self.assertEqual(table.item(1, 4).text(), 'Brak danych')
        self.assertEqual(table.item(2, 4).text(), 'Nie sprawdzono')
        self.page.attention.setChecked(True)
        self.assertEqual(table.rowCount(), 2)  # Unsent PIT-11 must not flag the accepted RCA.
        self.assertEqual(table.item(0, 0).text(), 'B')
        self.assertNotIn('payroll', self.page.comparison_tables)
        self.assertEqual(self.page.tabs.currentIndex(), 1)
        self.assertTrue(self.page.tabs.tabBar().isHidden())
        self.page.tabs.setCurrentIndex(0)
        self.assertEqual(self.page.tabs.currentIndex(), 1)

    def test_new_report_types_appear_and_selection_survives_filter_changes(self):
        self.select('form:PIT8C')
        selector = self.page.report_selectors['payroll']
        self.assertEqual(selector.currentData(), 'form:PIT8C')
        self.page.search.setText('B')
        self.assertEqual(self.page.declaration_tables['payroll'].rowCount(), 1)
        self.page.search.clear()
        self.page.render(self.rows[:1], '2026-12')
        self.assertEqual(selector.currentData(), 'form:PIT8C')
        self.assertEqual(self.page.declaration_tables['payroll'].item(0, 4).text(), 'Brak danych')

    def test_ezla_combines_companies_and_keeps_unread_firms_visible(self):
        self.select('ezla')
        panel = self.page.ezla_panel
        self.assertEqual(panel.table.rowCount(), 3)
        results = []
        for company in self.rows[:2]:
            report = Report(company['nip'], company['nazwa'], '2026-11-12', [
                Certificate('Anna Test', '00000000000', 'PESEL', 'ZZ', str(company['id']),
                            'Wystawione', '2026-11-01', '2026-11-01', '2026-11-10')])
            results.append({'report': report, 'company': company, 'database': 'Enova' + str(company['id'])})
        panel.render_batch({'reports': results, 'errors': []})
        panel.render_batch({'reports': results, 'errors': []})
        self.assertEqual(panel.table.rowCount(), 3)
        self.assertEqual(panel.table.item(0, 1).text(), 'Anna Test')
        self.assertEqual(panel.table.item(1, 1).text(), 'Anna Test')
        self.assertEqual(panel.table.item(2, 4).text(), 'Nie wczytano raportu XML')
        self.page.search.setText('C')
        self.assertEqual(panel.table.rowCount(), 1)
        self.page.search.clear()
        self.assertEqual(panel.table.item(0, 1).text(), 'Anna Test')
        panel.clear()
        self.assertEqual(panel.table.rowCount(), 3)
        self.assertEqual(panel.table.item(0, 4).text(), 'Nie wczytano raportu XML')
