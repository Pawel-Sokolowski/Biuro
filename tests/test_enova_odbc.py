"""Desktop driver detection and server-only tests; no real network connections."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from app.integrations.enova.odbc_support import load_odbc, resolve_driver, SUPPORTED_DRIVERS
from app.integrations.enova.connection_settings import check_connection


class OdbcSupportTests(TestCase):
    def test_auto_prefers_18_and_reuses_installed_17(self):
        self.assertEqual(resolve_driver({}, SimpleNamespace(drivers=lambda: list(SUPPORTED_DRIVERS))), SUPPORTED_DRIVERS[0])
        self.assertEqual(resolve_driver({'driver': 'auto'}, SimpleNamespace(drivers=lambda: [SUPPORTED_DRIVERS[1]])), SUPPORTED_DRIVERS[1])

    def test_explicit_driver_does_not_fall_back_and_missing_driver_is_clear(self):
        installed = SimpleNamespace(drivers=lambda: [SUPPORTED_DRIVERS[1]])
        with self.assertRaisesRegex(ValueError, 'nie zainstalowano'):
            resolve_driver({'driver': SUPPORTED_DRIVERS[0]}, installed)
        with self.assertRaisesRegex(ValueError, 'architektur'):
            resolve_driver({}, SimpleNamespace(drivers=lambda: []))

    def test_packaged_missing_module_message_does_not_request_requirements_install(self):
        with patch.dict('sys.modules', {'pyodbc': None}), patch('sys.frozen', True, create=True):
            with self.assertRaisesRegex(ValueError, 'aktualną wersję Biuro') as caught:
                load_odbc()
            self.assertNotIn('requirements', str(caught.exception))

    def test_server_only_test_runs_on_desktop_without_company_mappings(self):
        connection = Mock()
        connection.cursor.return_value.fetchone.return_value = ('master',)
        driver = SimpleNamespace(drivers=lambda: [SUPPORTED_DRIVERS[1]], connect=Mock(return_value=connection))
        with patch.dict('sys.modules', {'pyodbc': driver}):
            result = check_connection({'server': r'192.168.1.16\ENOVA', 'username': 'reader', 'sources': []}, password='secret')
        self.assertTrue(result[0]['ok'])
        self.assertIn('DRIVER={ODBC Driver 17 for SQL Server}', driver.connect.call_args.args[0])
        self.assertNotIn('DATABASE=', driver.connect.call_args.args[0])
        self.assertIn('Encrypt={yes}', driver.connect.call_args.args[0])
        self.assertIn('TrustServerCertificate={no}', driver.connect.call_args.args[0])
        connection.close.assert_called_once()

    def test_named_instance_requires_backslash(self):
        from app.integrations.enova.odbc_support import validate_server
        self.assertEqual(validate_server(r'192.168.1.16\SQLEMKA'), r'192.168.1.16\SQLEMKA')
        self.assertEqual(validate_server('sql.example,1433'), 'sql.example,1433')
        with self.assertRaisesRegex(ValueError, 'ukośnika wstecznego'):
            validate_server('192.168.1.16/SQLEMKA')

    def test_sql_details_preserve_state_and_redact_password(self):
        from app.integrations.enova.odbc_support import describe_sql_error
        error = describe_sql_error(Exception('08001', 'SSL Provider: certificate chain not trusted; PWD={private;pass};'),
                                   secrets=('private;pass',))
        self.assertEqual(error['sqlstate'], '08001')
        self.assertIn('TLS', error['hint'])
        self.assertIn('certificate chain', error['details'])
        self.assertNotIn('private', error['details'])
        self.assertNotIn('pass}', error['details'])

    def test_login_failure_is_distinguished_from_network_failure(self):
        from app.integrations.enova.odbc_support import describe_sql_error
        error = describe_sql_error(Exception('28000', 'Login failed (18456)'))
        self.assertIn('logowanie', error['hint'])
        self.assertEqual(error['sqlstate'], '28000')

    def test_query_timeout_is_not_misreported_as_a_connection_timeout(self):
        from app.integrations.enova.odbc_support import describe_sql_error
        query = describe_sql_error(Exception('HYT00','Przekroczono limit czasu zapytania (SQLExecDirectW)'))
        connection = describe_sql_error(Exception('HYT00','Login timeout expired (SQLDriverConnect)'))
        self.assertIn('wykonania zapytania',query['hint'])
        self.assertNotIn('zaporę',query['hint'])
        self.assertIn('czas połączenia',connection['hint'])
        self.assertIn('TCP/IP',connection['hint'])
