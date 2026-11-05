import sys
import types
import unittest
from unittest import mock
from xml.etree import ElementTree

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication, QTextBrowser, QWidget

from app.db.dao_conn import get_conn
from app.email import email_storage as storage, email_ui
from app.mail_toasts import MailToaster
from app.windows_toasts import WindowsToasts
from tests.test_review_followups import MailboxFixture

_app = QApplication.instance() or QApplication(['toast-target-tests'])


class ToastTargetTests(unittest.TestCase):
    def test_each_native_notification_keeps_its_own_target_even_when_clicked_out_of_order(self):
        callbacks, documents = [], []
        class Document:
            def load_xml(self, xml):
                documents.append(ElementTree.fromstring(xml))
        class Notification:
            def __init__(self, document):
                pass
            def add_activated(self, callback):
                callbacks.append(callback)
                return len(callbacks)
        xml_module = types.ModuleType('winrt.windows.data.xml.dom')
        xml_module.XmlDocument = Document
        toast_module = types.ModuleType('winrt.windows.ui.notifications')
        toast_module.ToastNotification = Notification
        native = WindowsToasts.__new__(WindowsToasts)
        QObject.__init__(native)
        native._notifier = mock.Mock()
        native._notifications = []
        clicked = []
        native.activated.connect(clicked.append)
        with mock.patch.dict(sys.modules, {'winrt.windows.data.xml.dom': xml_module,
                                           'winrt.windows.ui.notifications': toast_module}):
            native.show('A & B', '<sender@example.com>', {'row_id': 1})
            native.show('Next', 'Message', {'row_id': 2})
        callbacks[1](None, None)
        callbacks[0](None, None)
        self.assertEqual(clicked, [{'row_id': 2}, {'row_id': 1}])
        self.assertEqual(documents[0].find('.//text').text, 'A & B')
        self.assertEqual(len(native._notifications), 2)

    def test_poll_passes_account_and_row_identity_to_native_backend(self):
        toaster = MailToaster.__new__(MailToaster)
        toaster._last_ids = {}
        toaster._tray = None
        toaster._native = mock.Mock()
        with mock.patch('app.mail_toasts.get_new_unread_messages', return_value=(10, [
                {'id': 9, 'message_id': '<old@x>', 'nadawca': 'A', 'temat': 'First'},
                {'id': 10, 'message_id': '<new@x>', 'nadawca': 'B', 'temat': 'Second'}])):
            toaster.check([{'id': 4, 'email': 'office@example.com'}], {})
        targets = [call.args[2] for call in toaster._native.show.call_args_list]
        self.assertEqual([target['row_id'] for target in targets], [9, 10])
        self.assertTrue(all(target['account_id'] == 4 for target in targets))

    def test_tray_fallback_click_uses_the_active_message_target(self):
        window = QWidget()
        self.addCleanup(window.close)
        tray = mock.Mock()
        clicked = []
        with mock.patch('app.mail_toasts.native_toasts', return_value=None), \
                mock.patch('app.mail_toasts.QSystemTrayIcon') as factory:
            factory.isSystemTrayAvailable.return_value = True
            factory.return_value = tray
            toaster = MailToaster(window, clicked.append)
        target = {'row_id': 12, 'account_id': 4}
        toaster._show('Title', 'Body', target)
        tray.messageClicked.connect.call_args.args[0]()
        self.assertEqual(clicked, [target])


class MessageNavigationTests(MailboxFixture):
    def setUp(self):
        super().setUp()
        self.row_id = self.add_messages(1)
        with get_conn() as conn:
            conn.execute('INSERT INTO pracownik_konto_email(pracownik_id, konto_id) VALUES (1, 1)')

    def test_new_mail_target_does_not_require_an_assignment_but_rechecks_access(self):
        target = {'account_id': 1, 'row_id': self.row_id, 'message_id': '<1@review>'}
        self.assertIsNone(storage.resolve_assigned_message(1, target))
        self.assertEqual(storage.resolve_notification_message(1, target)['id'], self.row_id)
        other = self.make_employee('no-account@example.com')
        self.assertIsNone(storage.resolve_notification_message(other, target))
        with get_conn() as conn:
            conn.execute('DELETE FROM pracownik_konto_email WHERE pracownik_id = 1')
        self.assertIsNone(storage.resolve_notification_message(1, target))

    def test_click_opens_exact_message_and_cancels_pending_search(self):
        for patcher in (mock.patch.object(email_ui.EmailPage, 'start_background_sync'),
                        mock.patch.object(email_ui, 'MailBrowser', QTextBrowser),
                        mock.patch.object(email_ui.EmailPage, '_run_async')):
            patcher.start()
            self.addCleanup(patcher.stop)
        page = email_ui.EmailPage(1)
        self.addCleanup(page.close)
        self.addCleanup(page.stop_background_sync)
        page.search_input.setText('old search')
        self.assertTrue(page._search_timer.isActive())
        with mock.patch.object(page, 'on_mail_clicked') as opened:
            self.assertTrue(page.open_message_notification({'account_id': 1, 'row_id': self.row_id}))
        self.assertFalse(page._search_timer.isActive())
        self.assertEqual(page.search_input.text(), '')
        self.assertEqual(page.mail_list.currentItem().data(0, email_ui.ROLE_ROW_ID), self.row_id)
        self.assertEqual(page.folder_tree.currentItem().data(0, email_ui.ROLE_DATA), 'INBOX')
        self.assertEqual(opened.call_args.args[0].data(0, email_ui.ROLE_ROW_ID), self.row_id)

    def test_retired_row_with_reused_uid_is_not_opened_as_the_original(self):
        storage.record_mailbox_generation(1, 'INBOX', '11')
        self.add_messages(1)
        storage.record_mailbox_generation(1, 'INBOX', '12')
        storage.save_message_headers(1, self.folder, [{'uid': '1', 'message_id': '<replacement@review>', 'subject': 'Replacement'}])
        self.assertIsNone(storage.resolve_notification_message(1, {'account_id': 1, 'row_id': self.row_id, 'message_id': '<1@review>'}))
