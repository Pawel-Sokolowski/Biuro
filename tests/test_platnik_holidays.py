import json
import os
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock,patch
from cryptography.fernet import Fernet
from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting,set_setting
from app.integrations.platnik.holidays import normalize,_import_year,read_year,import_year
from app.integrations.platnik.holiday_query import QUERY


def source_row(**changes):
    return {'source_id':1,'source_payer_id':2,'source_person_id':3,'nip':'PL 111-222-33-44',
            'imiona_nazwiska':'Jan Testowy','rok':2026,'miesiac':10,'month_year':2026,
            'kod_ubezpieczenia':'0514','status_zrodlowy':'A','skladka_em':Decimal('100.00'),**changes}


class NormalizationTests(TestCase):
    def test_status_codes_are_record_state_not_refusal_or_payment(self):
        active,inactive = normalize([source_row(),source_row(source_id=2,status_zrodlowy='N')],2026)
        self.assertEqual(active['nip'],'1112223344')
        self.assertEqual(active['kod_ubezpieczenia'],'0514')
        self.assertEqual(active['decyzja'],'Aktualne zwolnienie (A)')
        self.assertEqual(inactive['decyzja'],'Nieaktualny wpis (N)')
        self.assertIsNone(active['platnosc'])
        self.assertIsNone(active['skladki_oplacane'])
        self.assertIsNone(active['wyslane_powiadomienie'])
        self.assertEqual(active['skladka_em'],'100.00')
        self.assertEqual(len(normalize([source_row(status_zrodlowy='?')],2026)[0]['issues']),1)

    def test_missing_identity_is_flagged_and_wrong_year_or_month_rejected(self):
        row = normalize([source_row(nip=None,imiona_nazwiska=None)],2026)[0]
        self.assertEqual(len(row['issues']),2)
        with self.assertRaises(ValueError):
            normalize([source_row(rok=2025)],2026)
        with self.assertRaises(ValueError):
            normalize([source_row(miesiac=13)],2026)
        self.assertIn('WHERE r.ROK=?',QUERY)
        self.assertIn('r.ID_PLATNIK',QUERY)
        self.assertNotIn('ID_PLATNIKA',QUERY)


class ImportTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.key = patch.dict(os.environ,{'BIO_ENCRYPTION_KEY':Fernet.generate_key().decode()})
        self.key.start()
        self.addCleanup(self.key.stop)
        self.company = self.make_company(nip='1112223344')
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]

    def import_mock(self,rows):
        connection = Mock()
        cursor = connection.cursor.return_value
        columns = list(rows[0] if rows else source_row())
        cursor.description = [(name,) for name in columns]
        cursor.fetchmany.return_value = [tuple(row.get(name) for name in columns) for row in rows]
        with patch('app.integrations.platnik.holidays.load',return_value={'enabled':True}), \
             patch('app.integrations.platnik.holidays.connect',return_value=connection):
            result = _import_year(2026,self.admin)
        connection.close.assert_called_once()
        cursor.execute.assert_called_once_with(QUERY,2026)
        return result

    def test_import_keeps_n_records_encrypted_and_filters_by_company_access(self):
        self.assertEqual(self.import_mock([source_row(),source_row(source_id=2,status_zrodlowy='N')])['rows'],2)
        snapshot = get_setting('platnik_holidays_2026')
        self.assertNotIn('Jan Testowy',snapshot)
        self.assertNotIn('"kod_ubezpieczenia"',snapshot)
        result = read_year(self.admin,2026)
        self.assertEqual([row['status_zrodlowy'] for row in result['rows']],['A','N'])
        with patch('app.integrations.enova.accounting.require_access'), \
             patch('app.security.authorization.assigned_company_ids',return_value=set()), \
             patch('app.security.authorization.is_administrator',return_value=False):
            self.assertEqual(read_year(999,2026)['rows'],[])

    def test_failed_year_import_does_not_replace_previous_snapshot(self):
        self.import_mock([source_row()])
        previous = get_setting('platnik_holidays_2026')
        with self.assertRaises(ValueError):
            self.import_mock([source_row(rok=2025)])
        self.assertEqual(get_setting('platnik_holidays_2026'),previous)

    def test_empty_source_year_is_stored_as_a_successful_empty_read(self):
        self.assertEqual(self.import_mock([])['rows'],0)
        result = read_year(self.admin,2026)
        self.assertTrue(result['imported_at'])
        self.assertEqual(result['rows'],[])
        self.assertEqual(len(result['companies']),1)

    def test_non_admin_cannot_import(self):
        with patch('app.security.authorization.is_administrator',return_value=False),self.assertRaises(PermissionError):
            import_year(2026,999)


class YearlyPanelTests(TestCase):
    def test_payroll_selector_uses_year_and_hides_month_without_second_monthly_editor(self):
        from app.accounting.accounting_ui import AccountingPage
        page = AccountingPage(5,department='payroll')
        try:
            selector = page.report_selectors['payroll']
            selector.setCurrentIndex(selector.findData('holidays'))
            self.assertTrue(page.yearly_selected())
            self.assertTrue(page.month.isHidden())
            self.assertEqual(page.holiday_panel.year.minimum(),2024)
            selector.setCurrentIndex(selector.findData('documents'))
            self.assertFalse(page.yearly_selected())
            self.assertFalse(page.month.isHidden())
        finally:
            page.close()

    def test_missing_and_inactive_entries_are_visible_without_payment_inference(self):
        from app.accounting.holiday_ui import ContributionHolidays
        panel = ContributionHolidays(5)
        try:
            rows = normalize([source_row(),source_row(source_id=2,status_zrodlowy='N')],2026)
            panel.loaded({'rows':[{**row,'firma':'Firma A'} for row in rows],
                          'companies':[{'id':1,'nip':'1112223344','nazwa':'Firma A'},
                                       {'id':2,'nip':'2223334455','nazwa':'Firma B'}],
                          'imported_at':'2026-11-13T10:00:00+01:00','configured':True})
            self.assertEqual(panel.table.rowCount(),3)
            self.assertEqual(panel.table.item(0,7).text(),'Brak danych')
            panel.active.setChecked(True)
            self.assertEqual(panel.table.rowCount(),1)
        finally:
            panel.close()
