from unittest import TestCase
from unittest.mock import Mock, patch
from app.email.email_actions import send_email_smtp, prepare_message, store_sent_copy


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
             patch('app.email.email_actions.store_sent_copy',return_value=True) as store, \
             patch('app.email.email_storage.find_special_folder',return_value='Sent'), \
             patch('app.email.email_actions.choose_copy_folder',side_effect=['Client A','Client B']):
            connection.return_value.__enter__.return_value = smtp
            ok, info = send_email_smtp(1,['office@example.test'],'Subject','Body',bcc=['a@example.test','b@example.test'],save_copy=True)
        self.assertTrue(ok)
        self.assertEqual(info,'OK')
        self.assertNotIn(b'Bcc:',wire[0])
        self.assertNotIn(b'a@example.test',wire[0])
        self.assertEqual(store.call_count,3)
        self.assertEqual(str(store.call_args_list[0].args[1]['Bcc']),'a@example.test, b@example.test')
        self.assertEqual(str(store.call_args_list[1].args[1]['Bcc']),'a@example.test')
        self.assertEqual(str(store.call_args_list[2].args[1]['Bcc']),'b@example.test')
        smtp.send_message.assert_called_once()

    def test_archive_failure_reports_warning_without_resending_smtp(self):
        with patch('app.email.email_actions.get_smtp_config_for_account',return_value=self.config()), \
             patch('app.email.email_actions.open_smtp') as connection, \
             patch('app.email.email_actions.store_sent_copy',return_value=False):
            smtp = connection.return_value.__enter__.return_value
            smtp.send_message.return_value = {}
            ok, info = send_email_smtp(1,['a@example.test'],'Subject','Body',save_copy=True)
        self.assertTrue(ok)
        self.assertIn('nie ponawiaj',info)
        smtp.send_message.assert_called_once()

    def test_missing_sent_folder_refreshes_server_listing_and_never_guesses_folder(self):
        message = prepare_message('a@example.test','A',['b@example.test'],'Subject','Body')
        with patch('app.email.email_storage.find_special_folder',side_effect=[None,'Sent']), \
             patch('app.email.email_sync.list_folders_from_imap_and_store') as refresh, \
             patch('app.email.email_sync.append_message',return_value=True) as append, \
             patch('app.email.email_sync.sync_folder'):
            self.assertTrue(store_sent_copy(1,message,None))
            refresh.assert_called_once_with(1)
            self.assertEqual(append.call_args.args[1],'Sent')
