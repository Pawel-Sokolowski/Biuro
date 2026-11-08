"""Background refreshes must update data without replacing the user's context."""
import time
import unittest
from copy import deepcopy
from datetime import date
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QListWidget, QLabel, QTextBrowser, QTableView, QWidget, QAbstractItemView

from app.db.dao_conn import get_conn
from app.email import email_storage as storage, email_ui
from app.monthly_view.monthly_view_model import MonthlyViewModel
from app.utils.view_state import reconcile_list
from tests.base import DbTestCase
from tests.test_review_followups import MailboxFixture
from tests.test_review_optimizations import wait_until

_app = QApplication.instance() or QApplication(['refresh-state'])
ROLE = Qt.ItemDataRole.UserRole


class ViewStateTests(unittest.TestCase):
    def test_background_monthly_update_waits_for_the_active_editor(self):
        from app.monthly_view.monthly_view_ui import MonthlyViewPage
        page = MonthlyViewPage.__new__(MonthlyViewPage)
        QWidget.__init__(page)
        self.addCleanup(page.close)
        page.table_ks, page.table_kadry = QTableView(page), QTableView(page)
        with mock.patch.object(page, '_reload_rows') as reload:
            with mock.patch.object(page.table_ks, 'state', return_value=QAbstractItemView.State.EditingState):
                page.refresh_background()
                timer = page._background_refresh_timer
                page.refresh_background()
                self.assertIs(page._background_refresh_timer, timer)
                reload.assert_not_called()
            page.refresh_background()
            reload.assert_called_once()
            self.assertFalse(timer.isActive())

    def test_list_updates_reuse_items_and_anchor_selection_by_id(self):
        view = QListWidget()
        self.addCleanup(view.close)
        view.resize(400, 240)
        view.show()
        records = [(i, f'Item {i}') for i in range(100)]
        def render(item, entry):
            item.setText(entry[1])
        reconcile_list(view, records, lambda entry: entry[0], render)
        view.setCurrentRow(55)
        view.scrollToItem(view.item(50), view.ScrollHint.PositionAtTop)
        _app.processEvents()
        current = view.currentItem()
        anchor = view.itemAt(4, 1).data(ROLE)
        reconcile_list(view, [(-1, 'New'), *records], lambda entry: entry[0], render)
        self.assertIs(view.currentItem(), current)
        self.assertEqual(view.itemAt(4, 1).data(ROLE), anchor)
        reconcile_list(view, [entry for entry in records if entry[0] != 55], lambda entry: entry[0], render)
        self.assertEqual(view.selectedItems(), [])
        self.assertIsNone(view.currentItem())

    def test_monthly_unchanged_rows_emit_no_changes_and_updates_are_limited(self):
        rows = [{'id': 1, 'nazwa': 'A'}, {'id': 2, 'nazwa': 'B'}]
        model = MonthlyViewModel(deepcopy(rows), 'ksiegowosc')
        changes, resets = QSignalSpy(model.dataChanged), QSignalSpy(model.modelReset)
        model.set_rows(deepcopy(rows))
        self.assertEqual((len(changes), len(resets)), (0, 0))
        rows[1]['nazwa'] = 'Changed'
        model.set_rows(rows)
        self.assertEqual(len(changes), 1)
        self.assertEqual((changes[0][0].row(), changes[0][1].row()), (1, 1))


