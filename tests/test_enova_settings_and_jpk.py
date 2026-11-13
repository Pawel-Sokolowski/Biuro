import json
from datetime import date
from unittest import TestCase
from unittest.mock import patch
from cryptography.fernet import Fernet
from app.integrations.enova.document_counts import validate_rows, run_import
from app.integrations.enova.jpk import validate_jpk
from app.integrations.enova.connection_settings import save_connection, load_connection, credentials
from app.db.dao_conn import get_conn
from tests.base import DbTestCase


class JpkValidationTests(TestCase):
    def test_acceptance_requires_upo_and_complete_company_month_mapping(self):
        source = {'nip': '1234567890'}
        row = {'nip': source['nip'], 'miesiac': '2026-11', 'status': 'sent', 'upo': None}
        self.assertEqual(validate_jpk([row], source, ['2026-11'])['2026-11']['status'], 'sent')
        for changes in ({'status': 'accepted'}, {'status': 'unknown'}, {'nip': '9999999999'}):
            with self.assertRaises(ValueError):
                validate_jpk([{**row, **changes}], source, ['2026-11'])
        with self.assertRaises(ValueError):
            validate_jpk([], source, ['2026-11'])
        accepted = validate_jpk([{**row, 'status': 'accepted', 'upo': 'REF-123'}], source, ['2026-11'])
        self.assertEqual(accepted['2026-11']['upo'], 'REF-123')

    def test_breakdown_is_validated_against_count(self):
        source = {'nip': '1234567890', 'accounting_type': 'KH'}
        row = {'nip': source['nip'], 'miesiac': '2026-11', 'dokumenty': 10, 'pozycje_bankowe': 6,
               'rodzaje': '{"ZR":4,"SPU":6}'}
        count = validate_rows([row], source, ['2026-11'])[0]
        self.assertEqual((count.total, count.by_type), (12, {'ZR': 4, 'SPU': 6}))
        with self.assertRaises(ValueError):
            validate_rows([{**row, 'rodzaje': '{"ZR":11}'}], source, ['2026-11'])


class ConnectionAndJpkTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company(nip='1234567890', forma_opodatkowania='Księgi rachunkowe')

    def test_connection_encryption_and_blank_password_preserves_secret(self):
        config = {'server': r'10.0.0.1\ENOVA', 'database':'Biuro_rachunkowe', 'username': 'reader', 'enabled': True,
                  'months_back': 3, 'sources': [{'nip': '1234567890', 'database': 'ClientA'}]}
        with patch.dict('os.environ', {'BIO_ENCRYPTION_KEY': Fernet.generate_key().decode()}):
            save_connection(config, actor_id=self.admin, password='secret')
            stored = load_connection()
            self.assertEqual(stored['database'],'Biuro_rachunkowe')
            self.assertEqual(stored['sources'][0]['database'],'ClientA')
            self.assertNotIn('secret', json.dumps({k: v for k, v in stored.items() if k != '_directory'}))
            self.assertEqual(credentials(stored), ('reader', 'secret'))
            save_connection({**config, 'server': 'new-server'}, actor_id=self.admin)
            self.assertEqual(credentials(load_connection()), ('reader', 'secret'))
            from app.admin.settings.enova_connection_settings import EnovaConnectionSettings
            page = EnovaConnectionSettings(self.admin)
            self.addCleanup(page.close)
            self.assertEqual(page.database.text(),'Biuro_rachunkowe')
            page.database.setText('Biuro_rachunkowe_nowa')
            with patch('app.admin.settings.enova_connection_settings.QMessageBox.information'):
                page.save()
            self.assertEqual(load_connection()['database'],'Biuro_rachunkowe_nowa')
            self.assertEqual(credentials(load_connection()), ('reader','secret'))
        with patch('app.security.authorization.is_administrator', return_value=False):
            with self.assertRaises(PermissionError):
                save_connection(config, actor_id=self.admin, password='secret')

    def test_jpk_sync_without_finished_accounting_and_daily_deduplication(self):
        config = {'enabled': True, 'server': 'sql', 'months_back': 1,
                  'sources': [{'nip': '1234567890', 'database': 'ClientA', 'jpk_query': 'jpk.sql'}]}
        row = {'nip': '1234567890', 'miesiac': '2026-11', 'status': 'accepted', 'upo': 'REF-123'}
        with patch('app.integrations.enova.jpk.fetch_counts', return_value=[row]) as fetch:
            for _ in range(2):
                result = run_import(config, apply=True, today=date(2026, 11, 12))
                self.assertTrue(result[0]['ok'], result)
            fetch.assert_called_once()
        with patch('app.integrations.enova.jpk.fetch_counts', return_value=[{**row, 'status': 'sent', 'upo': None}]):
            run_import(config, today=date(2026, 11, 13))
        with patch('app.integrations.enova.jpk.fetch_counts', return_value=[{**row, 'upo': None}]):
            failure = run_import(config, apply=True, today=date(2026, 11, 13))
            self.assertFalse(failure[0]['ok'])
        from app.integrations.enova.accounting import list_accounting
        rows = [row for row in list_accounting(self.admin, '2026-11') if row['id'] == self.company]
        self.assertEqual(json.loads(rows[0]['jpk'])['status'], 'accepted')
        from app.accounting.accounting_ui import AccountingPage
        page = AccountingPage(self.admin)
        page.render(rows, '2026-11')
        column = next(i for i in range(page.table.columnCount()) if page.table.horizontalHeaderItem(i).text() == 'JPK_VAT\nStatus')
        self.assertEqual(page.table.item(0, column).text(), 'Przyjęty (UPO)')
        self.assertEqual(page.table.item(0, 3).text(), '—')
        page.close()

    def test_snapshot_and_total_are_saved_together_and_settings_form_loads(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,etap) VALUES (%s,'2026-11','Wysłane wyniki')", (self.company,))
        config = {'enabled': True, 'months_back': 1, 'sources': [{'nip': '1234567890', 'database': 'ClientA'}]}
        fetch = lambda *_: [{'nip': '1234567890', 'miesiac': '2026-11', 'dokumenty': 10,
                             'pozycje_bankowe': 6, 'rodzaje': '{"ZR":4,"SPU":6}'}]
        result = run_import(config, apply=True, today=date(2026, 11, 12), fetch=fetch)
        self.assertTrue(result[0]['ok'], result)
        from app.integrations.enova.accounting import list_accounting
        row = next(row for row in list_accounting(self.admin, '2026-11') if row['id'] == self.company)
        self.assertEqual(row['ilosc_dok'], 12)
        from app.invoices.faktury_logic import FakturyLogic
        self.assertEqual(FakturyLogic()._get_document_count_for_invoice(self.company, '2026-12', 0), 12)
        self.assertEqual(json.loads(row['snapshot'])['by_type'], {'ZR': 4, 'SPU': 6})
        from app.admin.settings.enova_connection_settings import EnovaConnectionSettings
        page = EnovaConnectionSettings(self.admin)
        self.assertEqual(page.table.columnCount(), 3)
        page.close()

    def test_manual_counts_are_preserved_before_and_after_enova_import(self):
        config = {'enabled': True, 'months_back': 1, 'sources': [{'nip': '1234567890', 'database': 'ClientA'}]}
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,etap,ilosc_dok) VALUES (%s,'2026-11','Wysłane wyniki',77)", (self.company,))
        def fetch(*_):
            return [{'nip': '1234567890', 'miesiac': '2026-11', 'dokumenty': 10, 'pozycje_bankowe': 6}]
        result = run_import(config, apply=True, today=date(2026, 11, 12), fetch=fetch)
        self.assertEqual(result[0]['manual_preserved'], ['2026-11'])
        with get_conn() as conn:
            self.assertEqual(tuple(conn.execute('SELECT ilosc_dok,etap FROM statusy_miesieczne WHERE firma_id=%s', (self.company,)).fetchone()), (77, 'Wysłane wyniki'))
        # A separate month starts empty, is auto-filled and is then corrected to zero.
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,etap) VALUES (%s,'2026-12','Wysłane wyniki')", (self.company,))
        def december(*_):
            return [{'nip': '1234567890', 'miesiac': '2026-12', 'dokumenty': 20, 'pozycje_bankowe': 0}]
        run_import(config, apply=True, today=date(2026, 12, 12), fetch=december)
        from app.db.dao_monthly import save_month_status
        save_month_status(self.company, '2026-12', {'etap': 'Wysłane wyniki', 'ilosc_dok': 0, '_manual_document_count': True})
        for day in (13, 14):
            run_import(config, apply=True, today=date(2026, 12, day), fetch=december)
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s AND miesiac='2026-12'", (self.company,)).fetchone()[0], 0)

    def test_manual_edit_during_remote_read_is_preserved(self):
        config = {'enabled': True, 'months_back': 1, 'sources': [{'nip': '1234567890', 'database': 'ClientA'}]}
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,etap) VALUES (%s,'2026-11','Wysłane wyniki')", (self.company,))
        def fetch(*_):
            from app.db.dao_monthly import save_month_status
            save_month_status(self.company, '2026-11', {'etap': 'Wysłane wyniki', 'ilosc_dok': 45, '_manual_document_count': True})
            return [{'nip': '1234567890', 'miesiac': '2026-11', 'dokumenty': 10, 'pozycje_bankowe': 0}]
        result = run_import(config, apply=True, today=date(2026, 11, 12), fetch=fetch)
        self.assertTrue(result[0]['ok'], result)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s', (self.company,)).fetchone()[0], 45)


