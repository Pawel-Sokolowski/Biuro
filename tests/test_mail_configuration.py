"""IMAP endpoint changes cannot reuse sessions or write old responses to a new mailbox."""
from unittest import mock

from app.config import get_dsn, schema_context
from app.db import dao_global_settings as settings
from app.db.dao_conn import get_conn
from app.email import email_storage as storage, email_sync as sync, email_ui
from tests.test_review_followups import MailboxFixture


class MailConfigurationTests(MailboxFixture):
    def setUp(self):
        super().setUp()
        settings.update_email_account_connection(1, 'review@example.com', 'old.invalid', 993,
                                                 'smtp.invalid', 465, 'SSL/TLS', 'SSL/TLS')
        storage.record_mailbox_generation(1, 'INBOX', '10')
        self.row_id = self.add_messages()
        storage.save_raw_message(self.row_id, b'Subject: Original\r\n\r\nOriginal')
        self.revision = storage.get_account_info(1)['connection_revision']
        sync.close_sessions()
        self.addCleanup(sync.close_sessions)

    def change_endpoint(self):
        settings.update_email_account_connection(1, 'new@example.com', 'new.invalid', 993,
                                                 'smtp.invalid', 465, 'SSL/TLS', 'SSL/TLS')

    def test_cached_session_is_replaced_after_configuration_change(self):
        first, second = mock.Mock(), mock.Mock()
        with mock.patch.object(sync, '_connect_imap', side_effect=[first, second]) as connect:
            with sync._shared_session(1) as original:
                pass
            self.change_endpoint()
            with sync._shared_session(1) as replacement:
                self.assertIsNot(original, replacement)
                self.assertIs(replacement.conn, second)
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(connect.call_args.args[0]['serwer_imap'], 'new.invalid')
        first.logout.assert_called_once()

    def test_password_rotation_reconnects_without_retiring_cached_mail(self):
        first, second = mock.Mock(), mock.Mock()
        with mock.patch.object(sync, '_connect_imap', side_effect=[first, second]):
            with sync._shared_session(1):
                pass
            settings.update_email_account_password(1, 'different-ciphertext')
            with sync._shared_session(1) as current:
                self.assertIs(current.conn, second)
        self.assertEqual(storage.get_message_row_id(1, self.folder, '1'), self.row_id)
        self.assertEqual(storage.load_raw_message(self.row_id), b'Subject: Original\r\n\r\nOriginal')

    def test_endpoint_change_retires_mappings_but_keeps_archived_body(self):
        self.change_endpoint()
        self.assertIsNone(storage.get_message_row_id(1, self.folder, '1'))
        self.assertIsNone(storage.get_mailbox_generation(1, 'INBOX'))
        self.assertIsNotNone(storage.load_raw_message(self.row_id))

    def test_same_uidvalidity_on_new_server_cannot_accept_old_headers_or_flags(self):
        self.change_endpoint()
        storage.record_mailbox_generation(1, 'INBOX', '10')
        storage.save_message_headers(1, self.folder, [{'uid': '1', 'subject': 'Replacement',
                                                      'date': '2999-01-01', 'is_seen': True}])
        replacement = storage.get_message_row_id(1, self.folder, '1')
        for write in (
            lambda: storage.save_message_headers(1, self.folder, [{'uid': '1', 'subject': 'Stale'}],
                        expected_generation='10', expected_account_revision=self.revision),
            lambda: storage.apply_server_state(1, self.folder, set(), set(), '0001-01-01',
                        expected_generation='10', expected_account_revision=self.revision),
            lambda: storage.save_refs(1, self.folder, [('1', 'stale')],
                        expected_generation='10', expected_account_revision=self.revision),
            lambda: storage.record_mailbox_generation(1, 'INBOX', '11', expected_account_revision=self.revision),
        ):
            with self.assertRaises(storage.MailboxGenerationChanged):
                write()
        self.assertFalse(storage.save_current_raw_message(1, self.folder, '1', replacement, '10', b'stale',
                                                         expected_account_revision=self.revision))
        with get_conn() as conn:
            row = conn.execute('SELECT * FROM wiadomosci_email WHERE id=%s', (replacement,)).fetchone()
        self.assertEqual(row['temat'], 'Replacement')
        self.assertEqual(row['przeczytana'], 1)
        self.assertEqual(row['usunieta'], 0)
        self.assertIsNone(storage.load_raw_message(replacement))

    def test_select_response_from_old_configuration_cannot_adopt_a_generation(self):
        account = storage.get_account_info(1)
        connection = mock.Mock()
        def select(*args, **kw):
            self.change_endpoint()
            return 'OK', [b'1']
        connection.select.side_effect = select
        connection.response.return_value = 'UIDVALIDITY', [b'99']
        session = sync._Session(connection, 1, account)
        with self.assertRaises(storage.MailboxGenerationChanged):
            session.select('INBOX', True)
        self.assertIsNone(storage.get_mailbox_generation(1, 'INBOX'))

    def test_legacy_account_update_uses_the_same_invalidation(self):
        settings.update_email_account(1, 'new@example.com', 'new.invalid', 'smtp.invalid', '')
        self.assertGreater(storage.get_account_info(1)['connection_revision'], self.revision)
        self.assertIsNone(storage.get_message_row_id(1, self.folder, '1'))

    def test_session_cache_is_namespaced_by_database_and_schema(self):
        with mock.patch.object(sync, '_get_account_info', return_value={'id': 1}), \
                mock.patch.object(sync, '_connect_imap', side_effect=[mock.Mock(), mock.Mock()]):
            with schema_context('first_schema'):
                with sync._shared_session(1) as first:
                    pass
            with schema_context('second_schema'):
                with sync._shared_session(1) as second:
                    self.assertIsNot(first, second)
        self.assertIn((get_dsn(), 'first_schema', 1), sync._sessions)
        self.assertIn((get_dsn(), 'second_schema', 1), sync._sessions)

    def test_reply_and_forward_do_not_resolve_a_replacement_by_uid(self):
        page = mock.Mock(current_account={'id': 1}, current_uid='1', current_folder_local='INBOX',
                         _current_message_row_id=self.row_id)
        with mock.patch.object(email_ui, 'load_message', return_value=None) as load, \
                mock.patch.object(email_ui.QMessageBox, 'warning'):
            self.assertIsNone(email_ui.EmailPage._current_original(page))
        load.assert_called_once_with(1, 'INBOX', '1', expected_row_id=self.row_id)
