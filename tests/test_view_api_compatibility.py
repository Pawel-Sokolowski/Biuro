"""Independent client/server updates must not break mail and calendar reads."""
from types import SimpleNamespace
from unittest import TestCase,mock
from app.remote import view_reads
from app.remote.client import OfficeClient


class ViewApiCompatibilityTests(TestCase):
    def test_old_server_calendar_uses_existing_authorized_reads(self):
        connection = SimpleNamespace(server_features=frozenset())
        events = [{'id':1,'tytul':'Existing'}]
        with mock.patch('app.remote.view_reads.client',return_value=connection), \
             mock.patch('app.remote.screen_data.calendar_view_snapshot',side_effect=AssertionError('unsupported batch')), \
             mock.patch('app.db.dao_calendar.list_events',return_value=events) as read, \
             mock.patch('app.kalendarz.google_sync.is_connected',return_value=False), \
             mock.patch('app.kalendarz.caldav_sync.is_configured',return_value=False):
            snapshot = view_reads.calendar_view_snapshot(7,'2026-11-01','2026-12-01')
        read.assert_called_once_with(7,'2026-11-01','2026-12-01')
        self.assertEqual(snapshot['events'],events)

    def test_old_server_mail_does_not_attempt_the_unknown_batch_operation(self):
        connection = SimpleNamespace(server_features=frozenset())
        accounts = [{'id':1,'email':'own@example.com'}]
        with mock.patch('app.remote.view_reads.client',return_value=connection), \
             mock.patch('app.remote.screen_data.mail_sidebar_snapshot',side_effect=AssertionError('unsupported batch')), \
             mock.patch('app.email.email_storage.get_email_accounts_for_employee',return_value=accounts) as read, \
             mock.patch('app.email.email_storage.get_folders_for_account',return_value=[{'id':3,'server_name':'INBOX'}]), \
             mock.patch('app.email.email_storage.get_unread_counts',return_value={3:2}), \
             mock.patch('app.db.dao_employees.list_employees',return_value=[]), \
             mock.patch('app.email.email_labels.resolve_label_numbers',return_value={}), \
             mock.patch('app.db.dao_app_settings.get_setting',return_value=''):
            snapshot = view_reads.mail_sidebar_snapshot(7)
        read.assert_called_once_with(7)
        self.assertEqual(snapshot['accounts'],accounts)
        self.assertEqual(snapshot['unread'],{1:{3:2}})

    def test_batch_permission_denial_is_not_retried_through_legacy_operations(self):
        connection = SimpleNamespace(server_features={'calendar_view_snapshot'})
        with mock.patch('app.remote.view_reads.client',return_value=connection), \
             mock.patch('app.remote.screen_data.calendar_view_snapshot',side_effect=PermissionError('denied')), \
             mock.patch('app.db.dao_calendar.list_events') as legacy:
            with self.assertRaises(PermissionError):
                view_reads.calendar_view_snapshot(7,'2026-11-01','2026-12-01')
        legacy.assert_not_called()

    def test_login_and_resume_negotiate_features_without_extra_requests(self):
        connection = OfficeClient('https://localhost')
        with mock.patch.object(connection,'_request',side_effect=[
            {'token':'session','employee_id':7},
            {'employee_id':7,'ui_features':['mail_sidebar_snapshot','calendar_view_snapshot']}]) as request:
            connection.login('user@example.com','password')
            self.assertEqual(connection.server_features,frozenset())
            connection.resume('session')
            self.assertEqual(connection.server_features,{'mail_sidebar_snapshot','calendar_view_snapshot'})
            self.assertEqual(request.call_count,2)
