from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest import TestCase
from unittest.mock import patch, Mock, MagicMock
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet
from app.services.worker_settings import DEFAULTS, save_settings, load_settings
from tests.base import DbTestCase
from app.db.dao_conn import get_conn


class DeploymentKeyTests(TestCase):
    def test_missing_key_is_never_generated_and_invalid_token_is_not_empty_password(self):
        from app.utils.env_config import ensure_encryption_key
        from app.utils.crypto import decrypt_password
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                ensure_encryption_key()
            self.assertNotIn('BIO_ENCRYPTION_KEY', os.environ)
        first, second = Fernet.generate_key(), Fernet.generate_key()
        token = Fernet(first).encrypt(b'secret').decode()
        with patch.dict(os.environ, {'BIO_ENCRYPTION_KEY': second.decode()}):
            with self.assertRaisesRegex(ValueError, 'innego klucza'):
                decrypt_password(token)
        with patch.dict(os.environ, {'BIO_ENCRYPTION_KEY': first.decode()}):
            self.assertEqual(decrypt_password(token), 'secret')

    def test_install_shared_key_preserves_other_settings_and_survives_reload(self):
        from app.utils.env_config import install_encryption_key, load_environment
        key = Fernet.generate_key().decode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('DB_URL=postgresql://test\nBIO_ENCRYPTION_KEY = old\n', encoding='utf-8')
            with patch('app.utils.env_config.user_env_path', return_value=path), patch.dict(os.environ, {}, clear=True):
                install_encryption_key(key)
                self.assertIn('DB_URL=postgresql://test', path.read_text())
                self.assertEqual(path.read_text().count('BIO_ENCRYPTION_KEY'), 1)
                del os.environ['BIO_ENCRYPTION_KEY']
                load_environment()
                self.assertEqual(os.environ['BIO_ENCRYPTION_KEY'], key)


class PlatnikGuiScheduleTests(TestCase):
    def test_daily_schedule_keeps_warsaw_hour_through_clock_change(self):
        from app.worker import platnik_gui_schedule

        warsaw = ZoneInfo('Europe/Warsaw')
        now = datetime(2026, 10, 24, 15, 59, tzinfo=warsaw)
        self.assertEqual(platnik_gui_schedule(now, '16:00', None).isoformat(),
                         '2026-10-24T16:00:00+02:00')
        self.assertEqual(platnik_gui_schedule(now, '16:00', {'date': '2026-10-24'}).isoformat(),
                         '2026-10-25T16:00:00+01:00')