class MailRefreshTests(MailboxFixture):
    def setUp(self):
        super().setUp()
        self.add_messages(150)
        for i in range(40):
            storage.save_folder(1, f'INBOX.Folder{i:02}', delimiter='.')
        self.real_sync = email_ui.EmailPage.start_background_sync
        for patcher in (
            mock.patch.object(email_ui, 'get_email_accounts_for_employee', return_value=[{'id': 1, 'email': 'review@example.com'}]),
            mock.patch.object(email_ui.EmailPage, 'start_background_sync'),
            mock.patch.object(email_ui, 'MailBrowser', QTextBrowser),
            mock.patch.object(email_ui.EmailPage, '_run_async'),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = email_ui.EmailPage(1)
        self.addCleanup(self.page.close)
        self.addCleanup(self.page.stop_background_sync)
        self.page.current_folder_local = 'INBOX'
        self.page.load_mail_list_from_db('INBOX')
        self.page.resize(1400, 700)
        self.page.show()
        _app.processEvents()

    def folder_items(self):
        result = {}
        def walk(item):
            result[self.page._folder_key(item)] = item
            for row in range(item.childCount()):
                walk(item.child(row))
        for row in range(self.page.folder_tree.topLevelItemCount()):
            walk(self.page.folder_tree.topLevelItem(row))
        return result

    def test_unread_updates_reuse_folder_nodes_and_keep_scroll_and_collapse(self):
        self.page.load_folders_from_db()
        tree = self.page.folder_tree
        items = self.folder_items()
        anchor = items[1, 'INBOX.Folder25']
        tree.scrollToItem(anchor, tree.ScrollHint.PositionAtTop)
        _app.processEvents()
        scroll = tree.verticalScrollBar().value()
        removed = QSignalSpy(tree.model().rowsRemoved)
        with get_conn() as conn:
            conn.execute('UPDATE wiadomosci_email SET przeczytana = 1')
        self.page.load_folders_from_db()
        self.assertIs(self.folder_items()[1, 'INBOX.Folder25'], anchor)
        self.assertEqual(tree.verticalScrollBar().value(), scroll)
        self.assertEqual(len(removed), 0)
        self.assertNotIn('(150)', self.folder_items()[1, 'INBOX'].text(0))

    def test_folder_structure_changes_keep_top_folder_and_current_selection(self):
        tree = self.page.folder_tree
        items = self.folder_items()
        tree.blockSignals(True)
        tree.setCurrentItem(items[1, 'INBOX.Folder30'])
        tree.blockSignals(False)
        tree.scrollToItem(items[1, 'INBOX.Folder20'], tree.ScrollHint.PositionAtTop)
        _app.processEvents()
        anchor = self.page._folder_key(tree.itemAt(4, 1))
        storage.save_folder(1, 'INBOX.A-new', delimiter='.')
        self.page.load_folders_from_db()
        self.assertEqual(self.page._folder_key(tree.itemAt(4, 1)), anchor)
        self.assertEqual(self.page._folder_key(tree.currentItem()), (1, 'INBOX.Folder30'))

    def test_failed_folder_read_keeps_the_existing_tree(self):
        original = self.page.folder_tree.topLevelItem(0)
        with mock.patch.object(email_ui, 'get_folders_for_account', side_effect=RuntimeError('read failed')):
            self.page.load_folders_from_db()
        self.assertIs(self.page.folder_tree.topLevelItem(0), original)

    def test_sync_appends_new_mail_without_moving_the_reading_position_or_preview(self):
        view = self.page.mail_list
        view.blockSignals(True)
        view.setCurrentItem(view.topLevelItem(55))
        view.blockSignals(False)
        view.scrollTo(view.model().item_index(view.topLevelItem(45)), view.ScrollHint.PositionAtTop)
        _app.processEvents()
        anchor = view.itemAt(view.viewport().rect().topLeft()).data(0, email_ui.ROLE_ROW_ID)
        selected = {item.data(0, email_ui.ROLE_ROW_ID) for item in view.selectedItems()}
        current = view.currentItem()
        cached = object()
        self.page.cached_message = cached
        self.page._current_message_row_id = current.data(0, email_ui.ROLE_ROW_ID)
        self.page._last_account_check = time.monotonic()
        storage.save_message_headers(1, self.folder, [{'uid': '999', 'subject': 'New mail', 'date': '2026-12-01 10:00'}])
        with mock.patch.object(email_ui, 'sync_automatic_accounts', return_value=[]), \
                mock.patch.object(self.page._sync_jobs, 'submit', side_effect=lambda work: work()), \
                mock.patch.object(self.page, '_clear_view') as clear:
            self.real_sync(self.page)
            self.assertTrue(wait_until(lambda: self.page._pending_mail_load is None))
            clear.assert_not_called()
        self.assertIs(view.currentItem(), current)
        self.assertIs(self.page.cached_message, cached)
        self.assertEqual({item.data(0, email_ui.ROLE_ROW_ID) for item in view.selectedItems()}, selected)
        self.assertEqual(view.itemAt(view.viewport().rect().topLeft()).data(0, email_ui.ROLE_ROW_ID), anchor)

    def test_preserving_refresh_does_not_reopen_a_collapsed_thread(self):
        with get_conn() as conn:
            conn.execute("UPDATE wiadomosci_email SET refs = '<1@review>' WHERE uid = '2'")
        self.page.load_mail_list_from_db('INBOX')
        view = self.page.mail_list
        head = next(item for item in view.all_items() if item.childCount())
        view.blockSignals(True)
        view.setCurrentItem(head.child(0))
        head.setExpanded(False)
        view.blockSignals(False)
        self.page.load_mail_list_from_db('INBOX', keep_view=True)
        self.assertFalse(view.isExpanded(view.model().item_index(head)))
        storage.save_message_headers(1, self.folder, [{'uid': '999', 'subject': 'New', 'date': '2026-12-01 10:00'}])
        self.page.load_mail_list_from_db('INBOX', keep_view=True)
        self.assertFalse(view.isExpanded(view.model().item_index(head)))

    def test_assignment_count_updates_reuse_filter_buttons(self):
        buttons = dict(self.page._chips_buttons)
        row_id = self.page._mail_rows[0]['id']
        storage.set_employee_assignment([row_id], 1, True)
        self.page._refresh_chips()
        self.assertEqual(self.page._chips_buttons, buttons)
        self.assertIn('(1)', buttons[1].text())


class CalendarRefreshTests(DbTestCase):
    def setUp(self):
        super().setUp()
        from app.kalendarz import calendar_ui
        from app.db.dao_calendar import save_event
        self.ui = calendar_ui
        for patcher in (mock.patch.object(calendar_ui.google_sync, 'is_connected', return_value=True),
                        mock.patch.object(calendar_ui.caldav_sync, 'is_configured', return_value=False),
                        mock.patch.object(calendar_ui.CalendarPage, '_auto_sync')):
            patcher.start()
            self.addCleanup(patcher.stop)
        for n in range(35):
            save_event(1, {'tytul': f'Event {n:02}', 'start': '2026-11-05T10:00', 'koniec': '2026-11-05T11:00'})
        self.page = calendar_ui.CalendarPage(1)
        self.addCleanup(self.page.close)
        self.page._auto_timer.stop()
        self.page._goto(date(2026, 11, 5))
        self.page.resize(1000, 700)
        self.page.show()
        _app.processEvents()

    def test_refresh_keeps_unchecked_sources_and_reuses_source_items(self):
        local = self.page.calendars.item(0)
        local.setCheckState(Qt.CheckState.Unchecked)
        self.page._refresh_all()
        self.assertIs(self.page.calendars.item(0), local)
        self.assertEqual(local.checkState(), Qt.CheckState.Unchecked)
        self.assertEqual(self.page._events, [])

    def test_unchanged_sync_keeps_grid_and_event_widgets(self):
        tree = self.page.tree
        selected = tree.topLevelItem(25)
        tree.setCurrentItem(selected)
        tree.scrollToItem(tree.topLevelItem(20), tree.ScrollHint.PositionAtTop)
        cell = self.page.grid.itemAt(10).widget()
        scroll = tree.verticalScrollBar().value()
        self.page._refresh_all()
        self.assertIs(tree.currentItem(), selected)
        self.assertIs(self.page.grid.itemAt(10).widget(), cell)
        self.assertEqual(tree.verticalScrollBar().value(), scroll)

    def test_changed_event_keeps_selected_event_by_id(self):
        tree = self.page.tree
        selected = tree.topLevelItem(25)
        tree.setCurrentItem(selected)
        event_id = selected.data(0, ROLE)['id']
        with get_conn() as conn:
            conn.execute("UPDATE wydarzenia SET tytul = 'Updated event' WHERE id = %s", (event_id,))
        self.page._refresh_all()
        self.assertEqual(tree.currentItem().data(0, ROLE)['id'], event_id)
        self.assertEqual(self.page._sel_id, event_id)
        self.assertIn('Updated event', tree.currentItem().text(0))


class ChatAndDashboardRefreshTests(DbTestCase):
    def test_chat_poll_appends_without_replacing_message_widgets_or_input(self):
        from app.chat import chat_ui
        with mock.patch.object(chat_ui, 'get_conversations', return_value=[]), \
                mock.patch.object(chat_ui, 'get_direct_conversations', return_value=[]):
            page = chat_ui.ChatPage(1)
        self.addCleanup(page.close)
        page._poll_timer.stop()
        page.current_konwersacja = 100
        messages = [{'id': i, 'pracownik_id': 1, 'tresc': f'Message {i}\n' + 'Body\n' * 8,
                     'data_wysylki': '2026-11-05 10:00:00', 'imie': 'Jan'} for i in range(1, 101)]
        page.resize(1000, 700)
        page.show()
        with mock.patch.object(chat_ui, 'get_attachments', return_value={}):
            page._render_grouped_messages(messages, scroll_to_bottom=False)
            _app.processEvents()
            original = next(label for label in page.findChildren(QLabel) if label.text() == messages[0]['tresc'])
            page.chat_input.setPlainText('Draft in progress')
            bar = page.scroll_area.verticalScrollBar()
            bar.setValue(bar.maximum() // 2)
            scroll = bar.value()
            page._seen_ids[100] = 100
            added = {**messages[-1], 'id': 101, 'tresc': 'New message'}
            with mock.patch.object(chat_ui, 'is_participant', return_value=True), \
                    mock.patch.object(chat_ui, 'get_latest_message_ids', return_value={100: {'id': 101}}), \
                    mock.patch.object(chat_ui, 'get_messages', return_value=[*messages, added]), \
                    mock.patch.object(chat_ui, 'mark_conversation_read'), \
                    mock.patch.object(chat_ui, 'get_unread_counts', return_value={}):
                page._poll_new_messages()
            self.assertIs(next(label for label in page.findChildren(QLabel) if label.text() == messages[0]['tresc']), original)
            self.assertEqual(bar.value(), scroll)
            self.assertEqual(page.chat_input.toPlainText(), 'Draft in progress')
            self.assertTrue(any(label.text() == 'New message' for label in page.findChildren(QLabel)))

    def test_dashboard_refresh_keeps_task_items_selection_and_top_record(self):
        from app.dashboard import dashboard_ui
        with mock.patch.object(dashboard_ui.DashboardPage, 'refresh_async'), \
                mock.patch.object(dashboard_ui.DashboardPage, 'refresh_backup_from_mail'):
            page = dashboard_ui.DashboardPage(1)
            self.addCleanup(page.close)
            page.resize(1100, 600)
            page.show()
        page._timer.stop()
        page._mail_timer.stop()
        data = dashboard_ui.get_dashboard_data(1)
        data['zadania'] = [{'id': i, 'tytul': f'Task {i}'} for i in range(100)]
        page._render_data(data)
        page.tasks.setCurrentRow(55)
        page.tasks.scrollToItem(page.tasks.item(45), page.tasks.ScrollHint.PositionAtTop)
        _app.processEvents()
        selected = page.tasks.currentItem()
        anchor = page.tasks.itemAt(4, 1).data(ROLE)
        data['zadania'].insert(0, {'id': 1000, 'tytul': 'New task'})
        page._render_data(data)
        self.assertIs(page.tasks.currentItem(), selected)
        self.assertEqual(page.tasks.itemAt(4, 1).data(ROLE), anchor)
