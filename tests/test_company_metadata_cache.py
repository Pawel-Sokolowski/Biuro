"""Company searches reuse independent backup metadata, not stale company rows."""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication
from app.companies import company_ui

_app = QApplication.instance() or QApplication(['company-cache-tests'])


class MetadataCacheTests(unittest.TestCase):
    def test_cache_expires_and_does_not_cross_database_scopes(self):
        cache = company_ui._BackupMetadataCache(ttl=30)
        load = mock.Mock(side_effect=['first', 'expired', 'other database'])
        with mock.patch.object(company_ui, 'monotonic', return_value=10) as clock:
            self.assertEqual(cache.read('one', load), 'first')
            clock.return_value = 39
            self.assertEqual(cache.read('one', load), 'first')
            self.assertEqual(load.call_count, 1)
            clock.return_value = 41
            self.assertEqual(cache.read('one', load), 'expired')
            self.assertEqual(cache.read('two', load), 'other database')
        self.assertEqual(load.call_count, 3)

    def test_concurrent_searches_share_a_metadata_read(self):
        cache = company_ui._BackupMetadataCache()
        started, release = threading.Event(), threading.Event()

        def load():
            started.set()
            self.assertTrue(release.wait(5))
            return 'snapshot'

        loader = mock.Mock(side_effect=load)
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(cache.read, 'office', loader)
            self.assertTrue(started.wait(5))
            second = pool.submit(cache.read, 'office', loader)
            release.set()
            self.assertEqual(first.result(5), 'snapshot')
            self.assertEqual(second.result(5), 'snapshot')
        self.assertEqual(loader.call_count, 1)

    def test_invalidated_inflight_read_cannot_replace_fresh_metadata(self):
        cache = company_ui._BackupMetadataCache()
        started, release = threading.Event(), threading.Event()

        def old_load():
            started.set()
            self.assertTrue(release.wait(5))
            return 'old snapshot'

        with ThreadPoolExecutor(1) as pool:
            old = pool.submit(cache.read, 'office', old_load)
            self.assertTrue(started.wait(5))
            cache.invalidate()
            self.assertEqual(cache.read('office', lambda: 'new snapshot'), 'new snapshot')
            release.set()
            self.assertEqual(old.result(5), 'old snapshot')
        unexpected = mock.Mock(side_effect=AssertionError('fresh cache should be retained'))
        self.assertEqual(cache.read('office', unexpected), 'new snapshot')

    def test_failed_load_is_not_cached(self):
        cache = company_ui._BackupMetadataCache()
        with self.assertRaisesRegex(OSError, 'offline'):
            cache.read('office', mock.Mock(side_effect=OSError('offline')))
        self.assertEqual(cache.read('office', lambda: 'retried'), 'retried')


class CompanySearchMetadataTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.object(company_ui.QTimer, 'singleShot'):
            self.page = company_ui.CompanyUI(1)
        self.addCleanup(self.page.close)
        self.page._reads.submit = lambda read, done, error: done(read())
        self.row = {'id': 7, 'nazwa': 'Full legal name', 'nazwa_skrocona': 'Surname Name', 'nip': '1234563218'}
        for target, value in (('get_dsn', 'review database'), ('get_schema', 'test schema')):
            patcher = mock.patch.object(company_ui, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_typing_requeries_company_rows_but_reads_backup_metadata_once(self):
        with mock.patch.object(company_ui, 'get_companies_filtered', return_value=[self.row]) as companies, \
                mock.patch.object(self.page, '_backup_by_company', return_value={}) as backups, \
                mock.patch('app.utils.backup_status.stored_backup_dates', return_value={}) as stored:
            for search in ('S', 'Su', 'Surname'):
                self.page.search_input.setText(search)
                self.page.refresh_async()
        self.assertEqual([call.args[0] for call in companies.call_args_list], ['S', 'Su', 'Surname'])
        self.assertEqual(backups.call_count, 1)
        self.assertEqual(stored.call_count, 1)

    def test_explicit_refresh_reloads_metadata_and_updates_the_backup_column(self):
        with mock.patch.object(company_ui, 'get_companies_filtered', return_value=[self.row]), \
                mock.patch.object(self.page, '_backup_by_company', return_value={}) as backups, \
                mock.patch('app.utils.backup_status.stored_backup_dates', side_effect=[
                    {7: ('2026-11-07', '2026-11-07 09:00')},
                    {7: ('2026-11-08', '2026-11-08 09:00')},
                ]) as stored:
            self.page.refresh_async()
            self.assertEqual(self.page.table.item(0, 13).text(), '2026-11-07')
            self.page.btn_refresh.click()
            self.assertEqual(self.page.table.item(0, 13).text(), '2026-11-08')
        self.assertEqual(backups.call_count, 2)
        self.assertEqual(stored.call_count, 2)

    def test_identical_records_keep_items_and_selection_but_changed_values_render(self):
        self.page._load_table([self.row], {}, {})
        selected = self.page.table.item(0, 1)
        self.page.table.selectRow(0)
        self.page._load_table([dict(self.row)], {}, {})
        self.assertIs(self.page.table.item(0, 1), selected)
        self.assertEqual(self.page.table.currentRow(), 0)
        self.row['nazwa_skrocona'] = 'Updated surname'
        self.page._load_table([self.row], {}, {})
        self.assertEqual(self.page.table.item(0, 1).text(), 'Updated surname')

    def test_editing_company_invalidates_metadata_name_mapping(self):
        self.page._load_table([self.row], {}, {})
        self.page.table.selectRow(0)
        with mock.patch.object(company_ui, 'CompanyForm') as form, \
                mock.patch.object(company_ui, 'get_companies_filtered', return_value=[self.row]), \
                mock.patch.object(self.page, '_backup_by_company', return_value={}) as backups, \
                mock.patch('app.utils.backup_status.stored_backup_dates', return_value={}) as stored:
            form.return_value.exec.return_value = True
            self.page.refresh_async()
            self.page._edit_company()
        self.assertEqual(backups.call_count, 2)
        self.assertEqual(stored.call_count, 2)
        form.assert_called_once_with(7, self.page, actor_id=1)
