from datetime import date
from unittest import TestCase
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from app.integrations.enova.document_counts import sql_connection_string
from app.integrations.enova.checking import (history_months, validate_declarations,
    validate_payroll, run_checking_import)
from app.integrations.enova.accounting import list_accounting
from app.db.checking_storage import save_snapshot
from app.db.dao_conn import get_conn
from tests.base import DbTestCase

NIP = '1234567890'
SOURCE = {'nip': NIP, 'database': 'EnovaA'}


class CheckingRulesTests(TestCase):
    def test_certificate_trust_is_explicit_and_preserves_encryption(self):
        for trust, expected in ((False, 'no'), (True, 'yes')):
            connection = sql_connection_string({'server': 'sql', 'trust_server_certificate': trust}, 'A', 'user', 'pass')
            self.assertIn('Encrypt={yes}', connection)
            self.assertIn('TrustServerCertificate={' + expected + '}', connection)
        with self.assertRaises(ValueError):
            sql_connection_string({'server': 'sql', 'trust_server_certificate': 'false'}, 'A', 'user', 'pass')

    def test_rolling_history_crosses_year_boundary_and_excludes_current_month(self):
        months = history_months('2026-01')
        self.assertEqual(months[0], '2025-01')
        self.assertEqual(months[-1], '2025-12')
        self.assertEqual(len(months), 12)

    def test_upo_acceptance_and_dates_are_not_inferred_from_import_time(self):
        row = {'nip': NIP, 'miesiac': '2026-11', 'department': 'accounting', 'form': 'CIT-8',
               'status': 'accepted', 'sent_at': '2026-11-02T10:00:00', 'upo': 'REF', 'upo_at': '2026-11-03T12:00:00'}
        value = validate_declarations([row], SOURCE, ['2026-11'])['2026-11'][0]
        self.assertNotEqual(value['sent_at'], value['upo_at'])
        for changed in ({'upo': ''}, {'department': 'invalid'}, {'sent_at': 'bad'}, {'nip': '9999999999'}):
            with self.assertRaises(ValueError):
                validate_declarations([{**row, **changed}], SOURCE, ['2026-11'])
        with self.assertRaises(ValueError):
            validate_declarations([row, row], SOURCE, ['2026-11'])
        self.assertEqual(validate_declarations([], SOURCE, ['2026-11']), {'2026-11': []})

    def test_unknown_tax_status_preserves_raw_code_without_inventing_dates_or_acceptance(self):
        row = {'nip': NIP, 'miesiac': '2026-11', 'department': 'accounting', 'form': 'VAT-UE',
               'status': 'unknown', 'raw_status': 'Enova=17; MF=200',
               'sent_at': None, 'upo': '', 'upo_at': None}
        value = validate_declarations([row], SOURCE, ['2026-11'])['2026-11'][0]
        self.assertEqual(value['raw_status'], 'Enova=17; MF=200')
        self.assertEqual(value['status'], 'unknown')
        self.assertIsNone(value['sent_at'])
        self.assertIsNone(value['upo_at'])
        with self.assertRaises(ValueError):
            validate_declarations([{**row, 'status': 'accepted'}], SOURCE, ['2026-11'])
        from app.integrations.enova.jpk import validate_jpk
        jpk = validate_jpk([row], SOURCE, ['2026-11'])['2026-11']
        self.assertEqual(jpk['raw_status'], row['raw_status'])
        self.assertEqual(jpk['status'], 'unknown')

    def test_source_processing_error_and_cancellation_are_distinct_from_mf_rejection(self):
        from app.accounting.insights import declaration_issue
        row = {'nip': NIP, 'miesiac': '2026-11', 'department': 'accounting', 'form': 'VAT-UE',
               'sent_at': None, 'upo': '', 'upo_at': None}
        for status, message in (('failed', 'Błąd przetwarzania'), ('cancelled', 'Anulowana'), ('rejected', 'Odrzucona')):
            value = validate_declarations([{**row, 'status': status}], SOURCE, ['2026-11'])['2026-11'][0]
            self.assertEqual(declaration_issue(value), message)

    def test_payroll_requires_explicit_empty_month_and_rejects_duplicate_types(self):
        row = {'nip': NIP, 'miesiac': '2026-11', 'rodzaj': 'Lista płac', 'liczba': 2}
        self.assertEqual(validate_payroll([row], SOURCE, ['2026-11'])['2026-11'], {'Lista płac': 2})
        with self.assertRaises(ValueError):
            validate_payroll([], SOURCE, ['2026-11'])
        with self.assertRaises(ValueError):
            validate_payroll([row, row], SOURCE, ['2026-11'])
        self.assertEqual(validate_payroll([{**row, 'rodzaj': None, 'liczba': 0}], SOURCE, ['2026-11']), {'2026-11': {}})

    def test_old_tab_permission_is_preserved(self):
        from app.security.authorization import parse_tabs
        self.assertIn('Sprawdzanie', parse_tabs('Pulpit|Księgowość'))
        self.assertNotIn('Sprawdzanie', parse_tabs('Pulpit'))


class CheckingIntegrationTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company(nip=NIP, forma_opodatkowania='Ryczałt')

    def test_independent_backfill_does_not_touch_manual_count_or_stage(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok,etap) VALUES (%s,'2026-11',77,'Brak')", (self.company,))
        config = {'enabled': True, 'months_back': 1, 'sources': [SOURCE]}
        def fetch(config, source, months, *, result_kind):
            return [{'nip': NIP, 'miesiac': month, 'dokumenty': 1 if month == '2025-11' else 0,
                     'pozycje_bankowe': 0, 'rodzaje': {'AMO': 1} if month == '2025-11' else {}} for month in months]
        with patch('app.integrations.enova.checking.fetch_counts', side_effect=fetch) as remote:
            first = run_checking_import(config, apply=True, today=date(2026, 11, 12))
            second = run_checking_import(config, apply=True, today=date(2026, 11, 12))
            self.assertTrue(first[0]['ok'], first)
            self.assertEqual(second[0]['mode'], 'skipped')
            remote.assert_called_once()
        with get_conn() as conn:
            self.assertEqual(tuple(conn.execute('SELECT ilosc_dok,etap FROM statusy_miesieczne WHERE firma_id=%s', (self.company,)).fetchone()), (77, 'Brak'))
        from app.integrations.enova.accounting import active_types
        row = next(row for row in list_accounting(self.admin, '2026-11') if row['id'] == self.company)
        self.assertEqual(active_types(row, 'accounting'), {'AMO'})

    def test_failed_hr_read_can_be_retried_on_the_same_day(self):
        config = {'enabled': True, 'months_back': 1, 'sources': [{**SOURCE, 'hr_events_query': 'hr.sql'}]}
        day = date(2026, 11, 29)
        counts = [{'nip': NIP, 'miesiac': '2026-11', 'dokumenty': 0, 'pozycje_bankowe': 0, 'rodzaje': {}}]
        with patch('app.integrations.enova.checking.fetch_counts', return_value=counts), \
             patch('app.integrations.enova.hr_events.fetch_events', side_effect=[RuntimeError('temporary'), []]) as fetch:
            first, = run_checking_import(config, apply=True, today=day, months=['2026-11'])
            second, = run_checking_import(config, apply=True, today=day, months=['2026-11'])
        self.assertTrue(first['hr_failed'])
        self.assertFalse(second['hr_failed'])
        self.assertEqual(fetch.call_count, 2)

    def test_history_is_bounded_and_isolated_between_company_ids(self):
        # IDs such as 1 and 11 must never overlap through SQL LIKE wildcards.
        another = self.make_company('Other history')
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET id=%s WHERE id=%s', (int(f'{self.company}1'), another))
            for company_id, month, kind in ((self.company, '2025-10', 'TOO_OLD'), (self.company, '2026-10', 'AMO'),
                                            (int(f'{self.company}1'), '2026-10', 'WRONG_COMPANY')):
                save_snapshot(conn, 'enova_checking', company_id, month,
                              {'documents': {'accounting': {kind: 1}, 'payroll': None}, 'accounting_details': {'private': True}})
        row = next(row for row in list_accounting(self.admin, '2026-11') if row['id'] == self.company)
        from app.integrations.enova.accounting import active_types
        self.assertEqual(active_types(row, 'accounting'), {'AMO'})
        self.assertEqual([report['month'] for report in row['history']], ['2026-10'])
        self.assertNotIn('accounting_details', row['history'][0])

    def test_view_uses_per_company_history_for_red_zero_and_filters_tax(self):
        from app.accounting.accounting_ui import AccountingPage
        app = QApplication.instance()
        self.assertIsNotNone(app)
        common = {'checking': {'documents': {'accounting': {}, 'payroll': None}, 'declarations': None},
                  'snapshot': None, 'jpk': None, 'ilosc_dok': 77}
        rows = [{**common, 'id': self.company, 'nazwa': 'A', 'nip': NIP, 'forma_opodatkowania': 'Ryczałt',
                 'history': [{'month': '2026-10', 'documents': {'accounting': {'AMO': 2}}}]},
                {**common, 'id': 999, 'nazwa': 'B', 'nip': '9999999999', 'forma_opodatkowania': 'KPiR', 'history': []}]
        page = AccountingPage(self.admin)
        page.render(rows, '2026-11')
        headers = [page.table.horizontalHeaderItem(i).text() for i in range(page.table.columnCount())]
        self.assertEqual(headers[4:8], ['Sprzedaż', 'Zakup', 'DW', 'Razem'])
        self.assertNotIn('AMO', headers)
        column = headers.index('DW')
        self.assertEqual(page.table.item(0, column).text(), '0')
        self.assertEqual(page.table.item(0, column).background().color().name(), '#fee2e2')
        self.assertNotEqual(page.table.item(1, column).background().color().name(), '#fee2e2')
        self.assertEqual(page.tabs.tabText(0), 'Księgowość')
        self.assertEqual(page.tabs.tabText(1), 'Kadry')
        self.assertNotIn('payroll', page.comparison_tables)
        selector = page.report_selectors['accounting']
        self.assertEqual(page.comparison_tables['accounting'].rowCount(), 0)
        selector.setCurrentIndex(selector.findData('comparison'))
        self.assertGreater(page.comparison_tables['accounting'].rowCount(), 0)
        self.assertNotIn('Rok temu', [page.comparison_tables['accounting'].horizontalHeaderItem(i).text()
                                     for i in range(page.comparison_tables['accounting'].columnCount())])
        selector.setCurrentIndex(selector.findData('documents'))
        page.tax_filter.setCurrentIndex(page.tax_filter.findData('KPIR'))
        self.assertEqual(page.table.rowCount(), 1)
        self.assertEqual(page.table.item(0, 0).text(), 'B')
        page.close()

    def test_checking_always_uses_short_company_names(self):
        from PyQt6.QtCore import Qt
        from app.accounting.accounting_ui import AccountingPage
        from app.db.dao_companies import get_companies_filtered, save_partner

        other = self.make_company('Druga firma do sprawdzenia', forma_dzialalnosci='Spółka z o.o.')
        unique = self.make_company('Unikalna spółka cywilna', nazwa_skrocona='Unikalna', forma_dzialalnosci='s.c.')
        with get_conn() as conn:
            conn.execute("UPDATE firmy SET nazwa_skrocona='Wspólny skrót' WHERE id = ANY(%s)", ([self.company, other],))
            conn.execute("UPDATE firmy SET forma_dzialalnosci='Spółka cywilna' WHERE id = %s", (self.company,))
        save_partner(self.company, {'imie_pierwsze': 'Jan', 'nazwisko': 'Kowalski'})
        save_partner(self.company, {'imie_pierwsze': 'Anna', 'nazwisko': 'Nowak'})
        save_partner(other, {'imie_pierwsze': 'Tomasz', 'nazwisko': 'Inny'})
        save_partner(unique, {'imie_pierwsze': 'Ewa', 'nazwisko': 'Inna'})

        expected = {
            self.company: 'Wspólny skrót',
            other: 'Wspólny skrót',
            unique: 'Unikalna',
        }

        search = get_companies_filtered('Jan Kowalski', 'Wszystkie', None, self.admin)
        self.assertEqual(len(search), 1)
        self.assertEqual(search[0]['id'], self.company)

        rows = list_accounting(self.admin, '2026-11')
        returned = {row['id']: row for row in rows if row['id'] in expected}
        self.assertEqual({company_id: row['nazwa'] for company_id, row in returned.items()}, expected)

        page = AccountingPage(self.admin)
        self.addCleanup(page.close)
        page.render(rows, '2026-11')
        selector = page.report_selectors['accounting']
        selector.setCurrentIndex(selector.findData('comparison'))
        table = page.comparison_tables['accounting']
        table.frozen_column.horizontalHeader().sectionDoubleClicked.emit(0)
        seen = set()
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            company_id = item.data(Qt.ItemDataRole.UserRole)
            if company_id not in expected:
                continue
            seen.add(company_id)
            self.assertEqual(item.text(), expected[company_id])
            table.selectRow(row)
            self.assertEqual(page.selected_company_ids(), [company_id])
        self.assertEqual(seen, set(expected))

        page.search.setText('Druga firma do sprawdzenia')
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).data(Qt.ItemDataRole.UserRole), other)

    def test_hr_uses_one_company_identity_for_all_civil_partnership_employee_records(self):
        from PyQt6.QtCore import Qt
        from app.accounting.accounting_ui import AccountingPage

        company = {'id': self.company, 'nip': NIP, 'forma_dzialalnosci': 's.c.',
                   'nazwa': 'Spółka — Jan Kowalski, Anna Nowak', 'nazwa_podstawowa': 'Spółka'}
        company['hr'] = {'events': [
            {'event_type': kind, 'event_id': str(index), 'employee_id': '23', 'employee_name': 'Pracownik Testowy',
             'due_date': None, 'status': 'active'}
            for index, kind in enumerate(('contract_end', 'medical_exam', 'ohs_training'), 1)
        ]}
        page = AccountingPage(self.admin, department='payroll')
        self.addCleanup(page.close)
        page.render([company], '2026-11')
        reports = page.report_selectors['payroll']
        reports.setCurrentIndex(reports.findData('hr'))
        page.hr_range.setCurrentIndex(page.hr_range.findData(None))
        self.assertEqual(page.hr_table.rowCount(), 3)
        for row in range(3):
            self.assertEqual(page.hr_table.item(row, 0).text(), 'Spółka')
            self.assertEqual(page.hr_table.item(row, 0).data(Qt.ItemDataRole.UserRole), self.company)
            self.assertEqual(page.hr_table.item(row, 1).text(), NIP)

    def test_payroll_view_shows_employee_declarations_and_hr_without_comparisons(self):
        from app.accounting.accounting_ui import AccountingPage
        entry = {'department': 'payroll', 'form': 'PIT-11', 'status': 'not_sent',
                 'employee_id': '23', 'employee_name': 'Anna Test', 'upo': '', 'source_system': 'enova'}
        row = {'id': self.company, 'nazwa': 'Firma Test', 'nip': NIP, 'kadrowa': 'Kadrowa Biura',
               'forma_opodatkowania': 'Ryczałt', 'history': [],
               'checking': {'documents': {'accounting': {}, 'payroll': {'Umowa': 1}}, 'declarations': [entry]},
               'hr': {'events': [{'employee_id': '23', 'employee_name': 'Anna Test', 'event_type': 'contract_end',
                                  'status': 'active', 'due_date': None, 'document_number': 'U/23'}]}}
        with patch('app.preferences.department.default_department', return_value='payroll'):
            page = AccountingPage(self.admin)
            page.render([row], '2026-11')
            self.assertEqual(page.tabs.currentIndex(), 1)
            self.assertNotIn('payroll', page.comparison_tables)
            reports = page.report_selectors['payroll']
            reports.setCurrentIndex(reports.findData('declarations'))
            table = page.declaration_tables['payroll']
            self.assertEqual(table.item(0, 2).text(), 'Anna Test (23)')
            self.assertEqual(table.item(0, 1).text(), 'Kadrowa Biura')
            selector = page.declaration_filters['payroll']
            selector.setCurrentIndex(selector.findData('accepted'))
            self.assertEqual(table.rowCount(), 0)
            selector.setCurrentIndex(selector.findData('to_send'))
            self.assertEqual(table.rowCount(), 1)
            reports.setCurrentIndex(reports.findData('hr'))
            page.hr_range.setCurrentIndex(page.hr_range.findData(None))
            self.assertEqual(page.hr_table.item(0, 3).text(), 'Anna Test (23)')
            self.assertEqual(page.hr_table.item(0, 4).text(), 'U/23')
            reports.setCurrentIndex(reports.findData('ezla'))
            from app.integrations.ezla.report import Report, Certificate
            report = Report(nip=NIP, payer_name='Firma Test', generated_at='2026-11-12', certificates=[
                Certificate('Anna Test', '00000000000', 'PESEL', 'ZZ', '1', 'Anulowane',
                            '2026-11-01', '2026-11-01', '2026-11-10')])
            page.ezla_panel.render({'report': report, 'company': {'id': self.company, 'nazwa': 'Firma Test'}, 'database': 'EnovaTest'})
            self.assertEqual(page.ezla_panel.table.item(0, 2).text(), 'PESEL: *******0000')
            self.assertEqual(page.ezla_panel.table.item(0, 4).text(), 'Anulowane')
            self.assertIn('wynik importu sprawdź w enova', page.ezla_panel.summary.text())
            page.ezla_panel.clear()
            self.assertEqual(page.ezla_panel.table.rowCount(), 1)
            self.assertEqual(page.ezla_panel.table.item(0, 4).text(), 'Nie wczytano raportu XML')
            page.tabs.setCurrentIndex(0)
            page.apply_department_default()
            self.assertEqual(page.tabs.currentIndex(), 1)
            page.close()
