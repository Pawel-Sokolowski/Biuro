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
from app.integrations.platnik.holiday_query import QUERY, scoped_query
from app.integrations.platnik.holiday_tracking import read_tracking_year,save_field,WorksheetConflict


def source_row(**changes):
    return {'source_id':1,'source_payer_id':2,'source_person_id':3,'nip':'PL 111-222-33-44',
            'imiona_nazwiska':'Jan Testowy','rok':2026,'miesiac':10,'month_year':2026,
            'kod_ubezpieczenia':'0514','bazowy_kod_ubezpieczenia':'0510',
            'status_zrodlowy':'A','skladka_em':Decimal('100.00'),**changes}


class NormalizationTests(TestCase):
    def test_status_codes_are_record_state_not_refusal_or_payment(self):
        active,inactive = normalize([source_row(),source_row(source_id=2,status_zrodlowy='N')],2026)
        self.assertEqual(active['nip'],'1112223344')
        self.assertEqual(active['kod_ubezpieczenia'],'0510')
        self.assertEqual(active['kod_ulgi'],'0514')
        self.assertEqual(active['opis_statusu'],'Aktualne zwolnienie (A)')
        self.assertEqual(inactive['opis_statusu'],'Nieaktualny wpis (N)')
        self.assertIsNone(active['decyzja'])
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
        clock = patch('app.companies.holiday_eligibility.current_month', return_value='2026-11')
        clock.start()
        self.addCleanup(clock.stop)
        self.company = self.make_company(nip='1112223344')
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
            self.company_ids = {row[0] for row in conn.execute('SELECT id FROM firmy').fetchall()}

    def company_row(self,payload):
        # init_db also seeds "Domyślna firma", which sorts before the test client.
        # Verify the intended company's notes, independently of display ordering.
        rows = [row for row in payload['rows'] if row['firma_id']==self.company]
        self.assertEqual(len(rows),1)
        return rows[0]

    def import_mock(self,rows,company_ids=None):
        connection = Mock()
        cursor = connection.cursor.return_value
        columns = list(rows[0] if rows else source_row())
        cursor.description = [(name,) for name in columns]
        cursor.fetchmany.return_value = [tuple(row.get(name) for name in columns) for row in rows]
        with patch('app.integrations.platnik.holidays.load',return_value={'enabled':True}), \
             patch('app.integrations.platnik.holidays.connect',return_value=connection):
            result = _import_year(2026,self.admin,company_ids=company_ids)
        connection.close.assert_called_once()
        if company_ids is None:
            cursor.execute.assert_called_once_with(QUERY, 2026)
        else:
            cursor.execute.assert_called_once_with(scoped_query(), '<p><n>1112223344</n></p>', 2026)
        return result

    def test_selected_company_refresh_preserves_other_payers(self):
        self.make_company('Other',nip='2223334455')
        self.import_mock([source_row(),source_row(source_id=2,nip='2223334455',imiona_nazwiska='Old other')])
        self.import_mock([source_row(imiona_nazwiska='Updated selected'),
                          source_row(source_id=2,nip='2223334455',imiona_nazwiska='New other')],company_ids=[self.company])
        rows = read_year(self.admin,2026)['rows']
        self.assertEqual({row['nip']:row['imiona_nazwiska'] for row in rows},
                         {'1112223344':'Updated selected','2223334455':'Old other'})

    def test_six_column_worksheet_preserves_office_fields_and_distinguishes_rws_state(self):
        from app.accounting.holiday_ui import ContributionHolidays
        self.import_mock([source_row(status_zrodlowy='N')])
        save_field(self.admin, 2026, self.company, 'moze_skorzystac', 'Tak', 0)
        save_field(self.admin, 2026, self.company, 'zlozone', 'Tak, 09.11', 0)
        view = ContributionHolidays(self.admin)
        self.addCleanup(view.deleteLater)
        view.loaded(read_tracking_year(self.admin, 2026))
        self.assertEqual(
            [view.table.horizontalHeaderItem(col).text() for col in range(view.table.columnCount())],
            ['Firma', 'Może skorzystać ze zwolnienia', 'Wysłane powiadomienie',
             'Złożone', 'Miesiąc zwolnienia', 'Stan'],
        )
        index = next(i for i, row in enumerate(view.displayed_rows) if row['firma_id'] == self.company)
        self.assertEqual(view.table.item(index, 1).text(), 'Do sprawdzenia')
        self.assertIn('Wcześniejszy wpis biura: Tak', view.table.item(index, 1).toolTip())
        self.assertEqual(view.table.item(index, 3).text(), 'Tak, 09.11')
        self.assertEqual(view.table.item(index, 5).text(), 'Nieaktualny wpis RWS')
        save_field(self.admin, 2026, self.company, 'decyzja', 'Przyznano zwolnienie', 0)
        view.loaded(read_tracking_year(self.admin, 2026))
        self.assertEqual(view.table.item(index, 5).text(), 'Przyznano zwolnienie')

    def test_holiday_rca_records_affect_month_and_state_only_with_transmission_evidence(self):
        from app.accounting.holiday_ui import _worksheet_state
        from app.db.checking_storage import save_snapshot
        from app.utils.crypto import encrypt_password

        def snapshot(period, code, **fields):
            entry = {'form': 'ZUS RCA', 'employee_id': '12', 'insurance_code': code,
                     'payer_nip': '1112223344', 'status': 'unknown', 'raw_status': 'P', **fields}
            with get_conn() as conn:
                save_snapshot(conn, 'platnik_declarations', self.company, period,
                              encrypt_password(json.dumps({'declarations': [entry]})))

        snapshot('2026-03', '05 14 00')
        snapshot('2026-07', '05 94 00', status='rejected', sent_at='2026-08-05')
        snapshot('2026-08', '05 74 00', employee_id='')
        snapshot('2025-12', '0514', status='accepted', upo='OLDER')
        row = self.company_row(read_tracking_year(self.admin, 2026))
        self.assertIsNone(row['miesiac'])
        self.assertEqual(row['moze_skorzystac'], 'Do sprawdzenia')
        self.assertIn('sprawdź wysyłkę', _worksheet_state(row, False))

        snapshot('2026-11', '057401', status='sent', sent_at='2026-12-02')
        snapshot('2026-12', '059400', status='accepted', upo='RECEIPT')
        row = self.company_row(read_tracking_year(self.admin, 2026))
        self.assertEqual(row['miesiac'], '11; 12')
        self.assertEqual(row['moze_skorzystac'], 'Wykorzystano według RCA')
        self.assertIn('Wysłano RCA (05 74, 05 94)', _worksheet_state(row, False))
        self.assertIsNone(row['zlozone'])  # Filing an RCA does not attest to the RWS application.

    def test_rca_adapter_requires_documented_transmission_fields(self):
        from app.integrations.platnik.declarations import normalize as normalize_declarations

        nip = '1112223344'
        month = '2026-11'
        row = {'form': 'ZUS RCA', 'nip': nip, 'miesiac': month, 'valid_link': 1,
               'source_id': 21, 'payer_id': 23, 'employee_id': 42, 'employee_name': 'Test',
               'report_id': '01', 'insurance_code': '05 94 00', 'raw_status': 'P'}

        def read(**fields):
            return normalize_declarations([{**row, **fields}], [month], [nip])[nip][month][0]

        self.assertEqual(read()['status'], 'unknown')
        self.assertEqual(read(filing_status='sent', sent_at='2026-12-02')['status'], 'sent')
        self.assertEqual(read(filing_status='accepted', upo='RCA-RECEIPT')['status'], 'accepted')
        with self.assertRaises(ValueError):
            read(filing_status='sent')

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
        self.assertEqual({company['id'] for company in result['companies']},self.company_ids)

    def test_non_admin_cannot_import(self):
        with patch('app.security.authorization.is_administrator',return_value=False),self.assertRaises(PermissionError):
            import_year(2026,999)

    def test_connection_query_timeout_is_saved_without_replacing_existing_password(self):
        from app.integrations.platnik.holidays import load,save
        save({'server':'worksheet-fixture.invalid,1433','database':'worksheet_fixture',
              'username':'fixture_reader','query_timeout':300},
             self.admin,password='fixture-only')
        stored = load()
        self.assertEqual(stored['query_timeout'],300)
        save({'query_timeout':360},self.admin)
        updated = load()
        self.assertEqual(updated['query_timeout'],360)
        self.assertEqual(updated['password_enc'],stored['password_enc'])

    def test_office_notes_survive_import_without_rws_and_do_not_leak_into_another_year(self):
        employee = self.make_employee(imie='Katarzyna',nazwisko='Cicha')
        save_field(employee,2026,self.company,'kto_wyslal','KC',0)
        save_field(employee,2026,self.company,'decyzja','zawieszony?',0)
        save_field(employee,2026,self.company,'miesiac','luty',0)
        save_field(employee,2026,self.company,'skladki_oplacane','tylko zdrowotna',0)
        before = read_tracking_year(employee,2026)
        self.assertEqual(before['current_initials'],'KC')
        self.assertEqual(self.company_row(before)['kto_wyslal'],'KC')
        self.assertEqual(self.company_row(before)['sources'],[])
        for row in before['rows']:
            if row['firma_id']!=self.company:
                self.assertIsNone(row['kto_wyslal'])
                self.assertEqual(row['notes'],{})
        snapshot = get_setting(f'platnik_holiday_notes_2026_{self.company}')
        self.assertNotIn('zawieszony?',snapshot)
        self.import_mock([source_row(),source_row(source_id=2,status_zrodlowy='N',bazowy_kod_ubezpieczenia='0590')])
        row = self.company_row(read_tracking_year(employee,2026))
        self.assertEqual(row['kto_wyslal'],'KC')
        self.assertEqual(row['decyzja'],'zawieszony?')
        self.assertEqual(row['miesiac'],'luty')
        self.assertEqual(row['source_values']['miesiac'],'10')
        self.assertEqual(row['kod_ubezpieczenia'],'0510; 0590')
        self.assertEqual(row['statuses'],['A','N'])
        self.assertEqual(len(row['sources']),2)
        self.assertEqual(row['notes']['kto_wyslal']['actor_id'],employee)
        self.assertIsNone(self.company_row(read_tracking_year(employee,2025))['kto_wyslal'])
        self.import_mock([])
        self.assertEqual(self.company_row(read_tracking_year(employee,2026))['decyzja'],'zawieszony?')

    def test_separate_field_edits_merge_but_stale_same_field_edit_is_rejected(self):
        save_field(self.admin,2026,self.company,'wyslane_powiadomienie','Wysłano 09/11/2026',0)
        save_field(self.admin,2026,self.company,'kto_wyslal','KC',0)
        with self.assertRaises(WorksheetConflict):
            save_field(self.admin,2026,self.company,'kto_wyslal','AB',0)
        row = self.company_row(read_tracking_year(self.admin,2026))
        self.assertEqual(row['wyslane_powiadomienie'],'Wysłano 09/11/2026')
        self.assertEqual(row['kto_wyslal'],'KC')
        save_field(self.admin,2026,self.company,'kto_wyslal','',1)
        self.assertEqual(self.company_row(read_tracking_year(self.admin,2026))['kto_wyslal'],'')
        save_field(self.admin,2026,self.company,'kto_wyslal',None,2)
        self.assertIsNone(self.company_row(read_tracking_year(self.admin,2026))['kto_wyslal'])

    def test_parallel_edits_to_different_fields_are_not_lost(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.config import get_schema,schema_context
        schema = get_schema()
        def edit(field,value):
            with schema_context(schema):
                return save_field(self.admin,2026,self.company,field,value,0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(edit,'kto_wyslal','KC'),pool.submit(edit,'oswiadczenie','sama')]
            for future in results:
                future.result(timeout=20)
        row = self.company_row(read_tracking_year(self.admin,2026))
        self.assertEqual((row['kto_wyslal'],row['oswiadczenie']),('KC','sama'))

    def test_notes_require_checking_and_company_access_and_a_readable_shared_key(self):
        with patch('app.integrations.enova.accounting.allowed_tabs',return_value=[]),self.assertRaises(PermissionError):
            save_field(self.admin,2026,self.company,'kto_wyslal','KC',0)
        with patch('app.integrations.platnik.holiday_tracking.assigned_company_ids',return_value=set()),self.assertRaises(PermissionError):
            save_field(self.admin,2026,self.company,'kto_wyslal','KC',0)
        save_field(self.admin,2026,self.company,'kto_wyslal','KC',0)
        original = get_setting(f'platnik_holiday_notes_2026_{self.company}')
        with patch.dict(os.environ,{'BIO_ENCRYPTION_KEY':Fernet.generate_key().decode()}):
            with self.assertRaises(ValueError):
                read_tracking_year(self.admin,2026)
            with self.assertRaises(ValueError):
                save_field(self.admin,2026,self.company,'kto_wyslal','AB',1)
        self.assertEqual(get_setting(f'platnik_holiday_notes_2026_{self.company}'),original)


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
            panel.loaded({'rows':[{'firma_id':1,'firma':'Firma A','nip':'1112223344','miesiac':'1; 10','sources':rows,'statuses':['A','N']},
                                  {'firma_id':2,'firma':'Firma B','nip':'2223334455','sources':[],'statuses':[]}],
                          'imported_at':'2026-11-13T10:00:00+01:00','configured':True})
            self.assertEqual(panel.table.rowCount(),2)
            self.assertEqual(panel.table.columnCount(), 6)
            self.assertEqual(panel.table.item(0,4).text(),'styczeń; październik')
            self.assertEqual(panel.table.item(0,3).text(),'Brak danych')
            self.assertEqual(panel.table.item(0,5).text(),'Aktualne zwolnienie (RWS) · Nieaktualny wpis RWS')
            self.assertEqual(panel.table.item(1,5).text(),'Brak wpisu RWS')
            panel.active.setChecked(True)
            self.assertEqual(panel.table.rowCount(),1)
            panel.search.setText('październik')
            self.assertEqual(panel.table.rowCount(),1)
        finally:
            panel.close()
