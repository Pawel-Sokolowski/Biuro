import json
from unittest import TestCase
from unittest.mock import patch
from PyQt6.QtCore import QDate

from app.companies.checking_obligations import profile, report_applies, load, save
from app.companies.monthly_setup import MonthlySetup
from app.integrations.enova.accounting import declaration_entries, list_accounting
from app.db.dao_conn import get_conn
from tests.base import DbTestCase


class MonthlyScopeRulesTests(TestCase):
    def test_dra_only_hides_rca_even_when_historical_source_has_rca(self):
        row = {'checking_period': '2026-11', 'checking_obligations': {'periods': {
            '2026-11': {'vat_period': 'auto', 'forms': {'DRA': 'monthly', 'RCA': 'none'}}}},
            'checking': {'declarations': [{'department': 'payroll', 'form': 'ZUS RCA'},
                                          {'department': 'payroll', 'form': 'ZUS DRA'}]}}
        self.assertFalse(report_applies(row, 'RCA'))
        self.assertTrue(report_applies(row, 'DRA'))
        self.assertEqual([entry['form'] for entry in declaration_entries(row, 'payroll')], ['ZUS DRA'])
        self.assertTrue(report_applies(row, 'RCA', '2026-10'))

    def test_quarterly_vat_keeps_monthly_jpk_and_unknown_status_visible(self):
        row = {'status_vat': 'Czynny', 'vat_czynny': 0, 'checking_obligations': {'periods': {
            '2026-01': {'vat_period': 'quarterly', 'forms': {'JPKVAT': 'monthly', 'PIT': 'quarterly'}}}}}
        for month in ('2026-01', '2026-02', '2026-03'):
            self.assertTrue(report_applies(row, 'JPK_VAT', month))
            self.assertTrue(report_applies(row, 'JPK_V7K', month))
            self.assertEqual(report_applies(row, 'VAT', month), month == '2026-03')
            self.assertEqual(report_applies(row, 'PIT', month), month == '2026-03')
        self.assertFalse(report_applies({**row, 'status_vat': 'Zwolniony'}, 'JPK_VAT', '2026-03'))
        self.assertTrue(report_applies({'status_vat': 'Brak danych'}, 'JPK_VAT', '2026-03'))
        self.assertTrue(report_applies({'status_vat': 'Zwolniony'}, 'JPK_CIT', '2026-03'))

    def test_monthly_scope_changes_do_not_rewrite_previous_periods(self):
        settings = {'periods': {'2026-01': {'vat_period': 'monthly', 'forms': {'RCA': 'monthly'}},
                                '2026-11': {'vat_period': 'quarterly', 'forms': {'RCA': 'none'}}}}
        self.assertEqual(profile(settings, '2026-10')['forms']['RCA'], 'monthly')
        self.assertEqual(profile(settings, '2027-01')['forms']['RCA'], 'none')


class MonthlyScopeIntegrationTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company('DRA only', nip='1234567890', status_vat='Czynny')

    def test_selection_save_reload_and_effective_month(self):
        page = MonthlySetup(self.company)
        self.addCleanup(page.close)
        page.month.setDate(QDate(2026, 11, 1))
        page.checks['RCA'].setChecked(False)
        page.vat.setCurrentIndex(page.vat.findData('quarterly'))
        page.month.setDate(QDate(2026, 12, 1))
        self.assertFalse(page.checks['RCA'].isChecked())
        page.persist(self.company, self.admin)
        value = load(self.company)
        self.assertEqual(profile(value, '2026-11')['vat_period'], 'quarterly')
        self.assertEqual(profile(value, '2026-10')['vat_period'], 'auto')
        row = next(row for row in list_accounting(self.admin, '2026-11') if row['id'] == self.company)
        self.assertFalse(report_applies(row, 'RCA'))

    def test_selected_report_excludes_non_applicable_companies_without_hiding_missing_required_data(self):
        from app.accounting.accounting_ui import AccountingPage
        rows = [{'id': 1, 'nazwa': 'DRA only', 'status_vat': 'Zwolniony',
                 'checking_obligations': {'periods': {'2026-01': {'vat_period': 'auto', 'forms': {'RCA': 'none'}}}}},
                {'id': 2, 'nazwa': 'Reports required', 'status_vat': 'Czynny'}]
        page = AccountingPage(self.admin, department='payroll')
        self.addCleanup(page.close)
        page.render(rows, '2026-11')
        selector = page.report_selectors['payroll']
        selector.setCurrentIndex(selector.findData('form:RCA'))
        table = page.declaration_tables['payroll']
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), 'Reports required')
        page.attention.setChecked(True)
        self.assertEqual(table.rowCount(), 1)
        selector = page.report_selectors['accounting']
        selector.setCurrentIndex(selector.findData('form:JPKVAT'))
        self.assertEqual(page.declaration_tables['accounting'].rowCount(), 1)

    def test_permission_and_invalid_monthly_cadences_cannot_change_settings(self):
        edit = {'2026-11': {'vat_period': 'quarterly', 'forms': {'DRA': 'monthly'}}}
        with patch('app.security.authorization.can', return_value=False):
            with self.assertRaises(PermissionError):
                save(self.company, edit, self.admin)
        with self.assertRaises(ValueError):
            save(self.company, {'2026-11': {'vat_period': 'auto', 'forms': {'JPKVAT': 'quarterly'}}}, self.admin)
        self.assertEqual(load(self.company), {})
