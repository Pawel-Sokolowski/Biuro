"""Weekly ZUS and durable, company-scoped Enova reads; run on CI only."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo
from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting
from app.db.dao_monthly import save_month_status
from app.worker import enova_job,ezla_job


class WorkerSchedulingTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.make_company('Changed',nip='1234567890')
        self.config = {'enabled':True,'checking_enabled':False,
                       'sources':[{'nip':'1234567890','database':'CompanyA'}]}

    def pending(self):
        with get_conn() as conn:
            return conn.execute('SELECT company_id,period,revision FROM enova_stage_requests').fetchall()

    def test_no_change_never_reads_enova_and_repeated_stage_is_coalesced(self):
        with patch('app.integrations.enova.connection_settings.load_connection',return_value=self.config), \
             patch('app.integrations.enova.document_counts.run_import',return_value=[{'ok':True}]) as remote:
            self.assertEqual(enova_job()['mode'],'idle')
            remote.assert_not_called()
            save_month_status(self.company,'2026-11',{'etap':'Zaksięgowane'})
            revision = self.pending()[0]['revision']
            save_month_status(self.company,'2026-11',{'etap':'Zaksięgowane','ilosc_dok':3})
            self.assertEqual(self.pending()[0]['revision'],revision)
            self.assertEqual(enova_job()['mode'],'applied')
            self.assertEqual(remote.call_args.kwargs['months'],['2026-11'])
            self.assertEqual(remote.call_args.args[0]['sources'],self.config['sources'])
            self.assertFalse(self.pending())
            self.assertEqual(enova_job()['mode'],'idle')
            self.assertEqual(remote.call_count,1)

    def test_new_edit_during_read_survives_and_failure_is_retried(self):
        save_month_status(self.company,'2026-11',{'etap':'Zaksięgowane'})
        now = datetime(2026,11,21,10,tzinfo=timezone.utc)
        def read(*args,**kwargs):
            save_month_status(self.company,'2026-11',{'etap':'Wysłane wyniki'})
            return [{'ok':True}]
        with patch('app.integrations.enova.connection_settings.load_connection',return_value=self.config), \
             patch('app.integrations.enova.document_counts.run_import',side_effect=read) as remote:
            enova_job(now=now)
            self.assertEqual(len(self.pending()),1)
            remote.side_effect = ValueError('offline')
            first = enova_job(now=now)
            self.assertEqual(first['mode'], 'failed')
            self.assertEqual(first['results'][0]['error_type'], 'EnovaDocumentsFailed')
            self.assertEqual(enova_job(now=now+timedelta(seconds=30))['mode'], 'retry_waiting')
            self.assertEqual(remote.call_count, 2)
            self.assertEqual(len(self.pending()),1)
            self.assertEqual(enova_job(now=now+timedelta(seconds=60))['mode'], 'failed')
            with get_conn() as conn:
                row = conn.execute('SELECT attempts,next_attempt_at,last_error FROM enova_stage_requests').fetchone()
            self.assertEqual(tuple(row), (2, now+timedelta(minutes=3), 'EnovaDocumentsFailed'))
            from app.services.worker_monitor import snapshot
            self.assertEqual(snapshot(1)['enova_retries'][0]['company_id'], self.company)
            self.assertEqual(enova_job(now=now+timedelta(minutes=2))['mode'], 'retry_waiting')
            remote.side_effect = None
            remote.return_value = [{'ok':True}]
            enova_job(now=now+timedelta(minutes=3))
            self.assertFalse(self.pending())

    def test_adjacent_month_changes_are_read_together_for_one_company(self):
        for month in ('2026-09','2026-10','2026-11'):
            save_month_status(self.company,month,{'etap':'Zaksięgowane'})
        with patch('app.integrations.enova.connection_settings.load_connection',return_value=self.config), \
             patch('app.integrations.enova.document_counts.run_import',return_value=[{'ok':True}]) as remote:
            enova_job()
            self.assertEqual(remote.call_count,1)
            self.assertEqual(remote.call_args.kwargs['months'],['2026-09','2026-10','2026-11'])
            self.assertFalse(self.pending())

    def test_sent_accounting_stage_refreshes_count_even_if_upo_read_fails(self):
        now = datetime.now(ZoneInfo('Europe/Warsaw'))
        month = now.strftime('%Y-%m')
        self.config['sources'][0].update(accounting_type='KPIR', jpk_query='jpk.sql')
        from app.db.dao_monthly import get_month_status
        source_row = {'nip': '1234567890', 'miesiac': month, 'dokumenty': 42, 'pozycje_bankowe': 0}
        with patch('app.integrations.enova.connection_settings.load_connection', return_value=self.config), \
             patch('app.integrations.enova.document_counts.fetch_counts', return_value=[source_row]) as fetch, \
             patch('app.integrations.enova.jpk.fetch_counts', side_effect=ValueError('Brak danych UPO')) as jpk:
            save_month_status(self.company, month, {'etap': 'Zaksięgowane', 'etap_kadry': 'Wysłane wyniki'})
            self.assertEqual(enova_job(now=now)['mode'], 'failed')
            fetch.assert_not_called()
            save_month_status(self.company, month, {'etap': 'Wysłane wyniki'})
            self.assertEqual(enova_job(now=now)['mode'], 'failed')
            self.assertEqual(get_month_status(self.company, month)['ilosc_dok'], 42)
            self.assertEqual(len(self.pending()), 1)

            jpk.side_effect = None
            jpk.return_value = [{'nip': source_row['nip'], 'miesiac': month, 'status': 'unknown', 'upo': ''}]
            self.assertEqual(enova_job(now=now+timedelta(minutes=1))['mode'], 'applied')
            fetch.assert_called_once()

            save_month_status(self.company, month, {'etap': 'Zaksięgowane'})
            save_month_status(self.company, month, {'etap': 'Wysłane wyniki', 'ilosc_dok': 0,
                                                    '_manual_document_count': False})
            self.assertEqual(get_month_status(self.company, month)['ilosc_dok'], 42)
            fetch.return_value = [{**source_row, 'dokumenty': 57}]
            self.assertEqual(enova_job()['mode'], 'applied')
            self.assertEqual(get_month_status(self.company, month)['ilosc_dok'], 57)

    def test_ezla_catches_up_after_monday_and_completion_survives_restart(self):
        zone = ZoneInfo('Europe/Warsaw')
        monday = datetime(2026,11,16,3,tzinfo=zone)
        tuesday = datetime(2026,11,17,3,tzinfo=zone)
        with patch('app.integrations.ezla.sync.run_sync',return_value={'mode':'completed','companies':2,'failed':0}) as remote:
            self.assertEqual(ezla_job(now=monday.replace(hour=1))['mode'],'waiting')
            remote.assert_not_called()
            self.assertEqual(ezla_job(now=tuesday)['mode'],'completed')
            self.assertEqual(ezla_job(now=tuesday)['mode'],'checked_this_week')
            self.assertEqual(remote.call_count,1)
            ezla_job(now=datetime(2026,11,23,3,tzinfo=zone))
            self.assertEqual(remote.call_count,2)

    def test_interrupted_week_is_not_downloaded_again_but_a_failure_is_retried(self):
        zone = ZoneInfo('Europe/Warsaw')
        monday = datetime(2026,11,16,3,tzinfo=zone)
        from app.db.dao_app_settings import set_setting
        set_setting('worker_ezla_started_week', '2026-11-16')
        with patch('app.integrations.ezla.sync.run_sync') as remote:
            self.assertEqual(ezla_job(now=monday)['mode'], 'incomplete_week')
            remote.assert_not_called()
        set_setting('worker_ezla_started_week', '')
        with patch('app.integrations.ezla.sync.run_sync', return_value={'mode':'completed','companies':1,'failed':1}):
            with self.assertRaises(RuntimeError):
                ezla_job(now=monday)
        # A partial failure keeps the week retryable instead of blocking it for good.
        self.assertEqual(get_setting('worker_ezla_started_week'), '')
        with patch('app.integrations.ezla.sync.run_sync', return_value={'mode':'completed','companies':1,'failed':0}):
            self.assertEqual(ezla_job(now=monday)['mode'], 'completed')
        self.assertEqual(get_setting('worker_ezla_completed_week'), '2026-11-16')
        self.assertEqual(get_setting('worker_ezla_started_week'), '')

    def test_ezla_marks_the_week_started_before_downloading(self):
        monday = datetime(2026,11,16,3,tzinfo=ZoneInfo('Europe/Warsaw'))
        seen = {}
        def read(*args, **kwargs):
            seen['started'] = get_setting('worker_ezla_started_week')
            return {'mode':'completed','companies':1,'failed':0}
        with patch('app.integrations.ezla.sync.run_sync', side_effect=read):
            ezla_job(now=monday)
        self.assertEqual(seen['started'], '2026-11-16')

    def test_failed_or_busy_week_is_not_marked_completed(self):
        monday = datetime(2026,11,16,3,tzinfo=ZoneInfo('Europe/Warsaw'))
        with patch('app.integrations.ezla.sync.run_sync',return_value={'mode':'busy','companies':0,'failed':0}):
            self.assertEqual(ezla_job(now=monday)['mode'],'busy')
        self.assertIsNone(get_setting('worker_ezla_completed_week'))
        with patch('app.integrations.ezla.sync.run_sync',return_value={'mode':'completed','companies':2,'failed':1}):
            with self.assertRaises(RuntimeError):
                ezla_job(now=monday)
        self.assertIsNone(get_setting('worker_ezla_completed_week'))
