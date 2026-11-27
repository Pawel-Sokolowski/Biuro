from unittest import TestCase
from unittest.mock import Mock, patch
from app.email.email_actions import send_email_smtp
from app.email.sent_copies import _sent_folder


class SentCopyTests(TestCase):
    def config(self):
        return dict(user='office@example.test',display_name='Office',host='smtp.example.test',port=587,ssl='STARTTLS',password='private')

    def test_bcc_is_only_on_saved_copy_and_each_client_gets_its_own_archive(self):
        smtp = Mock()
        smtp.send_message.return_value = {}
        wire = []
        smtp.send_message.side_effect = lambda message, **_: wire.append(message.as_bytes()) or {}
        with patch('app.email.email_actions.get_smtp_config_for_account',return_value=self.config()), \
             patch('app.email.email_actions.open_smtp') as connection, \
             patch('app.email.sent_copies.stage_copy', return_value=5), \
             patch('app.email.sent_copies.confirm_copy') as confirm, \
             patch('app.email.sent_copies.flush_copy', return_value=True), \
             patch('app.email.email_actions.store_sent_copy',return_value=True) as store, \
             patch('app.email.email_storage.find_special_folder',return_value='Sent'), \
             patch('app.email.email_actions.choose_copy_folder',side_effect=['Client A','Client B']):
            connection.return_value.__enter__.return_value = smtp
            ok, info = send_email_smtp(1,['office@example.test'],'Subject','Body',bcc=['a@example.test','b@example.test'],save_copy=True)
        self.assertTrue(ok)
        self.assertEqual(info,'OK')
        self.assertNotIn(b'Bcc:',wire[0])
        self.assertNotIn(b'a@example.test',wire[0])
        self.assertEqual(str(confirm.call_args.args[1]['Bcc']), 'a@example.test, b@example.test')
        self.assertEqual(store.call_count, 2)
        self.assertEqual(str(store.call_args_list[0].args[1]['Bcc']), 'a@example.test')
        self.assertEqual(str(store.call_args_list[1].args[1]['Bcc']), 'b@example.test')
        smtp.send_message.assert_called_once()

    def test_archive_failure_reports_warning_without_resending_smtp(self):
        with patch('app.email.email_actions.get_smtp_config_for_account',return_value=self.config()), \
             patch('app.email.email_actions.open_smtp') as connection, \
             patch('app.email.sent_copies.stage_copy', return_value=5), \
             patch('app.email.sent_copies.confirm_copy'), \
             patch('app.email.sent_copies.flush_copy', return_value=False):
            smtp = connection.return_value.__enter__.return_value
            smtp.send_message.return_value = {}
            ok, info = send_email_smtp(1,['a@example.test'],'Subject','Body',save_copy=True)
        self.assertTrue(ok)
        self.assertIn('Nie ponawiaj',info)
        smtp.send_message.assert_called_once()

    def test_smtp_waits_for_a_durable_copy(self):
        with patch('app.email.email_actions.get_smtp_config_for_account', return_value=self.config()), \
             patch('app.email.email_actions.open_smtp') as connection, \
             patch('app.email.sent_copies.stage_copy', side_effect=OSError('database unavailable')):
            ok, _ = send_email_smtp(1, ['a@example.test'], 'Subject', 'Body', save_copy=True)
        self.assertFalse(ok)
        connection.assert_not_called()

    def test_missing_sent_folder_refreshes_server_listing_and_never_guesses_folder(self):
        with patch('app.email.email_storage.find_special_folder',side_effect=[None,'Sent']), \
             patch('app.email.email_sync.list_folders_from_imap_and_store') as refresh:
            self.assertEqual(_sent_folder(1), 'Sent')
            refresh.assert_called_once_with(1)
