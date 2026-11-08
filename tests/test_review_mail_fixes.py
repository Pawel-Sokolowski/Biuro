"""Regressions for non-destructive mail commands and stale message callbacks."""
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PyQt6.QtWidgets import QTextBrowser

from app.email import email_actions, email_rules, email_storage as storage, email_sync, email_ui
from tests.test_review_followups import MailboxFixture


class FolderRenameSafetyTests(unittest.TestCase):
    def server(self, capabilities=()):
        conn = mock.Mock(capabilities=capabilities)
        conn.unselect.return_value = ('OK', [])
        conn.rename.return_value = ('OK', [])
        conn.close.side_effect = AssertionError('CLOSE would expunge unrelated deleted mail')
        return conn

    def rename(self, session):
        with mock.patch.object(email_sync, '_mailbox_command', side_effect=lambda account, action: action(session)), \
                mock.patch.object(email_sync, 'rename_folder_local') as local:
            result = email_sync.rename_folder(1, 'Projects', 'Projects 2026')
        self.assertEqual(result, 'Projects 2026')
        local.assert_called_once_with(1, 'Projects', 'Projects 2026')

    def test_selected_mailbox_uses_unselect_without_expunge(self):
        conn = self.server((b'UNSELECT',))
        session = email_sync._Session(conn, 1)
        session.selected = ('INBOX', False)
        session.selected_generation = '10'
        self.rename(session)
        conn.unselect.assert_called_once_with()
        conn.close.assert_not_called()
        conn.logout.assert_not_called()
        conn.rename.assert_called_once_with('"Projects"', '"Projects 2026"')
        self.assertIsNone(session.selected)
        self.assertIsNone(session.selected_generation)

    def test_server_without_unselect_reconnects_without_close(self):
        original, replacement = self.server(), self.server()
        session = email_sync._Session(original, 1, {'id': 1})
        session.selected = ('INBOX', False)
        with mock.patch.object(email_sync, '_get_account_info', return_value={'id': 1}), \
                mock.patch.object(email_sync, '_connect_imap', return_value=replacement):
            self.rename(session)
        original.close.assert_not_called()
        original.logout.assert_called_once_with()
        original.rename.assert_not_called()
        replacement.rename.assert_called_once_with('"Projects"', '"Projects 2026"')
        self.assertIs(session.conn, replacement)

    def test_failed_unselect_does_not_rename_or_change_local_folder(self):
        conn = self.server((b'UNSELECT',))
        conn.unselect.return_value = ('NO', [b'Not permitted'])
        session = email_sync._Session(conn, 1)
        session.selected = ('INBOX', False)
        with mock.patch.object(email_sync, '_mailbox_command', side_effect=lambda account, action: action(session)), \
                mock.patch.object(email_sync, 'rename_folder_local') as local:
            with self.assertRaisesRegex(RuntimeError, 'Not permitted'):
                email_sync.rename_folder(1, 'Projects', 'Projects 2026')
        conn.close.assert_not_called()
        conn.rename.assert_not_called()
        local.assert_not_called()

    def test_unselected_session_does_not_reconnect(self):
        conn = self.server()
        session = email_sync._Session(conn, 1)
        self.rename(session)
        conn.logout.assert_not_called()
        conn.close.assert_not_called()


class ForwardAttachmentSafetyTests(unittest.TestCase):
    def test_same_filename_parts_keep_distinct_payloads_and_display_names(self):
        message = EmailMessage()
        message.set_content('Two invoices')
        for payload in (b'first invoice', b'second invoice'):
            message.add_attachment(payload, maintype='application', subtype='pdf', filename='invoice.pdf')
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(email_ui.tempfile, 'mkdtemp', return_value=folder):
            paths = email_ui.EmailPage._save_attachments_to_temp(message)
            self.assertNotEqual(paths[0], paths[1])
            outgoing = email_actions.prepare_message('sender@example.com', '', ['recipient@example.com'],
                                                     'Fwd', '', attachments=paths)
            parts = list(outgoing.iter_attachments())
            self.assertEqual([part.get_filename() for part in parts], ['invoice.pdf', 'invoice.pdf'])
            self.assertEqual([part.get_payload(decode=True) for part in parts], [b'first invoice', b'second invoice'])
            self.assertTrue(all(Path(path).is_file() for path in paths))


