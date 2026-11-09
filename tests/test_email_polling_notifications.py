"""Automatic mailbox scheduling, live assignment badges and notification links."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from contextlib import nullcontext
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QWidget,
                             QTableWidget, QTableWidgetItem, QComboBox, QTabWidget)
from app.email import email_sync, email_ui
from app.notifications import NotificationCenter, list_user_notifications
from app.tasks.tasks_ui import TasksPage


class AutomaticSyncTests(unittest.TestCase):
    def setUp(self):
        patcher=patch.object(email_sync,'account_sync_lock',side_effect=lambda *a, **kw: nullcontext(True))
        patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(email_sync,'sync_folder_flags',return_value=0)
        patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(email_sync,'needs_initial_sync',return_value=False)
        patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(email_sync,'_shared_session',side_effect=ConnectionError('no network in tests'))
        patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(email_sync,'get_setting',return_value=None)
        patcher.start();self.addCleanup(patcher.stop)

    def test_every_selectable_folder_and_account_is_checked_before_body_prefetch(self):
        events=[]
        def sync(account,folders,**kwargs):
            self.assertTrue(kwargs['headers_only'])
            events.append(('headers',account,list(folders)))
        with patch.object(email_sync,'list_folders_from_imap_and_store',return_value=True), \
             patch.object(email_sync,'selectable_folder_names',side_effect=lambda account:['INBOX','Clients','Archive']), \
             patch.object(email_sync,'sync_and_prefetch',side_effect=sync), \
             patch.object(email_sync,'prefetch_recent',side_effect=lambda account,folder,limit:events.append(('body',account,folder))):
            errors=email_sync.sync_automatic_accounts([{'id':1},{'id':2}],1,'Clients')
        self.assertFalse(errors)
        header_events=[event for event in events if event[0]=='headers']
        self.assertEqual({event[1] for event in header_events},{1,2})
        self.assertEqual(next(event[2] for event in header_events if event[1]==1),['Clients','INBOX','Archive'])
        self.assertTrue(all(set(event[2])=={'INBOX','Clients','Archive'} for event in header_events))
        self.assertEqual([event[0] for event in events[:2]],['headers','headers'])

    def test_busy_manual_account_does_not_block_repeated_automatic_passes(self):
        acquired = threading.Event()
        release = threading.Event()
        def manual():
            with email_sync._account_sync_lock(901):
                acquired.set()
                release.wait(5)
        worker = threading.Thread(target=manual)
        worker.start()
        self.assertTrue(acquired.wait(2))
        try:
            with patch.object(email_sync, 'list_folders_from_imap_and_store', return_value=True) as discovery, patch.object(email_sync, 'selectable_folder_names', return_value=['INBOX']), patch.object(email_sync, 'sync_and_prefetch') as sync, patch.object(email_sync, 'prefetch_recent') as bodies:
                for _ in range(2):
                    self.assertEqual(email_sync.sync_automatic_accounts([{'id':901},{'id':902}]), [])
                self.assertEqual([call.args[0] for call in discovery.call_args_list], [902,902])
                self.assertEqual([call.args[0] for call in sync.call_args_list], [902,902])
                self.assertEqual([call.args[0] for call in bodies.call_args_list], [902,902])
        finally:
            release.set()
            worker.join(2)

    def test_slow_account_does_not_prevent_another_account_sync(self):
        second_done=threading.Event()
        def sync(account,*args,**kwargs):
            if account==1:
                self.assertTrue(second_done.wait(2),'Second account was blocked behind the slow account')
            else:
                second_done.set()
        with patch.object(email_sync,'list_folders_from_imap_and_store',return_value=True), \
             patch.object(email_sync,'selectable_folder_names',return_value=['INBOX']), \
             patch.object(email_sync,'sync_and_prefetch',side_effect=sync), \
             patch.object(email_sync,'prefetch_recent',return_value=0):
            self.assertEqual(email_sync.sync_automatic_accounts([{'id':1},{'id':2}]),[])

    def test_automatic_sync_never_starts_full_download_even_with_incomplete_initial_cache(self):
        with patch.object(email_sync,'needs_initial_sync',return_value=True) as initial, \
             patch.object(email_sync,'set_setting') as setting, \
             patch.object(email_sync,'list_folders_from_imap_and_store',return_value=True), \
             patch.object(email_sync,'selectable_folder_names',return_value=['INBOX','Clients']), \
             patch.object(email_sync,'sync_and_prefetch') as sync, \
             patch.object(email_sync,'prefetch_recent',return_value=0):
            self.assertEqual(email_sync.sync_automatic_accounts([{'id':1}]),[])
        initial.assert_not_called()
        self.assertTrue(all(c.args[0].startswith(('email_full_sweep_', 'email_status_snapshot_')) for c in setting.call_args_list))
        self.assertFalse(sync.call_args.kwargs['first_run'])
        self.assertTrue(sync.call_args.kwargs['headers_only'])

    def test_only_folders_with_changed_status_are_synced(self):
        status = {'INBOX': 'MESSAGES=5,UIDNEXT=6', 'Clients': 'MESSAGES=2,UIDNEXT=3', 'Archive': 'MESSAGES=9,UIDNEXT=10'}
        saved = {}
        email_sync._auto_runs.pop(77, None)
        class Ctx:
            def __enter__(self): return object()
            def __exit__(self, *a): return False
        def run():
            with patch.object(email_sync, 'list_folders_from_imap_and_store', return_value=True), \
                 patch.object(email_sync, 'selectable_folder_names', return_value=list(status)), \
                 patch.object(email_sync, '_shared_session', return_value=Ctx()), \
                 patch.object(email_sync, '_folder_status', side_effect=lambda s, f: status[f]), \
                 patch.object(email_sync, 'get_setting', side_effect=lambda k: saved.get(k)), \
                 patch.object(email_sync, 'set_setting', side_effect=lambda k, v: saved.__setitem__(k, v)), \
                 patch.object(email_sync, 'sync_folder_flags', return_value=0) as flags, \
                 patch.object(email_sync, 'sync_and_prefetch') as sync, \
                 patch.object(email_sync, 'prefetch_recent', return_value=0):
                self.assertEqual(email_sync.sync_automatic_accounts([{'id': 77}]), [])
                self.assertEqual([call.args[1] for call in flags.call_args_list], list(status))
                return [call.args[1] for call in sync.call_args_list]
        self.assertEqual(run(), [['INBOX', 'Clients', 'Archive']])
        self.assertEqual(run(), [])
        status['Clients'] = 'MESSAGES=3,UIDNEXT=4'
        self.assertEqual(run(), [['Clients']])
        # Read flags were checked on every pass above. The periodic header
        # sweep still runs after ten passes even without a STATUS change.
        for _ in range(7):
            self.assertEqual(run(), [])
        self.assertEqual(run(), [['INBOX', 'Clients', 'Archive']])


    def test_manual_sync_requests_all_two_year_messages_only_for_selected_accounts(self):
        with patch.object(email_sync,'set_setting') as setting, patch.object(email_sync,'sync_and_prefetch') as sync:
            self.assertEqual(email_sync.sync_requested_accounts([{'id':2,'email':'b@example.com'}]),[])
        setting.assert_called_once_with('email_initial_sync_pending_2','1')
        self.assertEqual(sync.call_args.args,(2,['INBOX']))
        self.assertTrue(sync.call_args.kwargs['first_run'])
        self.assertFalse(sync.call_args.kwargs.get('headers_only',False))

    def test_failed_discovery_preserves_cache_and_other_accounts_continue(self):
        with patch.object(email_sync,'list_folder_infos',return_value=[]), patch.object(email_sync,'_store_folders') as store:
            self.assertFalse(email_sync.list_folders_from_imap_and_store(1))
            store.assert_not_called()
        with patch.object(email_sync,'delete_missing_folders') as delete:
            email_sync._store_folders(1,[])
            delete.assert_not_called()
        with patch.object(email_sync,'list_folders_from_imap_and_store',side_effect=lambda account:account!=1), \
             patch.object(email_sync,'selectable_folder_names',return_value=['INBOX']), \
             patch.object(email_sync,'sync_and_prefetch') as sync, \
             patch.object(email_sync,'prefetch_recent',return_value=0):
            errors=email_sync.sync_automatic_accounts([{'id':1},{'id':2}])
        self.assertEqual(len(errors),1)
        self.assertIn('#1',errors[0])
        self.assertEqual(sync.call_args.args[0],2)

    def test_headers_only_pass_does_not_download_bodies_or_expand_history(self):
        with patch.object(email_sync,'get_folders_for_account',return_value=[{'server_name':'INBOX'}]), \
             patch.object(email_sync,'sync_folder',return_value=3) as sync, \
             patch.object(email_sync,'prefetch_messages') as prefetch, \
             patch('app.utils.saldeo_status.apply_saldeo_emails'):
            count=email_sync.sync_and_prefetch(1,['INBOX','Clients'],headers_only=True)
        self.assertEqual(count,6)
        self.assertTrue(all(not call.kwargs['full_sync'] for call in sync.call_args_list))
        prefetch.assert_not_called()

    def test_connection_failure_is_reported_and_does_not_look_like_empty_mailbox(self):
        with patch.object(email_sync,'_get_account_info',return_value={'id':1}), \
             patch.object(email_sync,'_connect_imap',return_value=None), self.assertRaises(ConnectionError):
            email_sync.fetch_headers(1,'INBOX')
        with patch.object(email_sync,'get_folders_for_account',return_value=[{'server_name':'INBOX'}]), \
             patch.object(email_sync,'sync_folder',side_effect=ConnectionError('offline')), \
             patch('app.utils.saldeo_status.apply_saldeo_emails'), \
             self.assertLogs('app.email.email_sync',level='WARNING'), self.assertRaises(RuntimeError):
            email_sync.sync_and_prefetch(1,['INBOX'],headers_only=True)


class BadgeAndNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        settings=QSettings(str(Path(self.temp.name)/'preferences.ini'),QSettings.Format.IniFormat)
        patcher=patch('app.notifications.QSettings',return_value=settings)
        patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(NotificationCenter,'refresh')
        patcher.start();self.addCleanup(patcher.stop)

    def center(self,actor,navigate):
        center=NotificationCenter(actor,navigate)
        center.timer.stop()
        self.addCleanup(center.close)
        return center

    def test_notification_click_navigates_and_seen_state_is_isolated(self):
        target={'kind':'task','task_id':8}
        entry={'key':'task:8:date','kind':'task','title':'Przypisane zadanie: Test','target':target}
        navigate=Mock(return_value=True)
        center=self.center(1,navigate)
        center._loaded([entry],'')
        self.assertIn('(1)',center.text())
        center._open(entry)
        navigate.assert_called_once_with(target)
        self.assertNotIn('(1)',center.text())
        reopened=self.center(1,navigate);reopened._loaded([entry],'')
        self.assertNotIn('(1)',reopened.text())
        other=self.center(2,navigate);other._loaded([entry],'')
        self.assertIn('(1)',other.text())

    def test_unavailable_target_remains_unseen_and_mark_all_does_not_open_it(self):
        entry={'key':'email:9','kind':'email','title':'Przypisany e-mail: Test','target':{'kind':'email'}}
        navigate=Mock(return_value=False)
        center=self.center(1,navigate);center._loaded([entry],'')
        center._open(entry)
        self.assertIn('(1)',center.text())
        center.mark_all_seen()
        self.assertEqual(navigate.call_count,1)
        self.assertNotIn('(1)',center.text())

    def test_unread_count_changes_invalidate_employee_chip_cache(self):
        page=email_ui.EmailPage.__new__(email_ui.EmailPage);QWidget.__init__(page)
        self.addCleanup(page.close)
        page.current_account={'id':1}
        page.employee_filter=None;page.completed_filter=False;page._chips_signature=None
        page.employees=[{'id':7,'imie':'Jan','nazwisko':'Kowalski'}]
        page.label_numbers={7:1};page.chips_layout=QHBoxLayout(page);page.chips_group=QButtonGroup(page)
        with patch.object(email_ui,'count_unread_assigned_by_employee',return_value={7:4}):
            page._refresh_chips()
        self.assertEqual(page.chips_layout.itemAt(2).widget().text(),'Jan Kowalski (4)')
        with patch.object(email_ui,'count_unread_assigned_by_employee',return_value={7:3}):
            page._refresh_chips()
        self.assertEqual(page.chips_layout.itemAt(2).widget().text(),'Jan Kowalski (3)')

    def test_only_allowed_modules_and_open_assigned_tasks_generate_notifications(self):
        tasks=[{'id':1,'tytul':'Do zrobienia','status':'Nowe','updated_at':'2026-11-01'},
               {'id':2,'tytul':'Koniec','status':'Zakończone','updated_at':'2026-11-01'}]
        with patch('app.notifications.allowed_tabs',return_value=['Zadania']), \
             patch('app.notifications.list_tasks',return_value=tasks) as query, \
             patch('app.notifications.get_conn') as conn:
            entries=list_user_notifications(7)
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['target'],{'kind':'task','task_id':1})
        query.assert_called_once_with(pracownik_id=7,viewer_id=7)
        conn.assert_not_called()

    def test_task_notification_selects_the_task_in_main_list(self):
        page=TasksPage.__new__(TasksPage);QWidget.__init__(page);self.addCleanup(page.close)
        page.pracownik_id=7
        page.tabs=QTabWidget();page.tabs.addTab(QWidget(),'Zadania')
        page.status_filter=QComboBox();page.status_filter.addItem('Wszystkie',None)
        page.assignee_filter=QComboBox();page.assignee_filter.addItem('Wszyscy',None);page.assignee_filter.addItem('Jan Kowalski',7)
        page.table=QTableWidget(1,6)
        for column in range(6):page.table.setItem(0,column,QTableWidgetItem('Zadanie'))
        page.table.item(0,0).setData(Qt.ItemDataRole.UserRole,8)
        page._refresh=Mock()
        self.assertTrue(page.show_assignment_notification(8))
        self.assertEqual(page.table.currentRow(),0)
        self.assertEqual(page.assignee_filter.currentData(),7)

    def test_mail_poll_requests_do_not_start_overlapping_workers(self):
        page=email_ui.EmailPage.__new__(email_ui.EmailPage);QWidget.__init__(page);self.addCleanup(page.close)
        page._sync_running=True;page._resync_pending=False;page._sync_cancel=threading.Event()
        with patch.object(email_ui.threading,'Thread') as thread:
            page.start_background_sync()
        self.assertTrue(page._resync_pending)
        thread.assert_not_called()
        self.assertEqual(email_ui.AUTO_SYNC_INTERVAL_MS,120000)
