"""Authorization and multi-workstation chat regressions from the repository review."""
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from unittest import mock

from tests.base import DbTestCase
from app.chat import chat_model as cm
from app.db.dao_conn import get_conn
from app.security.authorization import allowed_tabs, assigned_company_ids
from app.utils.crypto import register_user


class AccessAndChatReviewTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_employee('owner@example.com')
        self.reader = self.make_employee('reader@example.com')
        self.other = self.make_employee('other@example.com')
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.channel = cm.create_conversation('Review channel', self.owner, [self.reader, self.other])

    def test_registration_requires_authorized_actor_and_cannot_promote_itself(self):
        for kwargs in ({}, {'actor_id': self.reader}, {'is_admin_flag': True}):
            self.assertFalse(register_user('unapproved@example.com', 'Secret123', **kwargs)[0])
        with get_conn() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM pracownicy WHERE email='unapproved@example.com'").fetchone())
        ok, uid, _ = register_user('approved@example.com', 'Secret123', actor_id=self.admin)
        self.assertTrue(ok)
        self.assertTrue(allowed_tabs(uid))

    def test_deactivated_admin_does_not_reopen_public_bootstrap(self):
        from app.security.bootstrap import needs_initial_admin, create_initial_admin
        with get_conn() as conn:
            conn.execute('UPDATE pracownicy SET aktywny=0 WHERE id=%s', (self.admin,))
        self.assertFalse(needs_initial_admin())
        with self.assertRaises(PermissionError):
            create_initial_admin('unauthorized@example.com', 'Secret123')

    def test_non_admin_rows_do_not_suppress_first_admin_bootstrap(self):
        from app.security.bootstrap import needs_initial_admin, create_initial_admin
        with get_conn() as conn:
            conn.execute('DELETE FROM pracownicy WHERE id=%s', (self.admin,))
        self.assertTrue(needs_initial_admin())
        self.assertFalse(register_user('first@example.com', 'Secret123')[0])
        self.assertTrue(needs_initial_admin())
        create_initial_admin('bootstrap@example.com', 'Secret123')
        self.assertFalse(needs_initial_admin())

    def test_inactive_and_unknown_employees_have_no_company_or_tab_access(self):
        company = self.make_company()
        self.assertIn(company, assigned_company_ids(self.reader))
        with get_conn() as conn:
            conn.execute('UPDATE pracownicy SET aktywny=0 WHERE id=%s', (self.reader,))
        self.assertEqual(allowed_tabs(self.reader), [])
        self.assertEqual(assigned_company_ids(self.reader), set())
        self.assertEqual(allowed_tabs(999999), [])
        self.assertEqual(assigned_company_ids(999999), set())

    def test_partial_role_update_preserves_admin_settings_and_explicit_false_works(self):
        from app.db.dao_global_settings import get_role, update_role
        before = get_role(4)
        self.assertTrue(before['can_access_admin_settings'])
        update_role(4, before['nazwa'], can_view_invoices=True)
        self.assertTrue(get_role(4)['can_access_admin_settings'])
        update_role(4, before['nazwa'], can_access_admin_settings=False)
        self.assertFalse(get_role(4)['can_access_admin_settings'])
        from app.admin.settings.settings_ui import GlobalSettingsPage
        page = GlobalSettingsPage(self.admin, is_admin=True)
        self.addCleanup(page.close)
        self.assertTrue(any('Użytkownicy' in page.nav.item(i).text() for i in range(page.nav.count())))

    def test_revocation_denies_messages_notification_reads_and_attachment_metadata(self):
        message = cm.add_message_with_attachments(self.channel, self.owner, 'Before removal', [{'nazwa': 'secret.txt', 'dane': b'secret'}])
        self.assertEqual(len(cm.get_messages(self.channel, actor_id=self.reader)), 1)
        self.assertTrue(cm.get_attachments([message], actor_id=self.reader))
        cm.update_conversation(self.channel, 'Review channel', [self.other], self.owner)
        cm.add_message(self.channel, self.owner, 'After removal')
        self.assertEqual(cm.get_messages(self.channel, actor_id=self.reader), [])
        self.assertEqual(cm.get_new_messages(self.channel, '1970-01-01', actor_id=self.reader), [])
        self.assertEqual(cm.get_attachments([message], actor_id=self.reader), {})
        self.assertEqual(cm.get_latest_message_ids([self.channel], actor_id=self.reader), {})
        self.assertEqual(cm.count_messages_after(self.channel, 0, actor_id=self.reader), 0)
        with self.assertRaises(PermissionError):
            cm.add_message(self.channel, self.reader, 'Forbidden')

    def test_open_chat_clears_on_revocation(self):
        from app.chat.chat_ui import ChatPage
        page = ChatPage(self.reader)
        self.addCleanup(page.close)
        self.addCleanup(page._poll_timer.stop)
        page._reads.invalidate()
        page._poll_busy = False
        page.current_konwersacja = self.channel
        page._rendered_conversation = self.channel
        page._rendered_messages = []
        cm.update_conversation(self.channel, 'Review channel', [self.other], self.owner)
        cm.add_message(self.channel, self.owner, 'Secret after removal')
        with mock.patch.object(page._reads, 'submit', side_effect=lambda read, done, error: done(read())):
            page._poll_async()
        self.assertIsNone(page.current_konwersacja)
        self.assertIsNone(page._rendered_messages)
        self.assertFalse(page.btn_delete.isEnabled())

    def test_writers_commit_in_id_order_including_attachment_sends(self):
        original = cm._get_conn
        inserted = threading.Event()
        release = threading.Event()
        class Connection:
            def __init__(self, wrapped):
                self.wrapped = wrapped
            def __getattr__(self, name):
                return getattr(self.wrapped, name)
            def execute(self, statement, params=None):
                result = self.wrapped.execute(statement, params)
                if 'INSERT INTO wiadomosci_chat' in statement and threading.current_thread().name.startswith('chat-first'):
                    inserted.set()
                    if not release.wait(10):
                        raise TimeoutError('first writer barrier')
                return result
            def __enter__(self):
                self.wrapped.__enter__()
                return self
            def __exit__(self, *args):
                return self.wrapped.__exit__(*args)
        def connection():
            return Connection(original())
        with mock.patch.object(cm, '_get_conn', side_effect=connection), \
                ThreadPoolExecutor(1, thread_name_prefix='chat-first') as first_pool, \
                ThreadPoolExecutor(1, thread_name_prefix='chat-second') as second_pool:
            first = first_pool.submit(cm.add_message, self.channel, self.owner, 'First')
            try:
                self.assertTrue(inserted.wait(10))
                second = second_pool.submit(cm.add_message_with_attachments, self.channel, self.other, 'Second', [{'nazwa': 'a.txt', 'dane': b'a'}])
                with self.assertRaises(TimeoutError):
                    second.result(timeout=.25)
                self.assertEqual(cm.get_messages(self.channel, actor_id=self.reader), [])
            finally:
                release.set()
            first_id, second_id = first.result(10), second.result(10)
        self.assertLess(first_id, second_id)
        cm.mark_conversation_read(self.channel, self.reader, through_id=first_id)
        self.assertEqual(cm.get_unread_counts(self.reader)[self.channel], 1)

    def test_direct_channel_creation_is_unique_under_concurrent_open(self):
        barrier = threading.Barrier(2)
        original = cm.get_active_employees
        def employees():
            rows = original()
            barrier.wait(10)
            return rows
        with mock.patch.object(cm, 'get_active_employees', side_effect=employees), ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(cm.ensure_direct_conversations, actor) for actor in (self.owner, self.reader)]
            for job in jobs:
                job.result(15)
        with get_conn() as conn:
            name = cm._direct_name(self.owner, self.reader)
            rows = conn.execute("SELECT id FROM konwersacje WHERE typ='prywatna' AND nazwa=%s", (name,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertCountEqual(cm.get_participant_ids(rows[0][0]), [self.owner, self.reader])

    def test_duplicate_channel_migration_preserves_messages_and_attachments(self):
        from app.db.chat_integrity_schema import apply
        with get_conn() as conn:
            conn.execute('DROP INDEX uq_chat_private_name')
            ids = [conn.execute("INSERT INTO konwersacje(nazwa,typ) VALUES ('dm:old','prywatna') RETURNING id").fetchone()[0] for _ in range(2)]
            for cid in ids:
                conn.execute('INSERT INTO uczestnicy_chat(konwersacja_id,pracownik_id,ostatnie_przeczytane_id) VALUES(%s,%s,0)', (cid, self.owner))
        first = cm.add_message(ids[0], self.owner, 'First history')
        second = cm.add_message_with_attachments(ids[1], self.owner, 'Second history', [{'nazwa': 'a.txt', 'dane': b'a'}])
        with get_conn() as conn:
            apply(conn)
            apply(conn)
        self.assertEqual([row['id'] for row in cm.get_messages(ids[0], actor_id=self.owner)], [first, second])
        self.assertTrue(cm.get_attachments([second], actor_id=self.owner))
        with get_conn() as conn:
            self.assertIsNone(conn.execute('SELECT id FROM konwersacje WHERE id=%s', (ids[1],)).fetchone())
