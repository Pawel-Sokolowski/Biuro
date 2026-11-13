"""Provisioning regressions: special-character passwords, key mismatch and no job execution."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.parse import urlsplit, unquote
from cryptography.fernet import Fernet
from app.services.worker_setup import database_url, save_local_config, _check_tokens


class WorkerSetupTests(TestCase):
    def test_postgres_credentials_are_encoded_and_ipv6_is_supported(self):
        password = "a@b:/#%?${HOME}'"
        url = database_url('192.168.1.20', 5432, 'biuro test', 'user@office', password)
        parsed = urlsplit(url)
        self.assertEqual(unquote(parsed.password), password)
        self.assertEqual(unquote(parsed.username), 'user@office')
        self.assertEqual(parsed.hostname, '192.168.1.20')
        self.assertEqual(unquote(parsed.path), '/biuro test')
        self.assertEqual(urlsplit(database_url('::1', 5432, 'biuro', 'u', 'p')).hostname, '::1')
        with self.assertRaises(ValueError):
            database_url('https://server', 5432, 'biuro', 'u', 'p')

    def test_worker_config_round_trips_without_modifying_desktop_config(self):
        from app.utils.env_config import load_environment
        with TemporaryDirectory() as folder, patch.dict(os.environ), \
             patch('app.services.worker_setup.worker_env_path', return_value=Path(folder) / 'worker.env'), \
             patch('app.db.dao_conn.close_all_pools'):
            desktop = Path(folder) / '.env'
            desktop.write_text('DB_URL=desktop\n', encoding='utf-8')
            dsn = database_url('localhost', 5432, 'biuro', 'u', "a${HOME}#'@")
            key = Fernet.generate_key().decode()
            save_local_config(dsn, key)
            os.environ['BIO_WORKER_MODE'] = '1'
            os.environ['DB_URL'] = 'inherited-other-office'
            self.assertEqual(load_environment(), [Path(folder) / 'worker.env'])
            self.assertEqual(os.environ['DB_URL'], dsn)
            self.assertEqual(os.environ['BIO_ENCRYPTION_KEY'], key)
            self.assertEqual(desktop.read_text(), 'DB_URL=desktop\n')
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ['.env', 'worker.env'])

    def test_key_mismatch_checks_email_and_service_credentials_without_network(self):
        key = Fernet.generate_key()
        token = Fernet(key).encrypt(b'secret').decode()
        conn = Mock()
        conn.execute.return_value.fetchall.side_effect = [
            [(1, 'office@example.test', token)], [('ezla_service', '{"password_enc":"' + token + '"}')]]
        self.assertEqual(_check_tokens(conn, key.decode()), 2)
        conn.execute.return_value.fetchall.side_effect = [[(1, 'office@example.test', token)], []]
        with self.assertRaisesRegex(ValueError, 'Klucz nie odszyfrowuje'):
            _check_tokens(conn, Fernet.generate_key().decode())

    def test_check_config_cli_never_starts_worker_or_qt(self):
        from app.worker import main
        with patch('app.utils.env_config.ensure_encryption_key'), \
             patch('app.services.worker_setup.preflight') as preflight, \
             patch('app.worker.serve') as serve, patch('app.diagnostics.configure_logging'):
            self.assertEqual(main(['--check-config']), 0)
            preflight.assert_called_once()
            serve.assert_not_called()

    def test_task_password_is_in_stdin_and_not_command_line_or_files(self):
        from app.services.worker_windows import manage_task
        with patch('app.services.worker_windows.os', SimpleNamespace(name='nt')), \
             patch('app.services.worker_windows.sys.frozen', True, create=True), \
             patch('app.services.worker_windows.identity', return_value=['OFFICE\\user', 'S-1-5-21-1']), \
             patch('app.services.worker_setup.worker_env_path') as config, \
             patch('app.services.worker_setup.preflight') as preflight, \
             patch('app.services.worker_windows.subprocess.CREATE_NO_WINDOW', 0, create=True), \
             patch('app.services.worker_windows.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='ok')) as run:
            config.return_value.is_file.return_value = True
            manage_task('install', 'private-password', expected_sid='S-1-5-21-1')
            args, kwargs = run.call_args
            self.assertNotIn('private-password', repr(args))
            self.assertIn('private-password', kwargs['input'])
            preflight.assert_called_once()

    def test_diagnostics_identify_all_unreadable_records_without_secrets(self):
        from app.services.worker_setup import CredentialKeyError
        key = Fernet.generate_key()
        readable = Fernet(key).encrypt(b'good-secret').decode()
        unreadable = Fernet(Fernet.generate_key()).encrypt(b'old-secret').decode()
        conn = Mock()
        conn.execute.return_value.fetchall.side_effect = [
            [(1,'good@example.test',readable),(2,'old@example.test',unreadable)],
            [('enova_connection','{"password_enc":"' + unreadable + '"}')]]
        with self.assertRaises(CredentialKeyError) as caught:
            _check_tokens(conn,key.decode())
        error = caught.exception
        self.assertEqual(len(error.failed),2)
        self.assertEqual(error.checked,3)
        message = str(error)
        self.assertIn('old@example.test',message)
        self.assertIn('enova_connection',message)
        self.assertNotIn(key.decode(),message)
        self.assertNotIn(unreadable,message)
        self.assertNotIn('old-secret',message)

    def test_key_import_updates_worker_config_and_survives_reload(self):
        from app.utils.env_config import install_encryption_key, load_environment
        with TemporaryDirectory() as folder, patch.dict(os.environ), \
             patch('app.services.worker_setup.worker_env_path', return_value=Path(folder)/'worker.env'), \
             patch('app.utils.env_config.user_env_path', return_value=Path(folder)/'.env'):
            worker = Path(folder)/'worker.env'
            worker.write_text('DB_URL=postgresql://server/biuro\n',encoding='utf-8')
            os.environ['BIO_WORKER_MODE'] = '1'
            key = Fernet.generate_key().decode()
            install_encryption_key(key)
            self.assertFalse((Path(folder)/'.env').exists())
            os.environ['BIO_ENCRYPTION_KEY'] = 'inherited-old-key'
            load_environment()
            self.assertEqual(os.environ['BIO_ENCRYPTION_KEY'],key)
            self.assertIn('DB_URL=postgresql://server/biuro',worker.read_text())

    def test_explicit_desktop_key_import_wins_over_inherited_old_key_on_restart(self):
        from app.utils.env_config import install_encryption_key, load_environment
        with TemporaryDirectory() as folder, patch.dict(os.environ), \
             patch('app.utils.env_config.user_env_path', return_value=Path(folder)/'.env'), \
             patch('app.utils.env_config.env_search_paths', return_value=[Path(folder)/'.env']):
            os.environ.pop('BIO_WORKER_MODE',None)
            key = Fernet.generate_key().decode()
            install_encryption_key(key)
            os.environ['BIO_ENCRYPTION_KEY'] = 'inherited-old-key'
            load_environment()
            self.assertEqual(os.environ['BIO_ENCRYPTION_KEY'],key)
