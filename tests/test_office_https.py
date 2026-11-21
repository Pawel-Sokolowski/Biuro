"""Actual TLS desktop-to-service round trips; only executed on CI."""
from datetime import datetime,timedelta,timezone
from ipaddress import ip_address
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tests.base import DbTestCase
from app.remote.client import OfficeClient
from app.remote.http_server import create_server


class OfficeHttpsTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Biuro CI')])
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ip_address('127.0.0.1'))]),critical=False)
            .sign(key,hashes.SHA256()))
        self.cert = root/'certificate.pem'
        private = root/'tls.pem'
        self.cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        self.server = create_server('127.0.0.1',0,str(self.cert),str(private),root)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.client = OfficeClient('https://127.0.0.1:'+str(self.server.server_address[1]),verify=str(self.cert))

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.server.office_service.close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()
        super().tearDown()

    def test_login_read_export_and_logout_never_download_an_office_key(self):
        self.assertEqual(self.client.login('admin@biuro.pl','Admin12345'),1)
        company = self.make_company('HTTPS firma')
        result = self.client.call('app.db.dao_companies.get_company',(company,))
        self.assertEqual(result['nazwa'],'HTTPS firma')
        from app.companies import holiday_eligibility
        values = {**dict.fromkeys(holiday_eligibility.CONDITIONS,True),
                  'insurance_code':'051000','insured_count':1}
        payer = self.make_company('Wakacje HTTPS',nip='1112223344',adres_email='owner@example.com',forma_dzialalnosci='JDG')
        with mock.patch.object(holiday_eligibility,'current_month',return_value='2026-11'):
            saved = self.client.call('app.companies.holiday_eligibility.save_conditions',
                                     (999,payer,'2026-12','company',values,0))
            self.assertEqual(saved['revision'],1)
            conditions = self.client.call('app.companies.holiday_eligibility.read_conditions',(999,payer,'2026-12'))
            self.assertEqual(conditions['people'][0]['values']['actor_id'],1)
            candidates = self.client.call('app.companies.holiday_eligibility.list_candidates',(999,'2026-12'))
            self.assertEqual(next(c for c in candidates['companies'] if c['id']==payer)['holiday_addresses']['eligible'],
                             'owner@example.com')
        raw = self.client.call('app.db.dao_app_settings.get_setting',(f'zus_holiday_mailing_2026-12_{payer}',))
        self.assertNotIn('gAAAAA',raw)
        self.assertNotIn('051000',raw)
        session = self.client._request('session')
        self.assertEqual(set(session),{'employee_id','version','ui_authorization','ui_features'})
        self.assertEqual(set(session['ui_authorization']),{'administrator','permissions','tabs'})
        self.assertTrue(all(isinstance(value,bool) for value in session['ui_authorization']['permissions'].values()))
        target = Path(self.temporary.name)/'companies.csv'
        self.client.call('app.db.dao_companies.export_companies_csv',(str(target),))
        self.assertIn('HTTPS firma',target.read_text(encoding='utf-8-sig'))
        token = self.client.token
        self.client.logout()
        with self.assertRaises(PermissionError):
            self.client.resume(token)

    def test_mt940_upload_imports_12_transactions_into_the_shared_database(self):
        from app.db.dao_conn import get_conn
        from tests.test_payment_safety import statement

        self.client.login('admin@biuro.pl', 'Admin12345')
        source = Path(self.temporary.name) / 'statement.sta'
        source.write_bytes(b''.join(statement(str(idx)) for idx in range(1, 13)))
        operation = 'app.db.dao_payments.import_mt940_file'
        self.assertEqual(self.client.call(operation, (str(source), 1)),
                         {'all': 12, 'new': 12, 'duplicates': 0, 'own': 0})
        self.assertEqual(self.client.call(operation, (str(source), 1)),
                         {'all': 12, 'new': 0, 'duplicates': 12, 'own': 0})
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM bank_transakcje').fetchone()[0], 12)

    def test_company_form_initial_read_is_batched_off_the_gui_thread(self):
        from app.companies.company_form import CompanyForm
        from tests.test_review_optimizations import wait_until

        self.client.login('admin@biuro.pl', 'Admin12345')
        company = self.make_company('Karta HTTPS')
        requests = []
        request = self.client._request
        gui_thread = threading.get_ident()

        def trace(path, payload=None, **kwargs):
            requests.append((path, payload, threading.get_ident()))
            return request(path, payload, **kwargs)

        with mock.patch('app.remote.state._client', self.client), \
                mock.patch.object(self.client, '_request', side_effect=trace):
            form = CompanyForm(company, actor_id=1)
            self.addCleanup(form.close)
            self.assertTrue(wait_until(lambda: 'nazwa' in form.inputs))
            self.assertEqual(form.inputs['nazwa'].text(), 'Karta HTTPS')
            self.assertEqual([p['operation'] for _, p, _ in requests],
                             ['app.companies.form_data.company_form_snapshot'])
            self.assertNotEqual(requests[0][2], gui_thread)
            form.section_tabs.setCurrentWidget(form.section_pages['Notatki'])
            self.assertTrue(wait_until(lambda: 'Notatki' in form._card_tabs))
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(thread != gui_thread for _, _, thread in requests))

    def test_finished_file_transfers_release_session_slots(self):
        self.client.login('admin@biuro.pl', 'Admin12345')
        server_files = self.server.office_service.files
        target = Path(self.temporary.name) / 'companies.xlsx'
        from app.remote.auth import digest
        for _ in range(102):
            target.write_bytes(b'old content')
            reference = self.client._file(target, 'output')
            server_file = Path(server_files.path(digest(self.client.token), reference))
            server_file.write_bytes(b'export')
            self.client._request('download', reference)
            self.client.release_files([reference])
        self.assertFalse(server_files.items)
        target = target.with_suffix('.csv')
        self.client.call('app.db.dao_companies.export_companies_csv', (str(target),))
        self.assertTrue(target.exists())
        self.assertFalse(server_files.items)

    def test_short_qt_jobs_share_existing_https_connections(self):
        from PyQt6.QtCore import QObject
        from app.utils.background import TaskRunner
        from tests.test_review_optimizations import wait_until

        self.client.login('admin@biuro.pl', 'Admin12345')
        company = self.make_company('Połączenie wielokrotnego użycia')
        owner = QObject()
        runner = TaskRunner(owner)
        results, errors = [], []

        def submit():
            runner.submit(lambda: self.client.call('app.db.dao_companies.get_company', (company,)),
                          completed, errors.append)

        def completed(row):
            results.append(row)
            if len(results) < 80:
                submit()

        with mock.patch.object(self.client, '_new_session', wraps=self.client._new_session) as connect:
            submit()
            self.assertTrue(wait_until(lambda: len(results) == 80 or errors))
            self.assertFalse(errors)
            self.assertLess(connect.call_count, 4)
            self.assertTrue(all(row['id'] == company for row in results))

    def test_saldeo_file_export_requires_permission_beyond_invoice_tab(self):
        from app.db.dao_conn import get_conn
        employee = self.make_employee('invoices@example.test', haslo='Haslo12345')
        with get_conn() as conn:
            role = conn.execute("INSERT INTO role(nazwa,zakladki,can_export_data) VALUES ('Eksport test','Faktury',0) RETURNING id").fetchone()[0]
            conn.execute('UPDATE pracownicy SET rola_id=%s WHERE id=%s', (role, employee))
        self.client.login('invoices@example.test', 'Haslo12345')
        self.assertIn('Faktury', self.client._ui_authorization['tabs'])
        target = Path(self.temporary.name) / 'forbidden.xlsx'
        with self.assertRaises(PermissionError):
            self.client.call('app.utils.saldeo_exporter.export_saldeo_xlsx', ([], str(target)))
        self.assertFalse(target.exists())
        self.assertFalse(self.server.office_service.files.items)

    def test_company_card_mail_section_preserves_mail_module_permissions(self):
        from app.db.dao_conn import get_conn
        employee = self.make_employee('companies@example.test', haslo='Haslo12345')
        company = self.make_company('Kartoteka pracownika')
        with get_conn() as conn:
            role = conn.execute("INSERT INTO role(nazwa,zakladki) VALUES ('Kartoteka test','Klienci') RETURNING id").fetchone()[0]
            conn.execute('UPDATE pracownicy SET rola_id=%s WHERE id=%s', (role, employee))
            conn.execute("INSERT INTO konta_email(id,email) VALUES (10,'assigned@example.test')")
            conn.execute('INSERT INTO pracownik_konto_email(pracownik_id,konto_id) VALUES (%s,10)', (employee,))
        self.client.login('companies@example.test', 'Haslo12345')
        operation = 'app.companies.form_data.company_form_snapshot'
        self.assertEqual(self.client.call(operation, (company, employee, 'mail')),
                         {'accounts': [], 'filters': [], 'choices': {'senders': [], 'folders': []}})
        with self.assertRaises(PermissionError):
            self.client.call(operation, (company, employee, 'mail_choices'), {'account_id': 10})

    def test_bulk_campaign_history_and_idempotent_smtp_through_https(self):
        from app.db.dao_conn import get_conn
        from app.email.bulk_mail import BulkMailPlan, BulkMailSender
        from app.services import bulk_mail_log
        from tests.test_review_optimizations import wait_until

        self.client.login('admin@biuro.pl', 'Admin12345')
        company = self.make_company('Wysyłka HTTPS', adres_email='client@example.test')
        with get_conn() as conn:
            conn.execute("INSERT INTO konta_email(id,email) VALUES (10,'office@example.test')")
            conn.execute('INSERT INTO pracownik_konto_email(pracownik_id,konto_id) VALUES (1,10)')
        notice = Path(self.temporary.name) / 'notice.txt'
        notice.write_text('Treść załącznika', encoding='utf-8')
        plan = BulkMailPlan(999, 10, 'office@example.test', 'Temat', 'Treść',
                            ((company, ('client@example.test',)),), attachments=(str(notice),))
        with mock.patch('app.remote.state._client', self.client), \
                mock.patch('app.email.bulk_mail.send_email_smtp', return_value=(True, 'OK')) as smtp:
            sender = BulkMailSender()
            results = []
            sender.finished.connect(results.append)
            sender.start(plan)
            self.assertTrue(wait_until(lambda: bool(results)))
            self.assertFalse(results[0]['error'])
            history = bulk_mail_log.history()
            self.assertEqual(history[0]['actor_id'], 1)
            self.assertEqual(history[0]['remaining'], 0)
            self.assertEqual(history[0]['attachments'], ['notice.txt'])
            self.assertEqual(history[0]['batches'][0]['status'], 'sent')
            self.assertFalse(self.server.office_service.files.items)
            # Resubmit a completed campaign batch while retaining the original attachment identity.
            from dataclasses import replace
            refs = self.client.upload_campaign_attachments(plan.attachments)
            try:
                result = bulk_mail_log.send_campaign_batch(
                    history[0]['id'], replace(plan, attachments=refs), 0)
                self.assertTrue(result['ok'])
            finally:
                self.client.release_files(refs)
            smtp.assert_called_once()
            self.assertEqual(Path(smtp.call_args.kwargs['attachments'][0]).name, 'notice.txt')
            self.assertTrue(smtp.call_args.kwargs['require_attachments'])

    def test_selected_invoice_details_for_1000_records_use_one_https_request(self):
        from app.db.dao_conn import get_conn
        from app.remote.view_reads import invoice_selection

        self.client.login('admin@biuro.pl', 'Admin12345')
        self.assertIn('invoice_selection', self.client.server_features)
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO faktury (miesiac, kwota_netto, kwota_brutto, numer, pozycje_json)
                SELECT '2026-11', 100, 123, 'FV/' || n, '[{"nazwa":"Usługa"}]'
                FROM generate_series(1, 1000) AS n
            """)
            ids = [row[0] for row in conn.execute("SELECT id FROM faktury ORDER BY id DESC")]
        with mock.patch('app.remote.state._client', self.client), \
                mock.patch.object(self.client, '_request', wraps=self.client._request) as request:
            selected = invoice_selection([*ids, -1], 1, details=True)
        self.assertEqual(request.call_count, 1)
        self.assertEqual([row['id'] for row in selected], ids)
        self.assertEqual(selected[0]['numer'], 'FV/1000')
        self.assertEqual(json.loads(selected[0]['pozycje_json']), [{'nazwa': 'Usługa'}])

    def test_enova_ezla_endpoint_authenticates_before_releasing_company_xml(self):
        import base64
        import hashlib
        import json
        import requests
        from tests.test_ezla_report import report, certificate
        from app.db.dao_app_settings import set_setting
        from app.utils.crypto import encrypt_password
        company = self.make_company('Enova HTTPS firma', nip='1112223344')
        raw = report(certificate())
        set_setting(f'ezla_report_{company}_' + hashlib.sha256(raw).hexdigest(), json.dumps({
            'months':['2018-04'], 'needs_review':False,
            'xml_enc':encrypt_password(base64.b64encode(raw).decode('ascii'))}))
        url = self.client.server + '/api/v1/enova/ezla'
        payload = {'company_nip':'1112223344','month':'2018-04'}
        response = requests.post(url,json=payload,verify=str(self.cert),timeout=10)
        self.assertEqual(response.status_code,401)
        self.client.login('admin@biuro.pl','Admin12345')
        result = self.client._request('enova/ezla',payload)
        self.assertEqual(base64.b64decode(result['reports'][0]['xml']),raw)
        self.assertNotIn('xml_enc',str(result))

    def test_company_invoice_settings_and_monthly_logic_share_server_owned_state(self):
        from app.companies.company_logic import CompanyLogic
        from app.invoices.faktury_logic import FakturyLogic
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic
        from app.admin.global_settings_logic import GlobalSettingsLogic
        from app.remote.boundary import remote_class
        from app.remote.state import configure
        company = self.make_company('Proxy firma')
        self.client.login('admin@biuro.pl','Admin12345')
        configure(self.client)
        try:
            for cls in (CompanyLogic,FakturyLogic,MonthlyViewLogic,GlobalSettingsLogic):
                original = getattr(cls,'__office_original__',cls)
                proxy = remote_class(original.__module__+'.'+original.__name__)(original)
                with mock.patch('app.utils.ceidg_suspension.run_in_background'):
                    value = proxy(1) if cls.__name__=='MonthlyViewLogic' else proxy()
                if cls.__name__=='CompanyLogic':
                    self.assertEqual(value.get_company(company)['nazwa'],'Proxy firma')
                elif cls.__name__=='FakturyLogic':
                    self.assertEqual(value._get_company(company)['nazwa'],'Proxy firma')
                elif cls.__name__=='GlobalSettingsLogic':
                    self.assertTrue(value.get_roles())
                else:
                    value.set_month('2026-10')
                    self.assertEqual(value.aktualny_miesiac,'2026-10')
                    value.start_general_timer({})
                    self.assertTrue(value.is_general_timer_running())
                    self.assertTrue(value.timer_checkpoints())
                    value.stop_all_timers()
                    self.assertFalse(value.is_general_timer_running())
        finally:
            configure(None)

    def test_cleartext_and_untrusted_certificates_are_not_accepted(self):
        with self.assertRaises(ValueError):
            OfficeClient('http://127.0.0.1')
        with self.assertRaises(ValueError):
            OfficeClient('https://127.0.0.1',verify=False)
        other = OfficeClient(self.client.server)
        with self.assertRaises(ValueError):
            other.login('admin@biuro.pl','Admin12345')

    def test_login_ui_permissions_do_not_add_rpcs_but_data_still_checks_revocation(self):
        actor = self.client.login('admin@biuro.pl','Admin12345')
        with mock.patch.object(self.client,'_request',side_effect=AssertionError('UI permission RPC')):
            for _ in range(20):
                self.assertTrue(self.client.call('app.security.authorization.is_administrator',(actor,)))
                self.assertIn('Pulpit',self.client.call('app.security.authorization.allowed_tabs',(actor,)))
                self.client.call('app.security.authorization.get_permissions',(actor,))
        self.server.office_service.forget(self.client.token)
        with self.assertRaises(PermissionError):
            self.client.call('app.db.dao_employees.list_employees')
        self.assertIsNone(self.client._ui_authorization)

    def test_changed_role_overrides_the_clients_login_time_ui_hint(self):
        from app.db.dao_conn import get_conn
        regular = self.make_employee('limited-ui@example.com')
        actor = self.client.login('admin@biuro.pl','Admin12345')
        with get_conn() as conn:
            conn.execute('UPDATE pracownicy SET is_admin=0,rola_id=(SELECT rola_id FROM pracownicy WHERE id=%s) WHERE id=%s',
                         (regular,actor))
        self.assertTrue(self.client.call('app.security.authorization.is_administrator',(actor,)))
        with self.assertRaises(PermissionError):
            self.client.call('app.db.dao_app_settings.set_setting',(),{'key':'office_name','value':'denied'})

    def test_input_files_are_uploaded_and_output_paths_are_local(self):
        self.client.login('admin@biuro.pl','Admin12345')
        self.make_company('Przesylka')
        target = Path(self.temporary.name)/'saved.csv'
        self.client.call('app.db.dao_companies.export_companies_csv',(str(target),))
        self.assertTrue(target.exists())
        self.assertFalse(any(name in target.read_text(encoding='utf-8-sig') for name in ('BIO_ENCRYPTION_KEY','password_stamp')))
        source = Path(self.temporary.name)/'source.csv'
        source.write_bytes(b'uploaded content')
        def consume(path):
            self.assertNotEqual(str(source),str(path))
            Path(path).resolve().relative_to(self.server.office_service.files.root)
            return {'content':Path(path).read_bytes()}
        with mock.patch('app.reports.import_dao.import_work_file',new=consume):
            result = self.client.call('app.reports.import_dao.import_work_file',(str(source),))
        self.assertEqual(result,{'content':b'uploaded content'})

    def test_invalid_login_and_unknown_route_do_not_include_internal_errors(self):
        with self.assertRaises(PermissionError):
            self.client.login('admin@biuro.pl','Incorrect123')
        self.client.login('admin@biuro.pl','Admin12345')
        with self.assertRaises(ValueError):
            self.client._request('keys')

    def test_z_desktop_starts_without_database_credentials_or_office_key(self):
        import os,subprocess,sys
        script = r"""