class DatabaseConnectionTests(TestCase):
    def test_main_database_is_checked_before_company_sources_and_not_checked_twice(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from app.integrations.enova.connection_settings import check_connection
        root,company = Mock(),Mock()
        root.cursor.return_value.fetchone.return_value = ('Biuro_rachunkowe',)
        company.cursor.return_value.fetchone.return_value = ('ClientA',)
        driver = SimpleNamespace(connect=Mock(side_effect=[root,company]),drivers=lambda:['ODBC Driver 18 for SQL Server'])
        config = {'server':r'ENOVA\SQLEXPRESS','database':'Biuro_rachunkowe','username':'reader',
                  'sources':[{'database':'ClientA'},{'database':'Biuro_rachunkowe'}]}
        with patch.dict('sys.modules',{'pyodbc':driver}):
            result = check_connection(config,password='secret')
        self.assertEqual(result,[{'database':'Biuro_rachunkowe','ok':True},{'database':'ClientA','ok':True}])
        self.assertIn('DATABASE={Biuro_rachunkowe}',driver.connect.call_args_list[0].args[0])
        self.assertIn('DATABASE={ClientA}',driver.connect.call_args_list[1].args[0])
        root.close.assert_called_once()
        company.close.assert_called_once()

    def test_main_database_can_be_tested_without_company_mappings(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from app.integrations.enova.connection_settings import check_connection
        connection = Mock()
        connection.cursor.return_value.fetchone.return_value = ('Biuro_rachunkowe',)
        driver = SimpleNamespace(connect=Mock(return_value=connection),drivers=lambda:['ODBC Driver 18 for SQL Server'])
        with patch.dict('sys.modules',{'pyodbc':driver}):
            result = check_connection({'server':r'ENOVA\SQLEXPRESS','database':'Biuro_rachunkowe','username':'reader','sources':[]},password='secret')
        self.assertEqual(result,[{'database':'Biuro_rachunkowe','ok':True}])
        self.assertIn('DATABASE={Biuro_rachunkowe}',driver.connect.call_args.args[0])

    def test_test_checks_database_without_import_and_closes_connections(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from app.integrations.enova.connection_settings import check_connection
        connection = Mock()
        connection.cursor.return_value.fetchone.return_value = ('CompanyA',)
        driver = SimpleNamespace(connect=Mock(return_value=connection), drivers=lambda: ['ODBC Driver 18 for SQL Server'])
        config = {'server': r'10.0.0.1\ENOVA', 'username': 'reader', 'sources': [{'database': 'CompanyA'}]}
        with patch.dict('sys.modules', {'pyodbc': driver}):
            self.assertEqual(check_connection(config, password='secret'), [{'database': 'CompanyA', 'ok': True}])
            connection.cursor.return_value.execute.assert_called_once_with('SELECT DB_NAME()')
            connection.close.assert_called_once()
            self.assertEqual(driver.connect.call_args.kwargs['attrs_before'], {101: 1})
            connection.cursor.return_value.execute.side_effect = RuntimeError('secret')
            failed = check_connection(config, password='secret')[0]
            self.assertFalse(failed['ok'])
            self.assertEqual(failed['database'], 'CompanyA')
            self.assertNotIn('secret', failed['error']['details'])
            self.assertEqual(connection.close.call_count, 2)
