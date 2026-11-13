from unittest import TestCase
from unittest.mock import Mock,patch

from app.integrations.platnik.holidays import connect,check_connection,_import_year,PlatnikSqlError


class PlatnikConnectionTests(TestCase):
    def setUp(self):
        self.password = 'private;p}ass'
        self.config = {'server':r'PLATNIK-SRV\SQLEXPRESS','database':'platnik','username':'sa',
                       'driver':'ODBC Driver 17 for SQL Server','password_enc':'encrypted-secret',
                       'trust_server_certificate':True,'enabled':True}
        self.decrypt = patch('app.integrations.platnik.holidays.decrypt_password',return_value=self.password)
        self.decrypt.start()
        self.addCleanup(self.decrypt.stop)

    def test_login_failure_exposes_state_and_installed_driver_without_credentials(self):
        odbc = Mock()
        odbc.drivers.return_value = [self.config['driver']]
        odbc.connect.side_effect = Exception('28000',
            'Login failed (18456); PWD={private;p}}ass}; '+self.password+' encrypted-secret')
        with patch('app.integrations.platnik.holidays.load_odbc',return_value=odbc),self.assertRaises(PlatnikSqlError) as caught:
            connect(self.config)
        error = caught.exception
        self.assertEqual(error.diagnostic['sqlstate'],'28000')
        self.assertEqual(error.diagnostic['driver'],self.config['driver'])
        self.assertIn('logowanie',error.diagnostic['hint'])
        self.assertIn('18456',error.diagnostic['details'])
        self.assertNotIn(self.password,str(error.diagnostic))
        self.assertNotIn('private;p}}ass',str(error.diagnostic))
        self.assertNotIn('encrypted-secret',str(error.diagnostic))
        self.assertNotIn(self.password,str(error))

    def test_connection_test_retains_query_error_and_closes_connection(self):
        connection = Mock()
        connection.cursor.return_value.execute.side_effect = Exception('08001','SSL Provider: certificate chain not trusted')
        with patch('app.integrations.platnik.holidays.connect',return_value=connection),self.assertRaises(PlatnikSqlError) as caught:
            check_connection(self.config)
        self.assertEqual(caught.exception.diagnostic['sqlstate'],'08001')
        self.assertIn('certyfikatu',caught.exception.diagnostic['hint'])
        self.assertEqual(caught.exception.diagnostic['operation'],'Sprawdzenie bazy Płatnika')
        connection.close.assert_called_once()

    def test_query_failure_preserves_previous_snapshot_and_reports_import_phase(self):
        connection = Mock()
        connection.cursor.return_value.execute.side_effect = Exception('42S02',"Invalid object name 'dbo.RWS_ZWOLNIENIE'")
        with patch('app.integrations.platnik.holidays.load',return_value=self.config), \
             patch('app.integrations.platnik.holidays.connect',return_value=connection), \
             patch('app.integrations.platnik.holidays.set_setting') as save, \
             self.assertRaises(PlatnikSqlError) as caught:
            _import_year(2026)
        self.assertEqual(caught.exception.diagnostic['sqlstate'],'42S02')
        self.assertIn('2026',caught.exception.diagnostic['operation'])
        self.assertIn('RWS_ZWOLNIENIE',caught.exception.diagnostic['details'])
        save.assert_not_called()
        connection.close.assert_called_once()

    def test_missing_password_is_explained_without_trying_a_database_connection(self):
        with patch('app.integrations.platnik.holidays.load_odbc') as load_odbc,self.assertRaisesRegex(ValueError,'hasło SQL'):
            connect({**self.config,'password_enc':''})
        load_odbc.assert_not_called()

    def test_worker_panel_offers_password_redacted_details(self):
        from app.admin.settings.platnik_settings import PlatnikSettings
        # Share the suite's QApplication without needing a database for this UI case.
        from tests.base import QT_APP
        self.assertIsNotNone(QT_APP)
        with patch('app.security.authorization.is_administrator',return_value=True), \
             patch('app.admin.settings.platnik_settings.load',return_value=self.config):
            panel = PlatnikSettings(1)
        self.addCleanup(panel.close)
        error = PlatnikSqlError({'sqlstate':'28000','hint':'Sprawdź login.','details':'Login failed (18456)'},
                               self.config,'Połączenie z SQL Płatnika')
        panel.failed(error)
        self.assertFalse(panel.error_button.isHidden())
        self.assertIn('28000',panel.status.text())
        captured = []
        def show(box):
            captured.append(box.detailedText())
            return 0
        with patch('app.admin.settings.platnik_settings.QMessageBox.exec',show):
            panel.error_button.click()
        self.assertIn('18456',captured[0])
        self.assertIn(self.config['server'],captured[0])
        self.assertNotIn(self.password,captured[0])
        panel.checked(None)
        self.assertTrue(panel.error_button.isHidden())
