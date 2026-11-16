"""Weekly ZUS and durable, company-scoped Enova reads; run on CI only."""
import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting
from app.db.dao_monthly import save_month_status
from app.services.enova_stage import PREFIX
from app.worker import enova_job,ezla_job


class WorkerSchedulingTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.make_company('Changed',nip='1234567890')
        self.config = {'enabled':True,'checking_enabled':False,
                       'sources':[{'nip':'1234567890','database':'CompanyA'}]}

    def pending(self):
        with get_conn() as conn:
            return conn.execute('SELECT klucz,wartosc FROM app_settings WHERE starts_with(klucz,%s)',(PREFIX,)).fetchall()

    def test_no_change_never_reads_enova_and_repeated_stage_is_coalesced(self):
        with patch('app.integrations.enova.connection_settings.load_connection',return_value=self.config), \
             patch('app.integrations.enova.document_counts.run_import',return_value=[{'ok':True}]) as remote:
            self.assertEqual(enova_job()['mode'],'idle')
            remote.assert_not_called()
            save_month_status(self.company,'2026-11',{'etap':'W toku'})
            revision = json.loads(self.pending()[0]['wartosc'])['revision']
            save_month_status(self.company,'2026-11',{'etap':'W toku','ilosc_dok':3})
            self.assertEqual(json.loads(self.pending()[0]['wartosc'])['revision'],revision)
            self.assertEqual(enova_job()['mode'],'applied')
            self.assertEqual(remote.call_args.kwargs['months'],['2026-11'])
            self.assertEqual(remote.call_args.args[0]['sources'],self.config['sources'])
            self.assertFalse(self.pending())
            self.assertEqual(enova_job()['mode'],'idle')
            self.assertEqual(remote.call_count,1)

    def test_new_edit_during_read_survives_and_failure_is_retried(self):
        save_month_status(self.company,'2026-11',{'etap':'W toku'})
        def read(*args,**kwargs):
            save_month_status(self.company,'2026-11',{'etap':'Wysłane wyniki'})
            return [{'ok':True}]
        with patch('app.integrations.enova.connection_settings.load_connection',return_value=self.config), \
             patch('app.integrations.enova.document_counts.run_import',side_effect=read) as remote:
            enova_job()
            self.assertEqual(len(self.pending()),1)
            remote.side_effect = ValueError('offline')
            with self.assertRaises(ValueError):
                enova_job()
            self.assertEqual(len(self.pending()),1)
            remote.side_effect = None
            remote.return_value = [{'ok':True}]
            enova_job()
            self.assertFalse(self.pending())

    def test_adjacent_month_changes_are_read_together_for_one_company(self):
        for month in ('2026-09','2026-10','2026-11'):
            save_month_status(self.company,month,{'etap':'W toku'})
        with patch('app.integrations.enova.connection_settings.load_connection',return_value=self.config), \
             patch('app.integrations.enova.document_counts.run_import',return_value=[{'ok':True}]) as remote:
            enova_job()
            self.assertEqual(remote.call_count,1)
            self.assertEqual(remote.call_args.kwargs['months'],['2026-09','2026-10','2026-11'])
            self.assertFalse(self.pending())

    def test_ezla_downloads_only_monday_and_completion_survives_restart(self):
        zone = ZoneInfo('Europe/Warsaw')
        monday = datetime(2026,11,16,3,tzinfo=zone)
        with patch('app.integrations.ezla.sync.run_sync',return_value={'mode':'completed','companies':2,'failed':0}) as remote:
            self.assertEqual(ezla_job(now=datetime(2026,11,16,1,tzinfo=zone))['mode'],'waiting')
            self.assertEqual(ezla_job(now=datetime(2026,11,17,3,tzinfo=zone))['mode'],'waiting')
            remote.assert_not_called()
            ezla_job(now=monday)
            self.assertEqual(ezla_job(now=monday)['mode'],'checked_this_week')
            self.assertEqual(remote.call_count,1)
            ezla_job(now=datetime(2026,11,23,3,tzinfo=zone))
            self.assertEqual(remote.call_count,2)

    def test_failed_or_busy_week_is_not_marked_completed(self):
        monday = datetime(2026,11,16,3,tzinfo=ZoneInfo('Europe/Warsaw'))
        with patch('app.integrations.ezla.sync.run_sync',return_value={'mode':'busy','companies':0,'failed':0}):
            self.assertEqual(ezla_job(now=monday)['mode'],'busy')
        self.assertIsNone(get_setting('worker_ezla_completed_week'))
        with patch('app.integrations.ezla.sync.run_sync',return_value={'mode':'completed','companies':2,'failed':1}):
            with self.assertRaises(RuntimeError):
                ezla_job(now=monday)
        self.assertIsNone(get_setting('worker_ezla_completed_week'))
