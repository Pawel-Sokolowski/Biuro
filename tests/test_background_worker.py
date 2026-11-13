import json
import os
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch, Mock
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

    def test_enova_runs_after_daily_time_and_retries_failures(self):
        from app.worker import enova_job
        from datetime import datetime
        from zoneinfo import ZoneInfo
        day = lambda hour: datetime(2026, 11, 12, hour, tzinfo=ZoneInfo('Europe/Warsaw'))
        with patch('app.integrations.enova.connection_settings.load_connection', return_value={
                'enabled': True,'sources':[{'nip':'1112223344','database':'ClientA'}]}), \
                patch('app.integrations.enova.document_counts.run_import') as importer:
            self.assertEqual(enova_job(day(1))['mode'], 'waiting')
            importer.assert_not_called()
            importer.return_value = [{'ok': False}]
            with self.assertRaises(RuntimeError):
                enova_job(day(3))
            importer.return_value = [{'ok': True}]
            self.assertEqual(enova_job(day(3))['companies'], 1)
            self.assertEqual(enova_job(day(4))['mode'], 'checked_today')
            self.assertEqual(importer.call_count, 2)

    def test_changing_jpk_query_invalidates_daily_worker_marker(self):
        from app.worker import enova_job
        from datetime import datetime
        from zoneinfo import ZoneInfo
        config = {'enabled': True, 'checking_enabled': True, 'sources': [
            {'nip': '1112223344', 'database': 'ClientA', 'jpk_query': 'first.sql'}]}
        now = datetime(2026, 11, 12, 13, tzinfo=ZoneInfo('Europe/Warsaw'))
        with patch('app.integrations.enova.connection_settings.load_connection', return_value=config), \
                patch('app.integrations.enova.document_counts.run_import', return_value=[{'ok': True}]) as importer, \
                patch('app.integrations.enova.checking.run_checking_import', return_value=[{'ok': True}]):
            enova_job(now)
            self.assertEqual(enova_job(now)['mode'], 'checked_today')
            config['sources'][0]['jpk_query'] = 'replacement.sql'
            enova_job(now)
            self.assertEqual(importer.call_count, 2)

    def test_enova_connection_without_company_mapping_does_not_consume_daily_run(self):
        from app.worker import enova_job
        from app.db.dao_app_settings import get_setting
        from datetime import datetime
        from zoneinfo import ZoneInfo
        with patch('app.integrations.enova.connection_settings.load_connection',return_value={'enabled':True,'sources':[]}), \
             patch('app.integrations.enova.document_counts.run_import') as importer:
            with self.assertRaisesRegex(ValueError,'brak przypisanych baz'):
                enova_job(datetime(2026,11,12,13,tzinfo=ZoneInfo('Europe/Warsaw')))
        importer.assert_not_called()
        self.assertIsNone(get_setting('worker_enova_completed'))

    def test_missing_key_in_headless_worker_is_logged_and_returns_failure(self):
        from app.worker import main
        with patch('app.utils.env_config.ensure_encryption_key', side_effect=ValueError('Shared key missing')), \
             patch('app.diagnostics.configure_logging'), patch('app.worker.logger.exception') as logged, \
             patch('app.worker.serve') as serve:
            self.assertEqual(main(['--once']), 1)
            logged.assert_called_once_with('worker_startup_or_runtime_failed')
            serve.assert_not_called()
