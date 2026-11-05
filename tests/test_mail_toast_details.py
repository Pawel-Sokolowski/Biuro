import unittest
from unittest import mock
from app.mail_toasts import MailToaster, _notification_text


class MailToastDetailsTests(unittest.TestCase):
    def test_single_message_has_account_sender_and_subject(self):
        title, body = _notification_text([{'_account': {'email': 'office@example.com'},
                                          'nadawca': 'Jan Kowalski <jan@example.com>', 'temat': 'Wyciąg październik'}])
        self.assertIn('office@example.com', title)
        self.assertIn('Jan Kowalski <jan@example.com>', body)
        self.assertIn('Wyciąg październik', body)

    def test_summary_shows_messages_from_each_account_with_bounded_native_text(self):
        messages = [{'_account': {'email': f'office{i}@example.com'}, 'nadawca': 'Nadawca ' + 'x'*100,
                     'temat': 'Temat '+ 'y'*200} for i in range(5)]
        title, body = _notification_text(messages)
        self.assertIn('5', title)
        self.assertIn('office0@example.com', body)
        self.assertIn('office1@example.com', body)
        self.assertIn('Temat', body)
        self.assertIn('Pozostałe: 2', body)
        self.assertLessEqual(len(body), 240)

    def test_missing_values_and_header_newlines_are_readable(self):
        title, body = _notification_text([{'_account': {'email': 'a@example.com'}, 'nadawca': None, 'temat': None}])
        self.assertIn('(bez tematu)', body)
        self.assertIn('(nieznany nadawca)', body)
        _, body = _notification_text([{'_account': {'email': 'a@example.com'}, 'nadawca': 'Jan <j@example.com>',
                                      'temat': 'Temat\r\nDalsza część'}])
        self.assertIn('Temat Dalsza część', body)

    def test_poll_retains_account_context_and_does_not_change_first_poll_or_navigation(self):
        toaster = MailToaster.__new__(MailToaster)
        toaster._last_ids = {}
        toaster._tray = mock.Mock()
        accounts = [{'id': 1, 'email': 'first@example.com'}, {'id': 2, 'email': 'second@example.com'}]
        with mock.patch('app.mail_toasts.get_new_unread_messages', side_effect=[(10, []), (20, [])]):
            toaster.check(accounts, {})
        toaster._tray.showMessage.assert_not_called()
        with mock.patch('app.mail_toasts.get_new_unread_messages', side_effect=[
                (11, [{'nadawca': 'A <a@example.com>', 'temat': 'First'}]),
                (21, [{'nadawca': 'B <b@example.com>', 'temat': 'Second'}])]):
            toaster.check(accounts, {})
        calls = toaster._tray.showMessage.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn('first@example.com', calls[0].args[0])
        self.assertIn('second@example.com', calls[1].args[0])
