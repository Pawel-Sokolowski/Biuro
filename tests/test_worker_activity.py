import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from unittest import TestCase
from unittest.mock import patch
from app.db.dao_app_settings import get_setting
from app.db.dao_conn import get_conn
from app.services.worker_activity import KEY, batch, record
from tests.base import DbTestCase


class ActivityTests(DbTestCase):
    def events(self):
        return json.loads(get_setting(KEY) or '[]')

    def test_progress_is_visible_before_import_transaction_commits_and_survives_rollback(self):
        with self.assertRaises(RuntimeError):
            with get_conn() as importing:
                importing.execute("INSERT INTO app_settings VALUES ('uncommitted_import', 'pending')")
                record('ezla', 'downloading', company_id=123)
                with get_conn() as reader:
                    row = reader.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (KEY,)).fetchone()
                    self.assertEqual(json.loads(row[0])[-1]['stage'], 'downloading')
                    self.assertIsNone(reader.execute("SELECT 1 FROM app_settings WHERE klucz='uncommitted_import'").fetchone())
                raise RuntimeError('rollback import')
        self.assertEqual(self.events()[-1]['company_id'], 123)

    def test_concurrent_jobs_do_not_lose_progress_and_history_is_bounded(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(copy_context().run, record, 'enova', 'reading', company_id=fid)
                       for fid in range(8)]
            for future in futures:
                future.result(timeout=30)
        self.assertEqual({event['company_id'] for event in self.events()}, set(range(8)))
        with patch('app.services.worker_activity.LIMIT', 3):
            record('ezla', 'downloaded', company_id=9, count=0)
        self.assertEqual(len(self.events()), 3)
        self.assertEqual(self.events()[-1]['count'], 0)

    def test_failed_batch_never_claims_saved_and_preserves_original_error(self):
        with self.assertRaisesRegex(ValueError, 'original failure'):
            with batch('platnik', [1, 2]):
                raise ValueError('original failure')
        self.assertEqual([event['stage'] for event in self.events()], ['reading', 'reading', 'failed', 'failed'])


class ActivityFailureTests(TestCase):
    def test_monitor_failure_does_not_break_import_or_log_exception_secrets(self):
        with patch('app.services.worker_activity.get_activity_conn', side_effect=RuntimeError('service-secret')):
            with self.assertLogs('app.services.worker_activity', level='WARNING') as logs:
                record('ezla', 'downloaded', company_id=1, count=0)
        self.assertNotIn('service-secret', '\n'.join(logs.output))