class WorkerLoopTests(TestCase):
    """Loop behaviour that needs no database: corrupt state, timeouts and cadence."""

    def test_corrupt_worker_settings_fail_closed_to_disabled_defaults(self):
        from app.services.worker_settings import DEFAULTS, load_settings
        for raw in ('{bad json', '[]', '{"enabled": true, "mail_interval": "soon"}'):
            with self.subTest(raw=raw), patch('app.services.worker_settings.get_setting', return_value=raw):
                with self.assertLogs('app.services.worker_settings', level='WARNING'):
                    settings = load_settings()
                self.assertEqual(settings, DEFAULTS)
                self.assertFalse(settings['enabled'])

    def test_heartbeat_survives_a_corrupt_event_log_and_failed_recovery(self):
        from app.worker import serve
        settings = {'enabled': False}
        with patch('app.worker.leadership') as leadership, \
             patch('app.worker.get_setting', return_value='{not json'), \
             patch('app.worker.load_settings', return_value=settings), \
             patch('app.worker.set_setting') as written, \
             patch('app.services.checking_refresh.recover_interrupted', side_effect=RuntimeError('broken')), \
             patch('app.worker.WORKER_THREADS', 1):
            leadership.return_value.__enter__.return_value.execute.return_value = [1]
            self.assertEqual(serve(once=True), 0)
        heartbeat = json.loads([call.args[1] for call in written.call_args_list
                                if call.args[0] == 'background_worker_heartbeat'][-1])
        self.assertIn('checked_at', heartbeat)

    def test_unreadable_settings_are_logged_instead_of_stopping_the_worker(self):
        from app.worker import _parse_setting
        with self.assertLogs('app.worker', level='WARNING') as logs:
            self.assertEqual(_parse_setting('{oops', []), [])
            self.assertEqual(_parse_setting('{"wrong":"shape"}', []), [])
        self.assertEqual('\n'.join(logs.output).count('worker_setting_unreadable'), 2)

    def test_timed_out_job_is_not_resubmitted_while_its_thread_is_live(self):
        from app.services.worker_settings import DEFAULTS
        from app.worker import serve

        class ResponsiveStop(threading.Event):
            def wait(self, timeout=None):
                return super().wait(min(timeout or 0.01, 0.01))

        settings = {**DEFAULTS, 'enabled': True}
        settings.update({name: False for name in (
            'reminders', 'invoices', 'enova', 'ezla', 'ksef_import', 'saldeo',
            'platnik', 'platnik_gui', 'checking_refresh',
        )})
        stop, started, release, duplicate, released = (
            ResponsiveStop(), threading.Event(), threading.Event(), threading.Event(), threading.Event()
        )
        timed_out, finished_after_timeout = threading.Event(), threading.Event()
        calls = 0

        def job(_name, _stop):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                release.wait(2)
            elif not released.is_set():
                duplicate.set()
                stop.set()
            return {}

        def persist(key, value):
            if key == 'background_worker_heartbeat':
                status = json.loads(value)['jobs'].get('mail', {})
                if status.get('state') == 'failed' and status.get('error_type') == 'Timeout':
                    if status.get('still_running'):
                        timed_out.set()
                    else:
                        finished_after_timeout.set()

        leadership = MagicMock()
        leadership.return_value.__enter__.return_value.execute.return_value = [1]
        with patch('app.worker.leadership', leadership), \
             patch('app.worker.get_setting', return_value=None), \
             patch('app.worker.load_settings', return_value=settings), \
             patch('app.worker.set_setting', side_effect=persist), \
             patch('app.worker.run_job', side_effect=job), \
             patch('app.worker.job_timeout', return_value=0.01), \
             patch('app.worker.job_interval', return_value=0.01), \
             patch('app.worker.WORKER_THREADS', 2), \
             patch('app.worker.HEARTBEAT_SECONDS', 0), \
             patch('app.services.checking_refresh.recover_interrupted'), \
             patch('app.services.worker_setup.worker_env_path', return_value=Path('worker.env')):
            result = []
            worker = threading.Thread(target=lambda: result.append(serve(once=True, stop=stop)))
            worker.start()
            self.assertTrue(started.wait(2))
            self.assertTrue(timed_out.wait(2))
            self.assertFalse(duplicate.wait(0.05))
            released.set()
            release.set()
            self.assertTrue(finished_after_timeout.wait(2))
            stop.set()
            worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertFalse(duplicate.is_set())

    def test_job_without_a_timeout_is_never_abandoned(self):
        from app.worker import job_timeout
        self.assertIsNone(job_timeout('ezla', {}))
        self.assertIsNone(job_timeout('checking_refresh', {}))
        self.assertEqual(job_timeout('mail', {'mail_interval': 120}), 1800)
        self.assertEqual(job_timeout('platnik_gui', {'platnik_gui_interval': 60}), 1800)

    def test_job_interval_uses_fixed_cadence_for_enova(self):
        from app.worker import job_interval
        from app.services.worker_settings import DEFAULTS, ENOVA_INTERVAL
        self.assertEqual(job_interval('enova', DEFAULTS), ENOVA_INTERVAL)
        self.assertEqual(job_interval('mail', DEFAULTS), DEFAULTS['mail_interval'])


class WorkerTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]

    def test_worker_disabled_does_no_work_and_singleton_lock_prevents_duplicates(self):
        from app.worker import serve, leadership
        with patch('app.worker.run_job') as job:
            self.assertEqual(serve(once=True), 0)
            job.assert_not_called()
        from app.db.dao_app_settings import get_setting
        from app.version import __version__
        heartbeat = json.loads(get_setting('background_worker_heartbeat'))
        self.assertEqual(heartbeat['instance']['pid'],os.getpid())
        self.assertEqual(heartbeat['instance']['version'],__version__)
        self.assertTrue(heartbeat['instance']['id'])
        self.assertTrue(heartbeat['settings_checked_at'])
        with leadership():
            with self.assertRaisesRegex(RuntimeError, 'już działa'):
                with leadership():
                    self.fail('Duplicate worker acquired the lock')

    def test_worker_runs_enabled_jobs_independently_and_records_failed_job(self):
        from app.worker import serve
        save_settings({**DEFAULTS, 'enabled': True, 'invoices': False, 'enova': False}, actor_id=self.admin)
        def job(name, stop=None):
            if name == 'mail':
                raise RuntimeError('simulated')
            return {'sent': 0}
        with patch('app.worker.run_job', side_effect=job) as calls:
            self.assertEqual(serve(once=True), 1)
            self.assertEqual({call.args[0] for call in calls.call_args_list}, {'mail', 'reminders', 'ezla', 'checking_refresh'})
        with get_conn() as conn:
            status = json.loads(conn.execute("SELECT wartosc FROM app_settings WHERE klucz='background_worker_heartbeat'").fetchone()[0])
        self.assertEqual(status['jobs']['mail']['state'], 'failed')
        self.assertEqual(status['jobs']['reminders']['state'], 'ok')
        with patch('app.security.authorization.is_administrator', return_value=False):
            with self.assertRaises(PermissionError):
                save_settings(DEFAULTS, actor_id=self.admin)

    def test_invoice_worker_only_processes_existing_authorized_queue_without_resubmission(self):
        from app.worker import invoices_job
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [{'id': 123, 'invoice_email_actor_id': self.admin}]
        result = {'accepted': [{'delivery': {'status': 'sent'}}], 'errors': []}
        with patch('app.db.dao_conn.get_conn') as factory, patch('app.invoices.ksef_submission.process_invoice_batch', return_value=result) as process:
            factory.return_value.__enter__.return_value = conn
            self.assertEqual(invoices_job()['sent'], 1)
            process.assert_called_once_with([123], self.admin, send=False)
            sql = conn.execute.call_args.args[0]
            self.assertIn("invoice_email_status IN ('queued','failed_before_send')", sql)
            self.assertIn('cancelled_at IS NULL', sql)

    def test_invoice_worker_does_not_starve_the_next_invoice_after_50_failures(self):
        from app.worker import invoices_job
        with get_conn() as conn:
            ids = [row[0] for row in conn.execute("""
                INSERT INTO faktury(miesiac,kwota_netto,kwota_brutto,pozycje_json,invoice_email_status,
                                    invoice_email_actor_id,ksef_status,status,eksportowana)
                SELECT '2026-11',100,123,'[]',
                       CASE WHEN n=51 THEN 'queued' ELSE 'failed_before_send' END,%s,'przyjeta','wystawiona',1
                FROM generate_series(1,51) AS n RETURNING id
            """, (self.admin,))]
        with patch('app.invoices.ksef_submission.process_invoice_batch',
                   return_value={'accepted': [], 'errors': [{}]}) as process:
            with self.assertRaises(RuntimeError):
                invoices_job()
            self.assertEqual({call.args[0][0] for call in process.call_args_list}, set(ids[:50]))
        with patch('app.invoices.ksef_submission.process_invoice_batch',
                   return_value={'accepted': [{'delivery': {'status': 'sent'}}], 'errors': []}) as process:
            self.assertEqual(invoices_job()['sent'], 1)
            process.assert_called_once_with([ids[-1]], self.admin, send=False)

    def test_worker_settings_and_local_key_pages_construct(self):
        from app.admin.settings.worker_settings import WorkerSettings
        from app.admin.settings.encryption_settings import EncryptionSettings
        worker, encryption = WorkerSettings(self.admin), EncryptionSettings(self.admin)
        self.assertFalse(worker.enabled.isChecked())
        self.assertIn('Odcisk klucza', encryption.status.text())
        worker.close()
        encryption.close()

    def test_disabled_worker_cli_starts_without_login_or_visible_window(self):
        from app.worker import main
        with patch.dict(os.environ, {'BIO_ENCRYPTION_KEY': Fernet.generate_key().decode()}), patch('app.worker.run_job') as job:
            self.assertEqual(main(['--once']), 0)
            job.assert_not_called()

    def test_missing_key_in_headless_worker_is_logged_and_returns_failure(self):
        from app.worker import main
        with patch('app.utils.env_config.ensure_encryption_key', side_effect=ValueError('Shared key missing')), \
             patch('app.diagnostics.configure_logging'), patch('app.worker.logger.exception') as logged, \
             patch('app.worker.serve') as serve:
            self.assertEqual(main(['--once']), 1)
            logged.assert_called_once_with('worker_startup_or_runtime_failed')
            serve.assert_not_called()