class ManualRuleIdentityTests(MailboxFixture):
    def test_rule_snapshot_cannot_move_replacement_uid(self):
        storage.record_mailbox_generation(1, 'INBOX', '10')
        self.add_messages()
        storage.save_folder(1, 'Archive')
        conn = mock.Mock(capabilities=(b'MOVE',))
        conn.uid.return_value = ('OK', [])
        session = SimpleNamespace(conn=conn, select=lambda *args: True)

        def replace_before_matching(account):
            storage.record_mailbox_generation(1, 'INBOX', '11')
            self.add_messages()
            return [{'nadawca_zawiera': 'sender@example.com', 'target': 'Archive'}]

        with mock.patch.object(email_rules, '_active_rules', side_effect=replace_before_matching), \
                mock.patch.object(email_sync, '_shared_session') as shared, \
                self.assertLogs(email_rules.logger, 'WARNING'):
            shared.return_value.__enter__.return_value = session
            self.assertEqual(email_rules.apply_rules_to_folder(1, 'INBOX'), 0)
        conn.uid.assert_not_called()
        self.assertIsNotNone(storage.get_message_by_uid(1, '1', self.folder))

    def test_manual_mark_read_and_move_carry_the_selected_row_id(self):
        row_id = self.add_messages()
        with mock.patch.object(email_rules, '_active_rules', return_value=[
                {'nadawca_zawiera': 'sender@example.com', 'target': 'Archive', 'oznacz_jako_przeczytane': 1}]), \
                mock.patch.object(email_sync, 'mark_read_remote') as mark_read, \
                mock.patch.object(email_sync, 'move_emails') as move:
            self.assertEqual(email_rules.apply_rules_to_folder(1, 'INBOX'), 1)
        mark_read.assert_called_once_with(1, 'INBOX', '1', expected_row_id=row_id)
        move.assert_called_once_with(1, 'INBOX', ['1'], 'Archive', expected_rows={'1': row_id})


class BodyCallbackContextTests(MailboxFixture):
    def setUp(self):
        super().setUp()
        self.add_messages()
        with mock.patch.object(email_ui.EmailPage, 'start_background_sync'), \
                mock.patch.object(email_ui, 'get_email_accounts_for_employee', return_value=[{'id': 1}]), \
                mock.patch.object(email_ui, 'MailBrowser', QTextBrowser):
            self.page = email_ui.EmailPage(1)
        self.addCleanup(self.page.close)
        self.addCleanup(self.page.stop_background_sync)
        self.page.current_account = {'id': 1}
        self.page.current_folder_local = 'INBOX'
        self.page.load_mail_list_from_db('INBOX')

    def open_pending(self):
        item = self.page.mail_list.topLevelItem(0)
        with mock.patch.object(self.page, '_run_async') as runner:
            self.page.on_mail_clicked(item)
        return runner.call_args.args[1]

    def assert_ignored(self, done):
        message = EmailMessage()
        message.set_content('Old body')
        with mock.patch.object(self.page, '_render_message') as render, \
                mock.patch.object(self.page, '_mark_opened_as_read') as mark_read:
            done(message, None)
        render.assert_not_called()
        mark_read.assert_not_called()

    def test_employee_filter_to_empty_list_invalidates_pending_body(self):
        done = self.open_pending()
        token = self.page._load_token
        self.page._on_chip_clicked(999)
        self.assertEqual(self.page.mail_list.topLevelItemCount(), 0)
        self.assertGreater(self.page._load_token, token)
        self.assert_ignored(done)

    def test_completed_filter_to_empty_list_invalidates_pending_body(self):
        done = self.open_pending()
        self.page._on_completed_chip_clicked()
        self.assertEqual(self.page.mail_list.topLevelItemCount(), 0)
        self.assert_ignored(done)

    def test_account_revocation_clears_target_and_rejects_pending_body(self):
        done = self.open_pending()
        self.page._last_account_check = 0
        self.page._sync_running = False
        with mock.patch.object(email_ui, 'get_email_accounts_for_employee', return_value=[]):
            self.page.start_background_sync()
        self.assertIsNone(self.page.current_account)
        self.assertIsNone(self.page._current_message_row_id)
        self.assert_ignored(done)

    def test_callback_rechecks_row_identity_even_without_token_change(self):
        done = self.open_pending()
        self.page._current_message_row_id += 1
        self.assert_ignored(done)

    def test_current_body_still_renders_and_marks_only_its_row(self):
        done = self.open_pending()
        message = EmailMessage()
        message.set_content('Current body')
        row_id = self.page._current_message_row_id
        with mock.patch.object(self.page, '_render_message') as render, \
                mock.patch.object(self.page, '_mark_opened_as_read') as mark_read:
            done(message, None)
        render.assert_called_once_with(message)
        mark_read.assert_called_once_with(row_id, 1, 'INBOX', '1')
