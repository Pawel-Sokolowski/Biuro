import json
import os
from datetime import date
from unittest.mock import patch
from cryptography.fernet import Fernet
from tests.base import DbTestCase
from tests.test_ezla_report import report, certificate
from app.db.dao_conn import get_conn
from app.integrations.ezla.settings import load_settings, save_settings
from app.integrations.ezla.service import Download, ServiceError
from app.integrations.ezla.sync import run_sync, load_reports


class EzlaSyncTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.key = patch.dict(os.environ, {'BIO_ENCRYPTION_KEY': Fernet.generate_key().decode()})
        self.key.start()
        self.addCleanup(self.key.stop)
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company(nip='1112223344')
        save_settings({'enabled': True, 'login': 'dedicated@example.test', 'companies': [self.company]},
                      actor_id=self.admin, password='service-secret', zip_password='zip-secret')

    def status(self):
        with get_conn() as conn:
            return json.loads(conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',
                              (f'ezla_sync_{self.company}',)).fetchone()[0])

    def test_shared_passwords_are_preserved_and_reports_are_encrypted_and_deduplicated(self):
        first = load_settings()
        save_settings({'enabled': True, 'login': first['login'], 'companies': [self.company]}, actor_id=self.admin)
        self.assertEqual(first, load_settings())
        with patch('app.integrations.ezla.sync.fetch_reports', return_value=[Download('2018-08-01', report(certificate()))]):
            self.assertEqual(run_sync(actor_id=self.admin, today=date(2026, 11, 12))['failed'], 0)
            self.assertEqual(run_sync(actor_id=self.admin, today=date(2026, 11, 12))['results'][0]['new_reports'], 0)
        with get_conn() as conn:
            values = conn.execute("SELECT wartosc FROM app_settings WHERE klucz LIKE 'ezla_report_%'").fetchall()
        self.assertEqual(len(values), 1)
        self.assertNotIn('JAN PRZYKŁAD', values[0][0])
        self.assertNotIn('00000000000', values[0][0])
        saved = load_reports(self.admin, '2018-04')['reports']
        self.assertEqual(saved[0]['report'].certificates[0].employee_name, 'JAN PRZYKŁAD')
        self.assertEqual(load_reports(self.admin, '2018-05')['reports'], [])

    def test_live_progress_precedes_remote_response_and_success_follows_committed_reports(self):
        from app.services.worker_activity import KEY
        from app.db.dao_app_settings import get_setting
        def download(*args, **kwargs):
            events = json.loads(get_setting(KEY))
            self.assertEqual(events[-1]['stage'], 'downloading')
            self.assertEqual(events[-1]['company_id'], self.company)
            return [Download('2018-08-01', report(certificate()))]
        with patch('app.integrations.ezla.sync.fetch_reports', side_effect=download):
            run_sync(actor_id=self.admin)
        events = json.loads(get_setting(KEY))
        self.assertEqual([event['stage'] for event in events], ['downloading', 'saving', 'downloaded'])
        self.assertEqual(events[-1]['count'], 1)
        self.assertEqual(self.status()['state'], 'ok')
        self.assertNotIn('service-secret', json.dumps(events))
        self.assertNotIn('JAN PRZYKŁAD', json.dumps(events))

    def test_plain_text_settings_are_never_cast_as_report_json(self):
        with get_conn() as conn:
            for key, value in (('ezla_test_month', '2026-11'), ('ezla_test_date', '2026-11-12'),
                               ('ezla_test_label', 'zwykły tekst bez JSON')):
                conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)', (key, value))
        with patch('app.integrations.ezla.sync.fetch_reports', return_value=[Download('2018-08-01', report(certificate()))]):
            run_sync(actor_id=self.admin)
        included = load_reports(self.admin, '2018-04')
        self.assertEqual(len(included['reports']), 1)
        self.assertEqual(included['errors'], [])
        self.assertEqual(included['reports'][0]['company']['id'], self.company)
        self.assertEqual(load_reports(self.admin, '2018-05')['reports'], [])

    def test_failed_batch_rolls_back_new_reports_and_preserves_previous_cursor(self):
        original = Download('2018-08-01', report(certificate()))
        with patch('app.integrations.ezla.sync.fetch_reports', return_value=[original]):
            run_sync(actor_id=self.admin)
        cursor = self.status()['last_success']
        bad = report(certificate()).replace(b'1112223344', b'9999999999')
        with patch('app.integrations.ezla.sync.fetch_reports', return_value=[
                Download('2018-08-01', report(certificate(number='2'))), Download('2018-08-01', bad)]):
            result = run_sync(actor_id=self.admin)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(self.status()['last_success'], cursor)
        self.assertEqual(self.status()['error_code'], 'payer')
        self.assertEqual(len(load_reports(self.admin, '2018-04')['reports']), 1)

    def test_one_payer_failure_does_not_stop_the_next_company(self):
        second = self.make_company(nip='2223334455')
        config = load_settings()
        save_settings({**config, 'companies': [self.company, second]}, actor_id=self.admin)
        def fetch(nip, *args, **kwargs):
            if nip == '1112223344':
                raise ServiceError('101')
            return [Download('2018-08-01', report(certificate()).replace(b'1112223344', b'2223334455'))]
        with patch('app.integrations.ezla.sync.fetch_reports', side_effect=fetch), \
             patch('app.integrations.ezla.sync.wait_between') as wait:
            result = run_sync(actor_id=self.admin)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['companies'], 2)
        self.assertEqual(wait.call_args.args[0], 30)
        self.assertEqual(load_reports(self.admin, '2018-04')['reports'][0]['company']['id'], second)

    def test_read_uses_shared_key_and_never_returns_a_false_empty_success_on_bad_key(self):
        with patch('app.integrations.ezla.sync.fetch_reports', return_value=[Download('2018-08-01', report(certificate()))]):
            run_sync(actor_id=self.admin)
        with patch.dict(os.environ, {'BIO_ENCRYPTION_KEY': Fernet.generate_key().decode()}):
            result = load_reports(self.admin, '2018-04')
        self.assertEqual(result['reports'], [])
        self.assertTrue(result['errors'])

    def test_only_administrator_can_save_credentials_or_trigger_download(self):
        with patch('app.security.authorization.is_administrator', return_value=False):
            with self.assertRaises(PermissionError):
                save_settings(load_settings(), actor_id=self.admin)
            with self.assertRaises(PermissionError):
                run_sync(actor_id=self.admin)

    def test_settings_page_constructs_without_exposing_saved_passwords(self):
        from app.admin.settings.ezla_settings import EzlaSettings
        page = EzlaSettings(self.admin)
        try:
            self.assertEqual(page.password.text(), '')
            self.assertEqual(page.zip_password.text(), '')
            self.assertEqual(page.login.text(), 'dedicated@example.test')
            self.assertTrue(page.enabled.isChecked())
        finally:
            page.close()

    def test_worker_uses_independent_ezla_job_and_reports_partial_failure(self):
        from app.worker import run_job
        with patch('app.integrations.ezla.sync.run_sync', return_value={'companies': 2, 'failed': 0}) as run:
            self.assertEqual(run_job('ezla'), {'companies': 2, 'failed': 0, 'mode': 'completed'})
            run.assert_called_once_with(stop=None)
        with patch('app.integrations.ezla.sync.run_sync', return_value={'companies': 2, 'failed': 1}):
            with self.assertRaises(RuntimeError):
                run_job('ezla')

    def test_unattended_worker_downloads_commits_and_exposes_live_company_log(self):
        from app.worker import JOBS, serve
        from app.services.worker_settings import DEFAULTS, save_settings as save_worker
        from app.db.dao_app_settings import get_setting
        from app.services.worker_activity import KEY
        settings = {**DEFAULTS, **{name: False for name in JOBS}, 'enabled': True, 'ezla': True}
        save_worker(settings, actor_id=self.admin)
        with patch('app.integrations.ezla.sync.fetch_reports', return_value=[Download('2018-08-01', report(certificate()))]) as download:
            self.assertEqual(serve(once=True), 0)
        download.assert_called_once()
        heartbeat = json.loads(get_setting('background_worker_heartbeat'))
        self.assertEqual(heartbeat['jobs']['ezla']['state'], 'ok')
        self.assertEqual(heartbeat['jobs']['ezla']['result']['companies'], 1)
        self.assertEqual(self.status()['new_reports'], 1)
        event = json.loads(get_setting(KEY))[-1]
        self.assertEqual((event['company_id'], event['stage'], event['count']), (self.company, 'downloaded', 1))

    def test_batch_lock_prevents_manual_download_from_overlapping_runner(self):
        with get_conn() as connection:
            self.assertTrue(connection.execute('SELECT pg_try_advisory_xact_lock(748212)').fetchone()[0])
            with patch('app.integrations.ezla.sync.fetch_reports') as fetch:
                result = run_sync(actor_id=self.admin)
                self.assertEqual(result['mode'], 'busy')
                fetch.assert_not_called()

    def test_monitor_snapshot_contains_status_but_not_credentials_or_xml(self):
        from app.services.worker_monitor import snapshot
        value = snapshot(self.admin)
        text = repr(value)
        self.assertNotIn('service-secret', text)
        self.assertNotIn('zip-secret', text)
        self.assertNotIn('password_enc', text)
        self.assertEqual(value['ezla_companies'], [self.company])

    def test_http_status_is_persisted_for_worker_diagnostics(self):
        with patch('app.integrations.ezla.sync.fetch_reports',side_effect=ServiceError('http_503')):
            result = run_sync(actor_id=self.admin,today=date(2026,11,12))
        self.assertEqual(result['failed'],1)
        status = self.status()
        self.assertEqual(status['error_code'],'http_503')
        self.assertEqual(status['http_status'],503)
        self.assertIn('HTTP 503',str(ServiceError(status['error_code'],http_status=status['http_status'])))
