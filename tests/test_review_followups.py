"""Regressions for PR review: mailbox identity, retained pages and UI concurrency."""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QTextBrowser, QTreeWidgetItem

from app.config import schema_context
from app.db.dao_conn import get_conn
from app.email import email_storage as storage, email_sync
from tests.base import DbTestCase
from tests.test_review_optimizations import wait_until


class MailboxFixture(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            conn.execute("INSERT INTO konta_email(id,email) VALUES (1,'review@example.com')")
        self.folder = storage.save_folder(1, "INBOX")

    def add_messages(self, count=1):
        storage.save_message_headers(1, self.folder, [
            {"uid": str(i), "message_id": f"<{i}@review>", "subject": f"Message {i}",
             "sender": "sender@example.com", "date": "2026-11-01 10:00"}
            for i in range(1, count + 1)
        ])
        return storage.get_message_row_id(1, self.folder, "1")

    def fake_session(self, select=None, fetch=None, generation=b"10"):
        conn = mock.Mock()
        # imaplib consumes untagged responses; a second lookup loses UIDVALIDITY.
        conn.response.side_effect = [("UIDVALIDITY", [generation]), ("UIDVALIDITY", [None])]
        conn.select.side_effect = select
        conn.select.return_value = ("OK", [b"1"])
        conn.uid.side_effect = fetch
        conn.uid.return_value = ("OK", [(b"1 (BODY[])", b"Subject: Test\r\n\r\nBody")])
        shared = mock.patch.object(email_sync, "_shared_session")
        manager = shared.start()
        self.addCleanup(shared.stop)
        manager.return_value.__enter__.return_value = email_sync._Session(conn, 1)
        return conn


class BodyIdentityTests(MailboxFixture):
    def test_prefetch_reloads_mapping_when_another_sync_adopts_generation_during_select(self):
        storage.record_mailbox_generation(1, "INBOX", "10")
        old_id = self.add_messages()
        new_ids = []

        def select(*args, **kwargs):
            storage.record_mailbox_generation(1, "INBOX", "11")
            new_ids.append(self.add_messages())
            return "OK", [b"1"]

        self.fake_session(select=select, generation=b"11")
        self.assertEqual(email_sync.prefetch_messages(1, "INBOX", ["1"], strict=True), 1)
        self.assertIsNone(storage.load_raw_message(old_id))
        self.assertIsNotNone(storage.load_raw_message(new_ids[0]))

    def test_prefetch_does_not_save_to_a_mapping_retired_during_fetch(self):
        storage.record_mailbox_generation(1, "INBOX", "10")
        old_id = self.add_messages()

        def fetch(*args):
            storage.record_mailbox_generation(1, "INBOX", "11")
            self.add_messages()
            return "OK", [(b"1 (BODY[])", b"Subject: Replacement\r\n\r\nReplacement")]

        self.fake_session(fetch=fetch)
        with self.assertRaisesRegex(RuntimeError, "treści"):
            email_sync.prefetch_messages(1, "INBOX", ["1"], strict=True)
        self.assertIsNone(storage.load_raw_message(old_id))
        self.assertIsNone(storage.load_raw_message(storage.get_message_row_id(1, self.folder, "1")))

    def test_single_fetch_does_not_rebind_downloaded_bytes_to_replacement(self):
        storage.record_mailbox_generation(1, "INBOX", "10")
        old_id = self.add_messages()

        def fetch(*args):
            storage.record_mailbox_generation(1, "INBOX", "11")
            self.add_messages()
            return "OK", [(b"1 (BODY[])", b"Subject: Old\r\n\r\nOld body")]

        self.fake_session(fetch=fetch)
        self.assertIsNone(email_sync.fetch_message_from_imap(1, "INBOX", "1"))
        self.assertIsNone(storage.load_raw_message(old_id))
        self.assertIsNone(storage.load_raw_message(storage.get_message_row_id(1, self.folder, "1")))

    def test_body_write_rejects_changed_generation_identity_and_deleted_rows(self):
        storage.record_mailbox_generation(1, "INBOX", "10")
        row_id = self.add_messages()
        save = lambda rid, generation: storage.save_current_raw_message(1, self.folder, "1", rid, generation, b"body")
        self.assertFalse(save(row_id, "11"))
        self.assertFalse(save(row_id + 1, "10"))
        with get_conn() as conn:
            conn.execute("UPDATE wiadomosci_email SET usunieta=1 WHERE id=%s", (row_id,))
        self.assertFalse(save(row_id, "10"))
        self.assertIsNone(storage.load_raw_message(row_id))

    def test_single_fetch_saves_using_the_generation_consumed_by_select(self):
        storage.record_mailbox_generation(1, "INBOX", "10")
        row_id = self.add_messages()
        conn = self.fake_session()
        self.assertIsNotNone(email_sync.fetch_message_from_imap(1, "INBOX", "1", expected_row_id=row_id))
        self.assertIsNotNone(storage.load_raw_message(row_id))
        conn.response.assert_called_once_with("UIDVALIDITY")

    def test_generation_retirement_cannot_interleave_body_check_and_insert(self):
        storage.record_mailbox_generation(1, "INBOX", "10")
        row_id = self.add_messages()
        writing, release, retiring = threading.Event(), threading.Event(), threading.Event()
        original = storage._write_raw_message

        def write(conn, *args):
            writing.set()
            if not release.wait(3):
                raise TimeoutError("test did not release body writer")
            return original(conn, *args)

        def save():
            with schema_context(self.schema_name):
                return storage.save_current_raw_message(1, self.folder, "1", row_id, "10", b"original")

        def retire():
            with schema_context(self.schema_name):
                retiring.set()
                return storage.record_mailbox_generation(1, "INBOX", "11")

        with mock.patch.object(storage, "_write_raw_message", side_effect=write), ThreadPoolExecutor(2) as pool:
            saved = pool.submit(save)
            try:
                self.assertTrue(writing.wait(3))
                retired = pool.submit(retire)
                self.assertTrue(retiring.wait(3))
                # Retirement waits for the transaction that validated this body.
                with self.assertRaises(TimeoutError):
                    retired.result(timeout=0.1)
            finally:
                release.set()
            self.assertTrue(saved.result(timeout=3))
            self.assertTrue(retired.result(timeout=3))
        self.assertEqual(storage.load_raw_message(row_id), b"original")
        self.assertIsNone(storage.get_message_row_id(1, self.folder, "1"))


class MailListFollowupTests(MailboxFixture):
    def setUp(self):
        super().setUp()
        from app.email import email_ui
        self.ui = email_ui
        for patcher in (
            mock.patch.object(email_ui, "get_email_accounts_for_employee", return_value=[{"id": 1, "email": "review@example.com"}]),
            mock.patch.object(email_ui.EmailPage, "start_background_sync"),
            mock.patch.object(email_ui, "MailBrowser", QTextBrowser),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = email_ui.EmailPage(1)
        self.page.current_folder_local = "INBOX"
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(self.page.stop_background_sync)

    def load_first_page(self, count=1002):
        self.add_messages(count)
        self.page.load_mail_list_from_db("INBOX")
        self.assertEqual(len(self.page._mail_rows), min(count, 500))

    def load_older(self):
        self.page._load_mail_async(more=True)
        self.assertTrue(wait_until(lambda: self.page.older_messages.isEnabled()))

    def test_paging_revalidates_read_flags_and_removes_deleted_rows(self):
        self.load_first_page()
        read_id, deleted_id = [row["id"] for row in self.page._mail_rows[:2]]
        with get_conn() as conn:
            conn.execute("UPDATE wiadomosci_email SET przeczytana=1 WHERE id=%s", (read_id,))
            conn.execute("UPDATE wiadomosci_email SET usunieta=1 WHERE id=%s", (deleted_id,))
        self.load_older()
        rows = {r["id"]: r for r in self.page._mail_rows}
        self.assertTrue(rows[read_id]["przeczytana"])
        self.assertNotIn(deleted_id, rows)
        self.assertFalse(self.page.unread_map[rows[read_id]["uid"]])

    def test_paging_does_not_keep_retired_generation(self):
        storage.record_mailbox_generation(1, "INBOX", "10")
        self.load_first_page()
        old_ids = {r["id"] for r in self.page._mail_rows}
        storage.record_mailbox_generation(1, "INBOX", "11")
        self.add_messages(1002)
        self.load_older()
        self.assertFalse(old_ids & {r["id"] for r in self.page._mail_rows})

    def test_switching_folder_clears_old_click_targets_before_worker_finishes(self):
        self.load_first_page(1)
        self.page.current_uid = "1"
        self.page._current_message_row_id = self.page._mail_rows[0]["id"]
        self.page._current_message_account_id = 1
        item = QTreeWidgetItem(["Archive"])
        item.setData(0, self.ui.ROLE_DATA, "Archive")
        item.setData(0, self.ui.ROLE_ACCOUNT, 1)
        with mock.patch.object(self.page._list_jobs, "submit"):
            self.page.on_folder_clicked(item)
        self.assertEqual(self.page.mail_list.topLevelItemCount(), 0)
        self.assertEqual(self.page._targets(), [])
        self.assertEqual(self.page.current_folder_local, "Archive")

    def test_switching_account_clears_old_items_before_worker_finishes(self):
        self.load_first_page(1)
        self.page.accounts.append({"id": 2, "email": "second@example.com"})
        item = QTreeWidgetItem(["INBOX"])
        item.setData(0, self.ui.ROLE_DATA, "INBOX")
        item.setData(0, self.ui.ROLE_ACCOUNT, 2)
        with mock.patch.object(self.page._list_jobs, "submit"):
            self.page.on_folder_clicked(item)
        self.assertEqual(self.page.current_account["id"], 2)
        self.assertEqual(self.page.mail_list.topLevelItemCount(), 0)
        self.assertEqual(self.page._targets(), [])

    def test_refresh_preserves_loaded_older_selection_and_preview(self):
        self.load_first_page()
        self.load_older()
        selected = self.page._mail_rows[-1]
        self.page.current_uid = selected["uid"]
        self.page._current_message_row_id = selected["id"]
        cached = self.page.cached_message = object()
        self.page.mail_view.setPlainText("kept preview")
        self.page.load_mail_list_from_db("INBOX", keep_view=True)
        self.assertEqual(len(self.page._mail_rows), 1000)
        self.assertIs(self.page.cached_message, cached)
        self.assertEqual(self.page.current_uid, selected["uid"])
        self.assertEqual(self.page.mail_list.currentItem().data(0, self.ui.ROLE_ROW_ID), selected["id"])
        self.assertEqual(self.page.mail_view.toPlainText(), "kept preview")
        self.page._load_mail_async(keep_view=True)
        self.assertTrue(wait_until(lambda: self.page.older_messages.isEnabled()))
        self.assertEqual(len(self.page._mail_rows), 1000)
        self.assertIs(self.page.cached_message, cached)
        self.load_older()
        self.assertEqual(len(self.page._mail_rows), 1002)
        self.assertTrue(self.page.older_messages.isHidden())

    def test_retained_range_applies_current_assignment_filter(self):
        self.load_first_page(501)
        all_rows = storage.get_mail_page(1, "INBOX", through=("", 0))[0]
        ids = [r["id"] for r in all_rows]
        storage.set_employee_assignment(ids, 1, True)
        self.page.employee_filter = 1
        self.page.load_mail_list_from_db("INBOX")
        storage.set_employee_assignment([ids[0]], 1, False)
        self.load_older()
        self.assertNotIn(ids[0], {r["id"] for r in self.page._mail_rows})

    def test_appended_notification_target_has_assignment_and_completion_metadata(self):
        oldest = self.add_messages(1002)
        storage.set_employee_assignment([oldest], 1, True)
        storage.set_messages_completed([oldest], True)
        ensure_row = storage.get_message_by_uid(1, "1", self.folder)
        self.page.load_mail_list_from_db("INBOX", ensure_row=ensure_row)
        self.assertEqual(self.page.assign_map[oldest], [1])
        self.assertIn(oldest, self.page.completed_map)
        self.assertIn(oldest, {r["id"] for r in self.page._mail_rows})
        cursor = self.page._mail_cursor
        self.page.load_mail_list_from_db("INBOX", keep_view=True)
        self.assertIn(oldest, {r["id"] for r in self.page._mail_rows})
        self.assertEqual(len(self.page._mail_rows), 501)
        self.assertEqual(self.page._mail_cursor, cursor)
        self.load_older()
        self.assertEqual(len(self.page._mail_rows), 1001)
        self.load_older()
        self.assertEqual(len(self.page._mail_rows), 1002)

    def set_fallback_identity(self):
        row_id = self.add_messages()
        self.page.current_uid = "1"
        self.page._current_message_row_id = row_id
        self.page._current_message_account_id = 1
        return row_id

    def test_move_without_selected_item_uses_captured_row_identity(self):
        row_id = self.set_fallback_identity()
        with mock.patch.object(self.page, "_run_async", side_effect=lambda work, done: work()), \
                mock.patch.object(self.ui, "move_emails") as move:
            self.page._move_refs(self.page._targets(), "Archive", "Move")
        move.assert_called_once_with(1, "INBOX", ["1"], "Archive", expected_rows={"1": row_id})

    def test_permanent_delete_without_selected_item_uses_captured_row_identity(self):
        row_id = self.set_fallback_identity()
        with mock.patch.object(self.page, "_run_async", side_effect=lambda work, done: work()), \
                mock.patch.object(self.page, "_role_of", return_value="trash"), \
                mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(self.ui, "delete_email_remote", return_value=True) as delete:
            self.page.on_delete()
        delete.assert_called_once_with(1, "INBOX", "1", expected_row_id=row_id)

    def test_fallback_rejects_missing_or_different_account_identity(self):
        self.set_fallback_identity()
        self.page._current_message_row_id = None
        self.assertEqual(self.page._targets(), [])
        self.set_fallback_identity()
        self.page.current_account = {"id": 2}
        self.assertEqual(self.page._targets(), [])


class OtherReviewFollowupTests(DbTestCase):
    def setUp(self):
        super().setUp()
        for name in ("warning", "information", "critical", "question"):
            patcher = mock.patch.object(QMessageBox, name, return_value=QMessageBox.StandardButton.Yes)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_calendar_starts_sync_before_its_tab_is_opened(self):
        from app import main_ui
        with mock.patch.object(main_ui.OfficeManagerApp, "_start_saldeo_push_watcher"), \
                mock.patch.object(main_ui.CalendarPage, "_auto_sync") as sync, \
                mock.patch.object(main_ui.EmailPage, "start_background_sync"), \
                mock.patch("app.email.email_ui.MailBrowser", QTextBrowser):
            window = main_ui.OfficeManagerApp(1)
            try:
                self.assertEqual(window.pages.currentIndex(), 0)
                calendar = window.pages.widget(8)
                self.assertIsInstance(calendar, main_ui.CalendarPage)
                self.assertTrue(calendar._auto_timer.isActive())
                self.assertTrue(wait_until(lambda: sync.called))
                self.assertEqual(window.pages.currentIndex(), 0)
                calendar._auto_timer.timeout.emit()
                self.assertGreaterEqual(sync.call_count, 2)
            finally:
                window.close()
                window.deleteLater()

    def test_deletion_is_blocked_during_an_import_before_opening_dialogs(self):
        from app.reports.reports_ui import ReportsPage
        page = ReportsPage(1)
        try:
            page._import_busy = True
            with mock.patch("PyQt6.QtWidgets.QInputDialog.getItem") as dialog, \
                    mock.patch("app.reports.import_dao.delete_imported") as delete:
                page._delete_imported()
            dialog.assert_not_called()
            delete.assert_not_called()
            self.assertIn("importu", page.report_status.text())
            self.assertTrue(page._import_busy)
        finally:
            page.deleteLater()

    def test_deletion_rechecks_import_started_while_confirmation_was_open(self):
        from app.reports.reports_ui import ReportsPage
        page = ReportsPage(1)

        def confirm(*args):
            page._import_busy = True
            return QMessageBox.StandardButton.Yes

        try:
            with mock.patch("PyQt6.QtWidgets.QInputDialog.getItem", return_value=("Faktury", True)), \
                    mock.patch.object(QMessageBox, "question", side_effect=confirm), \
                    mock.patch("app.reports.import_dao.delete_imported") as delete:
                page._delete_imported()
            delete.assert_not_called()
        finally:
            page.deleteLater()
