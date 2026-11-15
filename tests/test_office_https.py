"""Actual TLS desktop-to-service round trips; only executed on CI."""
from datetime import datetime,timedelta,timezone
from ipaddress import ip_address
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
        session = self.client._request('session')
        self.assertEqual(set(session),{'employee_id','version'})
        target = Path(self.temporary.name)/'companies.csv'
        self.client.call('app.db.dao_companies.export_companies_csv',(str(target),))
        self.assertIn('HTTPS firma',target.read_text(encoding='utf-8-sig'))
        token = self.client.token
        self.client.logout()
        with self.assertRaises(PermissionError):
            self.client.resume(token)

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
import os,sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer,QThreadPool
from app.remote.client import OfficeClient
from app.remote.state import configure
assert 'DB_URL' not in os.environ
assert 'BIO_ENCRYPTION_KEY' not in os.environ
application = QApplication(['biuro-keyless-ci'])
connection = OfficeClient(sys.argv[1],verify=sys.argv[2])
connection.login('admin@biuro.pl','Admin12345')
configure(connection)
from app.main_ui import OfficeManagerApp
window = OfficeManagerApp(1)
assert window.btn_employee_timers.text() == 'Mój czas'
assert window.page_monthly.logic.__dict__.get('_office_object')
assert not hasattr(window,'key')
QTimer.singleShot(1500,application.quit)
application.exec()
window.close()
QThreadPool.globalInstance().waitForDone(10000)
print('KEYLESS_DESKTOP_READY')
"""
        env = dict(os.environ,BIO_CLIENT_MODE='1',QT_QPA_PLATFORM='offscreen')
        env.pop('DB_URL',None)
        env.pop('BIO_ENCRYPTION_KEY',None)
        result = subprocess.run([sys.executable,'-c',script,self.client.server,str(self.cert)],
                                env=env,text=True,capture_output=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('KEYLESS_DESKTOP_READY',result.stdout)
