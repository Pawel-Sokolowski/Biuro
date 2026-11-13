import json
import os
from unittest.mock import Mock,patch
from cryptography.fernet import Fernet
from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting
from app.services.checking_refresh import enqueue,process_next,latest,recover_interrupted
from app.integrations.enova.checking import CheckingBusy


class CheckingRefreshTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company('A',nip='1112223344',forma_opodatkowania='KPiR')
        self.other = self.make_company('B',nip='2223334455',forma_opodatkowania='KPiR')
        self.config = {'enabled':False,'sources':[{'nip':'1112223344','database':'A'}, {'nip':'2223334455','database':'B'}]}

    def test_selection_permissions_duplicate_clicks_and_reauthorization_before_execution(self):
        first = enqueue(self.admin,'enova',[self.company],'2026-11')
        self.assertEqual(enqueue(self.admin,'enova',[self.company,self.company],'2026-11')['id'],first['id'])
        with patch('app.services.checking_refresh.assigned_company_ids',return_value={self.company}):
            with self.assertRaises(PermissionError):
                enqueue(self.admin,'enova',[self.company,self.other],'2026-11')
        with patch('app.services.checking_refresh.require_access',side_effect=PermissionError('Dostęp odebrany')), \
             patch('app.integrations.enova.checking.run_checking_import') as importer:
            result = process_next()
        self.assertEqual(result['state'],'failed')
        self.assertIn('Dostęp odebrany',latest(self.admin)['error'])
        importer.assert_not_called()

    def test_enova_refresh_only_selected_firm_selected_month_and_previous_without_billing_changes(self):
        with get_conn() as conn:
            for fid in (self.company,self.other):
                conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok,etap) VALUES (%s,'2026-11',77,'Brak')",(fid,))
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)',
                         (f'enova_checking_{self.other}_2026-11','{"old": true}'))
        def fetch(config,source,months):
            return [{'nip':source['nip'],'miesiac':month,'dokumenty':1,'pozycje_bankowe':0,'rodzaje':{'AMO':1}} for month in months]
        with patch('app.integrations.enova.connection_settings.load_connection',return_value=self.config), \
             patch('app.integrations.enova.checking.fetch_counts',side_effect=fetch) as remote:
            for _ in range(2):
                enqueue(self.admin,'enova',[self.company],'2026-11')
                self.assertEqual(process_next()['state'],'completed')
            self.assertEqual(remote.call_count,2)  # Explicit refresh bypasses daily checking deduplication.
            self.assertEqual(remote.call_args.args[1]['database'],'A')
            self.assertEqual(remote.call_args.args[2],['2026-10','2026-11'])
        self.assertFalse(self.config['enabled'])
        self.assertEqual(json.loads(get_setting(f'enova_checking_{self.other}_2026-11')),{'old':True})
        self.assertIsNone(get_setting(f'enova_checking_checked_{self.company}'))
        self.assertIsNone(get_setting('worker_enova_completed'))
        with get_conn() as conn:
            self.assertEqual([tuple(row) for row in conn.execute('SELECT ilosc_dok,etap FROM statusy_miesieczne WHERE firma_id=ANY(%s)',([self.company,self.other],))],[(77,'Brak'),(77,'Brak')])

    def test_busy_source_requeues_and_worker_restart_recovers_interrupted_read(self):
        value = enqueue(self.admin,'enova',[self.company],'2026-11')
        with patch('app.integrations.enova.connection_settings.load_connection',return_value=self.config), \
             patch('app.integrations.enova.checking.run_checking_import',side_effect=CheckingBusy('busy')):
            self.assertEqual(process_next()['state'],'queued')
        with get_conn() as conn:
            value.update(state='running')
            conn.execute('UPDATE app_settings SET wartosc=%s WHERE klucz=%s',(json.dumps(value),'checking_refresh_'+value['id']))
        recover_interrupted()
        self.assertEqual(latest(self.admin)['state'],'queued')

    def test_missing_mapping_is_reported_without_stopping_next_selected_company(self):
        config = {**self.config,'sources':self.config['sources'][1:]}
        enqueue(self.admin,'enova',[self.company,self.other],'2026-11')
        with patch('app.integrations.enova.connection_settings.load_connection',return_value=config), \
             patch('app.integrations.enova.checking.run_checking_import',return_value=[{'ok':True}]) as importer:
            self.assertEqual(process_next()['state'],'partial')
        self.assertEqual(importer.call_args.args[0]['sources'],config['sources'])
        result = latest(self.admin)
        self.assertEqual([row['company_id'] for row in result['results']],[self.company,self.other])
        self.assertEqual([row['ok'] for row in result['results']],[False,True])

    def test_selected_ezla_uses_shared_credentials_and_leaves_other_payers_untouched(self):
        config = {'enabled':False,'companies':[self.company,self.other]}
        enqueue(self.admin,'ezla',[self.company],'2026-11')
        with patch.dict(os.environ,{'BIO_ENCRYPTION_KEY':Fernet.generate_key().decode()}), \
             patch('app.integrations.ezla.settings.load_settings',return_value=config), \
             patch('app.integrations.ezla.sync.load_settings',return_value=dict(config)), \
             patch('app.integrations.ezla.sync.credentials',return_value=('login','secret','zip')), \
             patch('app.integrations.ezla.sync.fetch_reports',return_value=[]) as fetch:
            self.assertEqual(process_next()['state'],'completed')
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0],'1112223344')
        self.assertIsNotNone(get_setting(f'ezla_sync_{self.company}'))
        self.assertIsNone(get_setting(f'ezla_sync_{self.other}'))

    def test_staff_can_queue_reads_under_current_module_permissions_without_admin_credentials(self):
        employee = self.make_employee()
        request = enqueue(employee,'enova',[self.company],'2026-11')
        self.assertEqual(latest(employee)['id'],request['id'])
        self.assertIsNone(latest(self.admin))
        with patch('app.services.checking_refresh.require_access',side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                enqueue(employee,'ezla',[self.company],'2026-11')

    def test_checking_selection_carries_company_ids_and_completed_request_refreshes_existing_view_once(self):
        from PyQt6.QtCore import QItemSelectionModel
        from app.accounting.accounting_ui import AccountingPage
        from app.integrations.enova.accounting import list_accounting
        page = AccountingPage(self.admin,department='accounting')
        self.addCleanup(page.close)
        page.render(list_accounting(self.admin,'2026-11'),'2026-11')
        table = page.tables['accounting']
        for index in range(table.rowCount()):
            if table.item(index,0).text() in ('A','B'):
                table.selectionModel().select(table.model().index(index,0),
                    QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
        self.assertEqual(page.selected_company_ids(),[self.company,self.other])
        controls = page.refresh_controls
        controls.refresh_view = Mock()
        value = enqueue(self.admin,'enova',[self.company],'2026-11')
        controls.loaded(value)
        controls.refresh_view.assert_not_called()
        value = {**value,'state':'completed','finished_at':'2026-11-13T12:00:00'}
        controls.loaded(value)
        controls.loaded(value)
        controls.refresh_view.assert_called_once()
