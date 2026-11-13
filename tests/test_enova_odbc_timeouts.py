"""pyodbc query timeout belongs to the connection, never the cursor."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from app.integrations.enova.connection_settings import check_connection
from app.integrations.enova.document_counts import fetch_counts
from app.integrations.enova.schema_probe import inspect_schema
from app.integrations.enova.discovery import discover


class OdbcTimeoutTests(TestCase):
    def setUp(self):
        # A permissive Mock used to conceal assignment of unsupported cursor fields.
        self.cursor = Mock(spec_set=['execute', 'fetchone', 'fetchmany', 'description'])
        self.connection = Mock()
        self.connection.cursor.return_value = self.cursor
        self.driver = SimpleNamespace(connect=Mock(return_value=self.connection),
                                      drivers=lambda: ['ODBC Driver 18 for SQL Server'])
        self.patch = patch.dict('sys.modules', {'pyodbc': self.driver})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.config = {'server': r'sql\ENOVA', 'username': 'reader'}

    def test_connection_test_uses_connection_query_timeout(self):
        self.cursor.fetchone.return_value = ('master',)
        results = check_connection(self.config, password='secret')
        self.assertTrue(results[0]['ok'], results)
        self.assertEqual(self.connection.timeout, 15)
        self.assertEqual(self.driver.connect.call_args.kwargs['timeout'], 15)
        self.connection.close.assert_called_once()

    def test_document_query_uses_connection_query_timeout(self):
        self.cursor.description = [('nip',), ('miesiac',), ('dokumenty',), ('pozycje_bankowe',)]
        self.cursor.fetchmany.return_value = [('1234567890', '2026-11', 1, 0)]
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / 'counts.sql'
            query.write_text('SELECT source_counts WHERE date >= ? AND date < ?')
            source = {'database': 'ClientA', 'count_query': str(query)}
            with patch('app.integrations.enova.document_counts.sql_credentials', return_value=('reader', 'secret')):
                rows = fetch_counts(self.config, source, ['2026-11'])
        self.assertEqual(rows[0]['dokumenty'], 1)
        self.assertEqual(self.connection.timeout, 120)
        self.connection.close.assert_called_once()

    def test_document_query_uses_connection_query_timeout_with_custom_limit(self):
        self.cursor.description = [('nip',), ('miesiac',), ('dokumenty',), ('pozycje_bankowe',)]
        self.cursor.fetchmany.return_value = [('1234567890', '2026-11', 1, 0)]
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / 'counts.sql'
            query.write_text('SELECT source_counts WHERE date >= ? AND date < ?')
            config = {**self.config, 'discovery_query_timeout': 1200}
            with patch('app.integrations.enova.document_counts.sql_credentials', return_value=('reader', 'secret')):
                fetch_counts(config, {'database': 'A', 'count_query': str(query)}, ['2026-11'])
        self.assertEqual(self.connection.timeout, 1200)
        self.assertEqual(self.driver.connect.call_args.kwargs['timeout'], 15)

    def test_metadata_export_uses_connection_query_timeout(self):
        self.cursor.description = [('schema_name',), ('object_name',), ('column_name',), ('data_type',)]
        self.cursor.fetchmany.side_effect = [[('dbo', 'Deklaracje', 'Status', 'int')], []]
        result = inspect_schema(self.config, 'ClientA', password='secret')
        self.assertEqual(result['columns'][0]['object_name'], 'Deklaracje')
        self.assertEqual(self.connection.timeout, 30)
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.connection.close.assert_called_once()

    def test_discovery_custom_query_timeout_is_separate_from_login_timeout(self):
        self.cursor.fetchmany.return_value = []
        result = discover({**self.config,'discovery_query_timeout':1200},password='secret')
        self.assertEqual(result['visible'],0)
        self.assertEqual(self.connection.timeout,1200)
        self.assertEqual(self.driver.connect.call_args.kwargs['timeout'],15)
        self.connection.close.assert_called_once()

    def test_invalid_discovery_timeout_never_opens_sql(self):
        for timeout in (0,10,1201,True,'120'):
            with self.subTest(timeout=timeout),self.assertRaises(ValueError):
                discover({**self.config,'discovery_query_timeout':timeout},password='secret')
        self.driver.connect.assert_not_called()
