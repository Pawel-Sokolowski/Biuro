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
        def check_connection(value,timeout=15):
            self.assertEqual(value['haslo_enc'],'private-ciphertext')
            return True,'OK'
        # The service restores the secret internally; no plaintext or ciphertext is returned.
        with mock.patch('app.email.email_storage.get_account_info',return_value=account), \
                mock.patch('app.email.email_connection.check_smtp',new=check_connection):
            redacted = dict(account,haslo_enc=PRESENT)
            self.assertEqual(self.call('app.email.email_connection.check_smtp',{'account':redacted},token=self.admin),(True,'OK'))
            with self.assertRaises(ValueError):
                self.call('app.email.email_connection.check_smtp',{'account':dict(redacted,serwer_smtp='attacker')},token=self.admin)
