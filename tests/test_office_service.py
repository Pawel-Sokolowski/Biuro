"""Server sessions, permissions, file capabilities and keyless business calls (CI only)."""
import json
from pathlib import Path
import tempfile
import time
from unittest import mock

from tests.base import DbTestCase
from app.db.dao_conn import get_conn,Row
from app.remote import auth
from app.remote.codec import encode,decode
from app.remote.service import OfficeService
from app.remote.state import configure,service_scope


class OfficeServiceTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.employee = self.make_employee('api@example.com',haslo='Employee12345')
        self.other = self.make_employee('other@example.com',haslo='Other12345')
        self.company = self.make_company('Serwer')
        self.directory = tempfile.TemporaryDirectory()
        self.service = OfficeService(self.directory.name)
        self.token = auth.login('api@example.com','Employee12345','test-peer')['token']
        self.admin = auth.login('admin@biuro.pl','Admin12345','test-admin')['token']

    def tearDown(self):
        configure(None)
        self.service.close()
        self.directory.cleanup()
        super().tearDown()

    def call(self,operation,arguments=None,token=None,object_id=None):
        result = self.service.submit(token or self.token,{'operation':operation,'arguments':encode(arguments or {}),'object':object_id})
        if result.get('completed'):
            return decode(result['result'])
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            response = self.service.poll(token or self.token,{'job':result['job']})
            if response['state']=='done':
                return decode(response['result'])
            if response['state']=='failed':
                exception = PermissionError if response.get('permission') else ValueError
                raise exception(response['message'])
            time.sleep(.01)
        self.fail('Service operation did not complete')

    def test_operation_cache_is_shared_within_job_but_not_between_jobs(self):
        from app.utils.request_cache import cached_value
        loader = mock.Mock(side_effect=['first','second'])
        def company(firma_id):
            first = cached_value('office-cache-regression',loader)
            second = cached_value('office-cache-regression',loader)
            self.assertEqual(first,second)
            return {'id':firma_id,'nazwa':first}
        with mock.patch('app.db.dao_companies.get_company',new=company):
            first = self.call('app.db.dao_companies.get_company',{'firma_id':self.company},token=self.admin)
            second = self.call('app.db.dao_companies.get_company',{'firma_id':self.company},token=self.admin)
        self.assertEqual(first['nazwa'],'first')
        self.assertEqual(second['nazwa'],'second')
        self.assertEqual(loader.call_count,2)

    def test_database_uses_hashed_session_tokens_and_revokes_on_password_change(self):
        with get_conn() as conn:
            value = conn.execute('SELECT token_hash FROM office_api_sessions WHERE employee_id=%s',(self.employee,)).fetchone()[0]
        self.assertNotEqual(value,self.token)
        self.assertEqual(value,auth.digest(self.token))
        self.assertEqual(auth.authenticate(self.token),self.employee)
        from app.db.dao_employees import change_employee_password
        change_employee_password(self.employee,'Changed12345')
        with self.assertRaises(PermissionError):
            self.call('app.db.dao_companies.get_company',{'firma_id':self.company})

    def test_disabled_user_and_logged_out_session_cannot_call_business_operations(self):
        self.service.forget(self.token)
        with self.assertRaises(PermissionError):
            auth.authenticate(self.token)
        other = auth.login('other@example.com','Other12345','peer')['token']
        with get_conn() as conn:
            conn.execute('UPDATE pracownicy SET aktywny=0 WHERE id=%s',(self.other,))
        with self.assertRaises(PermissionError):
            auth.authenticate(other)

    def test_no_key_sql_or_primitive_credential_operations_exist(self):
        for operation in ('execute_sql','get_encryption_key','sync_key','app.utils.crypto.decrypt_password',
                          'app.utils.env_config.ensure_encryption_key','app.db.dao_conn.get_conn',
                          'app.kalendarz.google_sync.get_client_credentials'):
            with self.assertRaises(PermissionError):
                self.call(operation,token=self.admin)
        from app.remote.operations import OPERATIONS
        self.assertFalse(any('key_sync' in name or 'decrypt' in name for name in OPERATIONS))

    def test_employee_cannot_spoof_actor_or_promote_itself(self):
        from app.security.authorization import is_administrator
        self.assertFalse(is_administrator(self.employee))
        result = self.call('app.security.authorization.is_administrator',{'pracownik_id':1})
        self.assertFalse(result)
        with self.assertRaises(PermissionError):
            self.call('app.db.dao_app_settings.set_setting',{'key':'office_name','value':'Injected'})
        with self.assertRaises(PermissionError):
            self.call('app.db.dao_employees.change_employee_password',{'pracownik_id':1,'new_password':'Injected12345'})

    def test_role_id_4_is_administrator_even_if_role_name_or_flag_is_missing(self):
        employee = self.make_employee('role4@example.com',haslo='Employee12345')
        with get_conn() as conn:
            conn.execute(
                "UPDATE pracownicy SET rola_id=4,is_admin=0 WHERE id=%s",
                (employee,),
            )
            conn.execute(
                "UPDATE role SET nazwa='Konto administracyjne' WHERE id=4",
            )
            conn.commit()
        from app.security.authorization import get_permissions,is_administrator,allowed_tabs
        self.assertTrue(is_administrator(employee))
        permissions = get_permissions(employee)
        self.assertTrue(all(permissions.values()))
        self.assertEqual(set(allowed_tabs(employee)),set([
            'Pulpit','Widok miesięczny','Klienci','E-mail','Czat','Faktury',
            'Raporty','Zadania','Kalendarz','Ustawienia','Sprawdzanie',
        ]))

    def test_employee_cannot_manage_alternate_administrator_role(self):
        employee = self.make_employee('staff@example.com',haslo='Employee12345')
        administrator = self.make_employee('alt-admin@example.com',haslo='Admin12345')
        with get_conn() as conn:
            conn.execute(
                "UPDATE pracownicy SET rola_id=4,is_admin=0 WHERE id=%s",
                (administrator,),
            )
            conn.execute(
                "UPDATE role SET nazwa='Administracja' WHERE id=4",
            )
            conn.commit()
        from app.security.authorization import is_administrator
        self.assertTrue(is_administrator(administrator))
        with self.assertRaises(PermissionError):
            self.call(
                'app.db.dao_employees.change_employee_password',
                {'pracownik_id':administrator,'new_password':'NewPassword123'},
                token=self.token,
            )

    def test_monthly_employee_filter_is_safe_for_authenticated_employees(self):
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic
        from app.remote.operations import OPERATIONS
        constructor = OPERATIONS[
            'app.monthly_view.monthly_view_logic.MonthlyViewLogic.__new__'
        ]
        filter_spec = OPERATIONS[
            'app.monthly_view.monthly_view_logic.MonthlyViewLogic.get_employee_filter_list'
        ]
        set_month_spec = OPERATIONS[
            'app.monthly_view.monthly_view_logic.MonthlyViewLogic.set_month'
        ]
        self.assertIn('tab:',constructor['capability'])
        self.assertEqual(filter_spec['capability'],'authenticated')
        self.assertEqual(set_month_spec['capability'],'authenticated')
        logic = object.__new__(MonthlyViewLogic)
        logic.pracownik_id = self.employee
        rows = logic.get_employee_filter_list()
        self.assertIn((self.employee,'Jan Kowalski'),rows)

    def test_legacy_employee_with_null_active_flag_is_listed(self):
        with get_conn() as conn:
            conn.execute(
                "UPDATE pracownicy SET aktywny=NULL WHERE id=%s",
                (self.employee,),
            )
            conn.commit()
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic
        logic = object.__new__(MonthlyViewLogic)
        logic.pracownik_id = self.employee
        rows = logic.get_employee_filter_list()
        self.assertIn((self.employee,'Jan Kowalski'),rows)

    def test_secrets_are_redacted_in_named_settings_mapping_and_database_rows(self):
        from app.db.dao_app_settings import set_setting
        from app.remote.secrets import PRESENT,public
        set_setting('google_client_secret','secret-on-server')
        set_setting('enova_connection',json.dumps({'server':'sql','password_enc':'ciphertext-on-server'}))
        self.assertEqual(self.call('app.db.dao_app_settings.get_setting',{'key':'google_client_secret'}),PRESENT)
        all_settings = self.call('app.db.dao_app_settings.list_settings',token=self.admin)
        self.assertEqual(all_settings['google_client_secret'],PRESENT)
        self.assertEqual(json.loads(all_settings['enova_connection'])['password_enc'],PRESENT)
        row = public(Row(['klucz','wartosc'],['google_client_secret','secret-on-server']))
        self.assertEqual((row['klucz'],row['wartosc']),('google_client_secret',PRESENT))

    def test_public_response_preserves_normal_strings_and_pricing_keys(self):
        from app.remote.secrets import public
        values = [
            'UZ_30',
            '2026-11-23T09:00',
            'KH_N0120.248.7',
            'Z importu faktur',
            '192.168.1.50',
            'ul. Kościuszki 12',
            '1234.56 PLN',
            '5260001246',
        ]
        self.assertEqual([public(value) for value in values], values)
        mapping = {
            'data_rozliczenia': '2026-11-23',
            'cena_netto': '1234.56',
            'adres_ip_serwera': '192.168.1.50',
            'nip': '5260001246',
            'nabywca_nip': '5260001246',
            'haslo_enc': 'ciphertext',
            'server_token_label': 'Numer podsystemu',
        }
        result = public(mapping)
        self.assertEqual(result['data_rozliczenia'], '2026-11-23')
        self.assertEqual(result['cena_netto'], '1234.56')
        self.assertEqual(result['adres_ip_serwera'], '192.168.1.50')
        self.assertEqual(result['nip'], '5260001246')
        self.assertEqual(result['nabywca_nip'], '5260001246')
        self.assertEqual(result['haslo_enc'], '[zapisane na serwerze]')
        self.assertEqual(result['server_token_label'], 'Numer podsystemu')

    def test_short_database_secret_does_not_redact_dates_in_diagnostics(self):
        from app.diagnostics import PrivacyFilter
        with mock.patch.dict('os.environ', {'DB_URL': 'postgresql://biuro:2026@localhost/biuro'}, clear=False):
            privacy = PrivacyFilter()
        self.assertEqual(privacy.redact('Data: 2026-11-23 09:00'), 'Data: 2026-11-23 09:00')
        self.assertEqual(
            privacy.redact('postgresql://biuro:2026@localhost/biuro'),
            'postgresql://biuro:[redacted]@localhost/biuro',
        )

    def test_timer_employee_filter_cannot_be_used_to_read_someone_elses_time(self):
        with self.assertRaises(PermissionError):
            self.call('app.db.dao_timers.list_employee_timers',{'employee_id':self.other,'actor_id':1})
        self.assertEqual(self.call('app.db.dao_timers.list_employee_timers',{'employee_id':self.employee,'actor_id':1}),[])

    def test_client_cannot_pass_a_raw_server_path_as_an_input_or_download_it(self):
        with self.assertRaises((ValueError,PermissionError)):
            self.call('app.reports.import_dao.import_work_file',{'path':'/etc/passwd'},token=self.admin)
        reference = self.service.files.put(auth.digest(self.token),'data.csv',b'a,b\n')
        with self.assertRaises(PermissionError):
            self.service.files.get(auth.digest(self.admin),reference)
        with self.assertRaises((ValueError,PermissionError)):
            self.service.files.get(auth.digest(self.token),{'file':'../../worker.env'})

    def test_desktop_business_read_uses_transport_and_blocks_direct_database_and_key_access(self):
        class Transport:
            def call(_,operation,args,kwargs):
                return self.call(operation,dict(zip(['firma_id'],args))|kwargs)
        from app.db.dao_companies import get_company
        from app.utils.env_config import ensure_encryption_key
        configure(Transport())
        self.assertEqual(get_company(self.company)['nazwa'],'Serwer')
        with self.assertRaises(PermissionError):
            get_conn()
        with self.assertRaises(PermissionError):
            ensure_encryption_key()

    def test_existing_secret_cannot_be_forwarded_to_a_new_sql_server(self):
        from app.db.dao_app_settings import set_setting
        from app.remote.secrets import PRESENT
        set_setting('enova_connection',json.dumps({'server':'trusted','username':'reader','password_enc':'stored'}))
        with self.assertRaises(ValueError):
            self.call('app.integrations.enova.connection_settings.check_connection',
                      {'config':{'server':'attacker','username':'reader','password_enc':PRESENT}},token=self.admin)

    def test_an_unauthorized_account_snapshot_is_rejected_before_reading_mail(self):
        with mock.patch('app.email.email_storage.get_email_accounts_for_employee',return_value=[]):
            with self.assertRaises(PermissionError):
                self.call('app.remote.screen_data.mail_view_snapshot',{'konto_id':999,'folder':'INBOX'})

    def test_raw_setting_changes_cannot_redirect_saved_credentials(self):
        for key in ('saldeo_web','enova_connection','caldav_url','google_client_secret'):
            with self.assertRaises(PermissionError):
                self.call('app.db.dao_app_settings.set_setting',{'key':key,'value':'attacker'},token=self.admin)

    def test_smtp_test_restores_credentials_only_for_unchanged_saved_account(self):
        from app.remote.secrets import PRESENT
        account = {'id':50,'email':'mail@example.com','serwer_imap':'imap.example.com','port_imap':993,
                   'ssl_imap':'SSL/TLS','serwer_smtp':'smtp.example.com','port_smtp':465,'ssl_smtp':'SSL/TLS','haslo_enc':'private-ciphertext'}
        def check_connection(account,timeout=15):
            self.assertEqual(account['haslo_enc'],'private-ciphertext')
            return True,'OK'
        # The service restores the secret internally; no plaintext or ciphertext is returned.
        with mock.patch('app.email.email_storage.get_account_info',return_value=account), \
                mock.patch('app.email.email_connection.check_smtp',new=check_connection):
            redacted = dict(account,haslo_enc=PRESENT)
            self.assertEqual(self.call('app.email.email_connection.check_smtp',{'account':redacted},token=self.admin),(True,'OK'))
            with self.assertRaises(ValueError):
                self.call('app.email.email_connection.check_smtp',{'account':dict(redacted,serwer_smtp='attacker')},token=self.admin)

    def test_bundled_queries_remain_server_defaults_instead_of_private_copies(self):
        from app.db.dao_app_settings import set_setting
        from app.integrations.enova.connection_settings import load_connection
        from app.integrations.enova.query_defaults import saved_source
        set_setting('enova_connection',json.dumps({'server':'trusted','username':'reader','password_enc':'saved',
                    'sources':[{'database':'company','nip':'1234563218'}]}))
        config = load_connection()
        def check_connection(config,*,password=''):
            self.assertIn('payroll_query',config['sources'][0]['_query_defaults'])
            self.assertNotIn('payroll_query',saved_source(config['sources'][0]))
            return []
        with mock.patch('app.integrations.enova.connection_settings.check_connection',new=check_connection):
            self.assertEqual(self.call('app.integrations.enova.connection_settings.check_connection',{'config':config},token=self.admin),[])

    def test_calendar_secret_failure_cannot_publish_a_new_url_with_an_old_password(self):
        from app.kalendarz import caldav_sync
        caldav_sync.save_config('https://trusted.example/','reader','old-password')
        with mock.patch('app.kalendarz.caldav_sync.encrypt_password',side_effect=ValueError('Unable to encrypt')):
            with self.assertRaises(ValueError):
                self.call('app.kalendarz.caldav_sync.save_config',
                          {'url':'https://new.example/','login':'new-reader','password':'new-password'},token=self.admin)
        config = caldav_sync.get_config()
        self.assertEqual((config['url'],config['login'],config['haslo']),('https://trusted.example/','reader','old-password'))

    def test_calendar_snapshot_uses_authenticated_actor_and_contains_batch_sharing_flags(self):
        from app.db.dao_calendar import save_event
        own = save_event(self.employee,{'tytul':'Own','start':'2026-11-05T10:00','koniec':'2026-11-05T11:00'})
        private = save_event(self.other,{'tytul':'Private','start':'2026-11-05T10:00','koniec':'2026-11-05T11:00'})
        with get_conn() as conn:
            conn.execute('INSERT INTO wydarzenia_dostep(wydarzenie_id,pracownik_id) VALUES (%s,%s)',(own,self.other))
        with mock.patch('app.kalendarz.google_sync.is_connected',return_value=False), \
             mock.patch('app.kalendarz.caldav_sync.is_configured',return_value=False):
            snapshot = self.call('app.remote.screen_data.calendar_view_snapshot',
                {'actor_id':self.other,'start':'2026-11-01','end':'2026-12-01'})
        events = {row['id']:row for row in snapshot['events']}
        self.assertIn(own,events)
        self.assertNotIn(private,events)
        self.assertTrue(events[own]['ma_udostepnienia'])
        self.assertEqual(snapshot['sources'],[('Lokalne (import ICS)','lokalne')])

    def test_mail_sidebar_reads_only_the_actors_accounts_without_per_account_reads(self):
        from app.email.email_storage import save_folder
        with get_conn() as conn:
            conn.execute("INSERT INTO konta_email(id,email) VALUES (91,'own@example.com'),(92,'private@example.com')")
        own_folder = save_folder(91,'INBOX')
        save_folder(92,'Secret')
        with mock.patch('app.email.email_storage.get_email_accounts_for_employee',return_value=[{'id':91,'email':'own@example.com'}]) as accounts, \
             mock.patch('app.email.email_storage.get_folders_for_account',side_effect=AssertionError('per-account folders')), \
             mock.patch('app.email.email_storage.get_unread_counts',side_effect=AssertionError('per-account counts')):
            snapshot = self.call('app.remote.screen_data.mail_sidebar_snapshot',{'actor_id':self.other})
        accounts.assert_called_with(self.employee)
        self.assertEqual(set(snapshot['folders']),{91})
        self.assertEqual(snapshot['folders'][91][0]['id'],own_folder)
        self.assertNotIn(92,snapshot['folders'])

    def test_client_calendar_data_cannot_choose_someone_elses_remote_identity(self):
        from app.db.dao_calendar import get_event
        event = self.call('app.db.dao_calendar.save_event',{'pracownik_id':1,'data':{
            'tytul':'Moje wydarzenie','start':'2026-10-01T10:00:00','koniec':'2026-10-01T11:00:00',
            'google_event_id':'another-users-event','caldav_uid':'another-users-uid','zrodlo':'google'}})
        stored = get_event(event)
        self.assertEqual(stored['pracownik_id'],self.employee)
        self.assertIsNone(stored['google_event_id'])
        self.assertIsNone(stored['caldav_uid'])
        self.assertEqual(stored['zrodlo'],'lokalne')
        with self.assertRaises(PermissionError):
            self.call('app.db.dao_calendar.set_google_event_id',{'event_id':event,'google_event_id':'injected'})

    def test_user_management_permission_cannot_take_over_an_administrator_account(self):
        with get_conn() as conn:
            role = conn.execute("INSERT INTO role(nazwa,can_manage_users) VALUES ('Accounts manager',1) RETURNING id").fetchone()[0]
            conn.execute('UPDATE pracownicy SET rola_id=%s WHERE id=%s',(role,self.employee))
        with self.assertRaises(PermissionError):
            self.call('app.db.dao_employees.change_employee_password',{'pracownik_id':1,'new_password':'Takeover12345'})
        # Active status must not weaken the protection of a privileged account.
        with get_conn() as conn:
            conn.execute('UPDATE pracownicy SET aktywny=0 WHERE id=1')
        with self.assertRaises(PermissionError):
            self.call('app.db.dao_employees.change_employee_password',{'pracownik_id':1,'new_password':'Takeover12345'})
        self.call('app.db.dao_employees.change_employee_password',{'pracownik_id':self.other,'new_password':'Changed12345'})
        self.assertEqual(auth.login('other@example.com','Changed12345','manager-test')['employee_id'],self.other)
