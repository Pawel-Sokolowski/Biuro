from unittest.mock import Mock, patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget, QMainWindow, QListWidget, QListWidgetItem, QStackedWidget
from app.chat.chat_model import (create_conversation, add_message, add_message_with_attachments,
                                 get_chat_notification_messages, update_conversation)
from app.chat.chat_toasts import ChatToaster
from tests.base import DbTestCase


class ChatToastTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.other = self.make_employee('chat@example.com', 'Jan', 'Kowalski')
        self.outsider = self.make_employee('hidden-chat@example.com')
        self.chat = create_conversation('Księgowość', self.other, [1])
        self.private = create_conversation('Tajny kanał', self.other, [self.outsider])

    def test_baseline_skips_history_then_delivers_only_new_accessible_incoming_messages(self):
        add_message(self.chat, self.other, 'Stara historia')
        baseline, rows = get_chat_notification_messages(1, None)
        self.assertEqual(rows, [])
        incoming = add_message(self.chat, self.other, 'Nowa wiadomość')
        add_message(self.chat, 1, 'Moja odpowiedź')
        add_message(self.private, self.other, 'Prywatna wiadomość')
        cursor, rows = get_chat_notification_messages(1, baseline)
        self.assertEqual([row['id'] for row in rows], [incoming])
        self.assertEqual(get_chat_notification_messages(1, cursor)[1], [])

    def test_new_channels_are_detected_without_refreshing_chat_page(self):
        baseline, _ = get_chat_notification_messages(1, None)
        channel = create_conversation('Nowy kanał', self.other, [1])
        incoming = add_message(channel, self.other, 'Zaproszenie')
        _, rows = get_chat_notification_messages(1, baseline)
        self.assertEqual([row['id'] for row in rows], [incoming])

    def test_module_permissions_and_removed_membership_prevent_notifications(self):
        baseline, _ = get_chat_notification_messages(1, None)
        add_message(self.chat, self.other, 'Tylko dla uczestników')
        update_conversation(self.chat, 'Księgowość', [], self.other)
        self.assertEqual(get_chat_notification_messages(1, baseline)[1], [])
        with patch('app.security.authorization.allowed_tabs', return_value=[]):
            self.assertEqual(get_chat_notification_messages(1, baseline), (None, []))

    def toaster(self, window):
        native = Mock()
        callback = Mock()
        with patch('app.mail_toasts.native_toasts', return_value=native), \
             patch('app.mail_toasts.QSystemTrayIcon.isSystemTrayAvailable', return_value=False), \
             patch('app.chat.chat_toasts.TaskRunner'):
            toaster = ChatToaster(window, 1, callback)
        self.addCleanup(toaster.hide)
        return toaster, native, callback

    def test_native_toasts_are_shown_minimized_and_full_screen(self):
        window = QWidget()
        self.addCleanup(window.close)
        toaster, native, _ = self.toaster(window)
        cursor, _ = get_chat_notification_messages(1, None)
        for display in (window.showMinimized, window.showFullScreen):
            display()
            QApplication.processEvents()
            add_message(self.chat, self.other, 'Przynieś dokumenty')
            result = get_chat_notification_messages(1, cursor)
            toaster._loaded(result)
            cursor = result[0]
        self.assertEqual(native.show.call_count, 2)
        title, body, target = native.show.call_args.args
        self.assertIn('Księgowość', title)
        self.assertIn('Jan Kowalski', body)
        self.assertIn('Przynieś dokumenty', body)
        self.assertEqual(target['conversation_id'], self.chat)
        toaster._loaded(get_chat_notification_messages(1, cursor))
        self.assertEqual(native.show.call_count, 2)

    def test_attachment_messages_and_bursts_have_readable_summary(self):
        window = QWidget()
        self.addCleanup(window.close)
        toaster, native, _ = self.toaster(window)
        cursor, _ = get_chat_notification_messages(1, None)
        add_message(self.chat, self.other, 'Dokumenty')
        add_message_with_attachments(self.chat, self.other, '', [{'nazwa': 'plik.txt', 'dane': b'abc'}])
        toaster._loaded(get_chat_notification_messages(1, cursor))
        self.assertEqual(native.show.call_count, 1)
        self.assertIn('załącznik', native.show.call_args.args[1])
        self.assertIn('Nowych wiadomości: 2', native.show.call_args.args[1])

    def test_notification_click_restores_window_and_selects_conversation(self):
        from app.main_ui import OfficeManagerApp
        from app.chat.chat_ui import ChatPage
        window = OfficeManagerApp.__new__(OfficeManagerApp)
        QMainWindow.__init__(window)
        self.addCleanup(window.close)
        window.pracownik_id = 1
        window.sidebar = QListWidget(window)
        item = QListWidgetItem('Czat')
        item.setData(Qt.ItemDataRole.UserRole, 'Czat')
        window.sidebar.addItem(item)
        window.pages = QStackedWidget(window)
        window.page_chat = ChatPage(1)
        self.addCleanup(window.page_chat._poll_timer.stop)
        window.pages.addWidget(window.page_chat)
        window.showMinimized()
        self.assertTrue(window._show_chat_tab({'kind': 'chat', 'conversation_id': self.chat}))
        self.assertFalse(window.isMinimized())
        self.assertEqual(window.page_chat.current_konwersacja, self.chat)
        # Avoid the full application's close handler on this deliberately minimal window.
        window.closeEvent = lambda event: event.accept()

    def test_direct_message_uses_senders_name_instead_of_internal_dm_name(self):
        from app.db.dao_conn import get_conn
        window = QWidget()
        self.addCleanup(window.close)
        toaster, native, _ = self.toaster(window)
        with get_conn() as conn:
            conn.execute("UPDATE konwersacje SET typ='prywatna', nazwa='dm:1-2' WHERE id=%s", (self.chat,))
        cursor, _ = get_chat_notification_messages(1, None)
        add_message(self.chat, self.other, 'Prywatna rozmowa')
        toaster._loaded(get_chat_notification_messages(1, cursor))
        self.assertIn('Jan Kowalski', native.show.call_args.args[0])
        self.assertNotIn('dm:1-2', native.show.call_args.args[0])

    def test_minimized_chat_does_not_automatically_mark_conversation_read(self):
        from app.chat import chat_ui
        page = chat_ui.ChatPage(1)
        self.addCleanup(page.close)
        self.addCleanup(page._poll_timer.stop)
        page.current_konwersacja = self.chat
        with patch.object(chat_ui, 'get_latest_message_ids', return_value={}), \
             patch.object(chat_ui, 'get_unread_counts', return_value={}), \
             patch.object(chat_ui, 'mark_conversation_read') as mark:
            page.showMinimized()
            QApplication.processEvents()
            page._poll_new_messages()
        mark.assert_not_called()