import os,sys,faulthandler
faulthandler.dump_traceback_later(25,repeat=True)
from PyQt6.QtWidgets import QApplication,QMessageBox
from PyQt6.QtCore import QTimer,QThreadPool
from app.remote.client import OfficeClient
from app.remote.state import configure
assert 'DB_URL' not in os.environ
assert 'BIO_ENCRYPTION_KEY' not in os.environ
application = QApplication(['biuro-keyless-ci'])
def unexpected_dialog(parent,title,text,*args,**kwargs):
    raise AssertionError(str(title)+': '+str(text))
for method in ('warning','critical','information'):
    setattr(QMessageBox,method,unexpected_dialog)
connection = OfficeClient(sys.argv[1],verify=sys.argv[2])
connection.login('admin@biuro.pl','Admin12345')
configure(connection)
print('KEYLESS_LOGGED_IN',flush=True)
from app.main_ui import OfficeManagerApp
print('KEYLESS_MODULES_LOADED',flush=True)
window = OfficeManagerApp(1)
print('KEYLESS_WINDOW_CREATED',flush=True)
assert window.btn_employee_timers.text() == 'Mój czas'
assert window.page_monthly.logic.__dict__.get('_office_object')
assert not hasattr(window,'key')
QTimer.singleShot(1500,application.quit)
application.exec()
window.close()
QThreadPool.globalInstance().waitForDone(10000)
connection.close()
print('KEYLESS_DESKTOP_READY')
"""
        env = dict(os.environ,BIO_CLIENT_MODE='1',QT_QPA_PLATFORM='offscreen')
        env.pop('DB_URL',None)
        env.pop('BIO_ENCRYPTION_KEY',None)
        try:
            result = subprocess.run([sys.executable,'-c',script,self.client.server,str(self.cert)],
                                    env=env,text=True,capture_output=True,timeout=70)
        except subprocess.TimeoutExpired as error:
            output = (error.stdout or b'') + (error.stderr or b'')
            self.fail(output.decode('utf-8',errors='replace') if isinstance(output,bytes) else output)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('KEYLESS_DESKTOP_READY',result.stdout)

    def test_unauthenticated_upload_is_rejected_before_waiting_for_its_body(self):
        import http.client,ssl
        context = ssl.create_default_context(cafile=str(self.cert))
        connection = http.client.HTTPSConnection('127.0.0.1',self.server.server_address[1],context=context,timeout=3)
        try:
            connection.putrequest('POST','/api/v1/files')
            connection.putheader('Content-Type','application/json')
            connection.putheader('Content-Length',str(32*1024*1024))
            connection.endheaders()
            # Do not transmit a body: the server must respond immediately.
            response = connection.getresponse()
            self.assertEqual(response.status,401)
            response.read()
        finally:
            connection.close()
