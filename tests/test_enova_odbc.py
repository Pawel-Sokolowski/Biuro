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
