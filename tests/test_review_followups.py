"""Regressions for PR review: mailbox identity, retained pages and UI concurrency."""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from PyQt6.QtCore import Qt, QTimer
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


class HeaderGenerationTests(MailboxFixture):
    def setUp(self):
        super().setUp()
        storage.record_mailbox_generation(1, "INBOX", "10")
        self.add_messages(2)

    @staticmethod
    def header(uid="1", subject="Stale", seen=False, refs="<old@review>"):
        return {"uid": uid, "message_id": f"<{subject}-{uid}@review>",
                "subject": subject, "sender": f"{subject}@example.com",
                "date": "2999-01-01 10:00", "is_seen": seen, "refs": refs}

    def replace_mailbox(self, generation="11"):
        storage.record_mailbox_generation(1, "INBOX", generation)
        storage.save_message_headers(1, self.folder, [
            self.header(uid, "Replacement", True, "<new@review>") for uid in ("1", "2")
        ])

    def assert_replacement_unchanged(self):
        for uid in ("1", "2"):
            row = storage.get_message_by_uid(1, uid, self.folder)
            self.assertEqual(row["temat"], "Replacement")
            self.assertEqual(row["nadawca"], "Replacement@example.com")
            self.assertEqual(row["refs"], "<new@review>")
            self.assertTrue(row["przeczytana"])
            self.assertFalse(row["usunieta"])

    def write_batch(self, kind, generation):
        if kind == "headers":
            storage.save_message_headers(1, self.folder, [self.header()], expected_generation=generation)
        elif kind == "flags":
            storage.apply_server_state(1, self.folder, {"1"}, {"1"}, "2000-01-01", expected_generation=generation)
        else:
            storage.save_refs(1, self.folder, [("1", "<old@review>")], expected_generation=generation)

    def test_stale_header_flag_and_reference_batches_cannot_change_replacements(self):
        self.replace_mailbox()
        for kind in ("headers", "flags", "refs"):
            with self.subTest(kind=kind), self.assertRaises(storage.MailboxGenerationChanged):
                self.write_batch(kind, "10")
            self.assert_replacement_unchanged()

    def test_missing_generation_does_not_bypass_a_known_checkpoint(self):
        with self.assertRaises(storage.MailboxGenerationChanged):
            storage.save_message_headers(1, self.folder, [self.header()], expected_generation=None)
        with self.assertRaises(storage.MailboxGenerationChanged):
            storage.apply_server_state(1, self.folder, set(), set(), "2000-01-01", expected_generation=None)

    def test_empty_header_batch_still_validates_its_generation(self):
        self.replace_mailbox()
        with self.assertRaises(storage.MailboxGenerationChanged):
            storage.save_message_headers(1, self.folder, [], expected_generation="10")
        self.assert_replacement_unchanged()

    def test_header_flag_and_reference_transactions_hold_the_generation_lock_through_commit(self):
        original = storage._lock_mailbox_generation
        for index, kind in enumerate(("headers", "flags", "refs")):
            generation = str(10 + index)
            self.add_messages(2)
            locked, release, retiring = threading.Event(), threading.Event(), threading.Event()

            def lock(*args):
                original(*args)
                locked.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release batch writer")

            def write():
                with schema_context(self.schema_name):
                    self.write_batch(kind, generation)

            def retire():
                with schema_context(self.schema_name):
                    retiring.set()
                    return storage.record_mailbox_generation(1, "INBOX", str(int(generation) + 1))

            with self.subTest(kind=kind), mock.patch.object(storage, "_lock_mailbox_generation", side_effect=lock), ThreadPoolExecutor(2) as pool:
                written = pool.submit(write)
                try:
                    self.assertTrue(locked.wait(3))
                    retired = pool.submit(retire)
                    self.assertTrue(retiring.wait(3))
                    with self.assertRaises(TimeoutError):
                        retired.result(timeout=0.1)
                finally:
                    release.set()
                written.result(timeout=3)
                self.assertTrue(retired.result(timeout=3))

    def header_imap(self, generation=b"10", select=None, empty=False):
        conn = mock.Mock()
        conn.select.return_value = ("OK", [b"2"])
        conn.select.side_effect = select
        conn.response.side_effect = [("UIDVALIDITY", [generation]), ("UIDVALIDITY", [None])]

        def uid(command, *args):
            if command == "SEARCH":
                return "OK", [b"" if empty or "UNSEEN" in args else b"1"]
            return "OK", [(b"1 (UID 1 FLAGS (\\Seen) BODY[HEADER] {50}",
                            b"Subject: Remote replacement\r\nMessage-ID: <remote@review>\r\n\r\n")]

        conn.uid.side_effect = uid
        return conn

    def fetch_headers(self, conn, **kwargs):
        with mock.patch.object(email_sync, "_get_account_info", return_value={"id": 1}), \
                mock.patch.object(email_sync, "_connect_imap", return_value=conn):
            return email_sync.fetch_headers(1, "INBOX", **kwargs)

    def test_known_uids_are_reloaded_when_another_selector_already_adopted_the_generation(self):
        def select(*args, **kwargs):
            storage.record_mailbox_generation(1, "INBOX", "11")
            return "OK", [b"1"]

        flags = {}
        conn = self.header_imap(generation=b"11", select=select)
        rows = self.fetch_headers(conn, known_uids={"1", "2"}, flags_out=flags)
        self.assertEqual([row["uid"] for row in rows], ["1"])
        self.assertEqual(flags["generation"], "11")
        conn.response.assert_called_once_with("UIDVALIDITY")

    def test_full_header_refresh_does_not_skip_existing_uids(self):
        rows = self.fetch_headers(self.header_imap(), known_uids=None)
        self.assertEqual([row["uid"] for row in rows], ["1"])

    def test_empty_search_carries_the_selected_generation(self):
        flags = {}
        self.assertEqual(self.fetch_headers(self.header_imap(empty=True), flags_out=flags), [])
        self.assertEqual(flags, {"generation": "10", "all": set(), "unseen": set()})

    def test_late_selector_cannot_roll_back_a_generation_adopted_during_select(self):
        def select(*args, **kwargs):
            self.replace_mailbox()
            return "OK", [b"2"]

        conn = self.header_imap(select=select)
        with self.assertRaises(storage.MailboxGenerationChanged):
            self.fetch_headers(conn)
        self.assertEqual(storage.get_mailbox_generation(1, "INBOX"), "11")
        self.assert_replacement_unchanged()

    def test_shared_selector_cannot_roll_back_a_generation_adopted_during_select(self):
        def select(*args, **kwargs):
            self.replace_mailbox()
            return "OK", [b"2"]

        with self.assertRaises(storage.MailboxGenerationChanged):
            email_sync._Session(self.header_imap(select=select), 1).select("INBOX", True)
        self.assert_replacement_unchanged()

    def check_sync_retry(self, phase, empty=False):
        attempts = []

        def fetch(*args, flags_out, **kwargs):
            first = not attempts
            attempts.append(True)
            flags_out.update(generation="10" if first else "11",
                             all=set() if first and empty else ({"1"} if first else {"1", "2"}),
                             unseen={"1"} if first else set())
            if first and phase == "headers":
                self.replace_mailbox()
            return [self.header()] if first and not empty else []

        backfills = []

        def backfill(*args):
            if not backfills and phase == "flags":
                self.replace_mailbox()
            backfills.append(True)
            return 0

        with mock.patch.object(email_sync, "fetch_headers", side_effect=fetch), \
                mock.patch.object(email_sync, "backfill_refs", side_effect=backfill):
            self.assertEqual(email_sync.sync_folder(1, "INBOX"), 0)
        self.assertEqual(len(attempts), 2)
        self.assert_replacement_unchanged()

    def test_sync_retries_headers_fetched_before_another_client_adopted_a_generation(self):
        self.check_sync_retry("headers")

    def test_sync_retries_flags_when_generation_changes_after_header_commit(self):
        self.check_sync_retry("flags")

    def test_sync_retries_an_empty_stale_snapshot_instead_of_deleting_new_messages(self):
        self.check_sync_retry("headers", empty=True)

    def test_repeated_generation_changes_fail_after_a_bounded_retry(self):
        attempts = []

        def fetch(*args, flags_out, **kwargs):
            generation = storage.get_mailbox_generation(1, "INBOX")
            flags_out.update(generation=generation, all=set(), unseen=set())
            self.replace_mailbox(str(int(generation) + 1))
            attempts.append(True)
            return []

        with mock.patch.object(email_sync, "fetch_headers", side_effect=fetch), \
                self.assertRaises(storage.MailboxGenerationChanged):
            email_sync.sync_folder(1, "INBOX")
        self.assertEqual(len(attempts), 2)
        self.assert_replacement_unchanged()

    def test_reference_backfill_rejects_a_generation_changed_during_fetch(self):
        def fetch(*args):
            self.replace_mailbox()
            return "OK", [(b"1 (UID 1 BODY[HEADER] {30}", b"References: <old@review>\r\n\r\n")]

        self.fake_session(fetch=fetch)
        with self.assertRaises(storage.MailboxGenerationChanged):
            email_sync.backfill_refs(1, "INBOX", self.folder)
        self.assert_replacement_unchanged()

    def check_stale_rule_action(self, mark_read):
        from app.email import email_rules
        original = email_rules.apply_rules

        def fetch(*args, flags_out, **kwargs):
            flags_out.update(generation="10", all={"1", "2"}, unseen={"1"})
            return [self.header()]

        def apply_after_retirement(*args, **kwargs):
            self.replace_mailbox()
            return original(*args, **kwargs)

        rule = {"target": "Archive", "temat_zawiera": "Stale", "oznacz_jako_przeczytane": mark_read}
        conn = self.fake_session(generation=b"11")
        with mock.patch.object(email_sync, "fetch_headers", side_effect=fetch), \
                mock.patch.object(email_sync, "backfill_refs", return_value=0), \
                mock.patch.object(email_rules, "_active_rules", return_value=[rule]), \
                mock.patch.object(email_rules, "apply_rules", side_effect=apply_after_retirement):
            self.assertEqual(email_sync.sync_folder(1, "INBOX", full_sync=False), 1)
        conn.uid.assert_not_called()
        self.assert_replacement_unchanged()

    def test_rules_cannot_mark_replacements_read_using_a_stale_header_batch(self):
        self.check_stale_rule_action(True)

    def test_rules_cannot_move_replacements_using_a_stale_header_batch(self):
        self.check_stale_rule_action(False)


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
        self.assertFalse(self.page.unread_map[read_id])

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

    def prepare_account_wide_filter(self, completed):
        self.add_messages(1002)
        archive = storage.save_folder(1, "Archive")
        with get_conn() as conn:
            conn.execute("UPDATE wiadomosci_email SET folder_id=%s WHERE uid IN ('3','1001')", (archive,))
            ids = [row[0] for row in conn.execute("SELECT id FROM wiadomosci_email")]
        storage.set_employee_assignment(ids, 1, True)
        storage.set_messages_completed(ids, True)
        self.page.employee_filter = None if completed else 1
        self.page.completed_filter = completed
        self.page.load_mail_list_from_db("INBOX")

    def click_message(self, uid):
        item = next(self.page.mail_list.topLevelItem(i) for i in range(self.page.mail_list.topLevelItemCount())
                    if self.page.mail_list.topLevelItem(i).data(0, self.ui.ROLE_DATA) == uid)
        with mock.patch.object(self.page, "_run_async"):
            self.page.on_mail_clicked(item)

    def check_account_wide_refresh(self, completed):
        self.prepare_account_wide_filter(completed)
        self.load_older()
        context = self.page._mail_context
        self.click_message("3")
        self.assertEqual(self.page.current_folder_local, "Archive")
        cached = self.page.cached_message = object()
        self.page.load_mail_list_from_db("Archive", keep_view=True)
        self.assertEqual(len(self.page._mail_rows), 1000)
        self.assertEqual(self.page._mail_context, context)
        self.assertIs(self.page.cached_message, cached)
        self.assertEqual(self.page.current_uid, "3")
        self.page._load_mail_async(keep_view=True)
        self.assertTrue(wait_until(lambda: self.page.older_messages.isEnabled()))
        self.assertEqual(len(self.page._mail_rows), 1000)
        self.assertIs(self.page.cached_message, cached)
        self.load_older()
        self.assertEqual(len(self.page._mail_rows), 1002)

    def test_assignment_list_context_survives_opening_another_folder(self):
        self.check_account_wide_refresh(False)

    def test_completed_list_context_survives_opening_another_folder(self):
        self.check_account_wide_refresh(True)

    def check_pending_account_wide_page(self, completed):
        self.prepare_account_wide_filter(completed)
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
        work, loaded, _ = submit.call_args.args
        self.assertFalse(self.page.older_messages.isEnabled())
        self.click_message("1001")
        self.assertEqual(self.page.current_folder_local, "Archive")
        loaded(work())
        self.assertTrue(self.page.older_messages.isEnabled())
        self.assertEqual(len(self.page._mail_rows), 1000)
        self.load_older()
        self.assertEqual(len(self.page._mail_rows), 1002)

    def test_pending_assignment_page_completes_after_opening_another_folder(self):
        self.check_pending_account_wide_page(False)

    def test_pending_completed_page_completes_after_opening_another_folder(self):
        self.check_pending_account_wide_page(True)

    def test_body_callback_uses_current_item_after_account_wide_paging_replaced_the_clicked_item(self):
        from email.message import EmailMessage
        self.prepare_account_wide_filter(True)
        item = next(self.page.mail_list.topLevelItem(i) for i in range(self.page.mail_list.topLevelItemCount())
                    if self.page.mail_list.topLevelItem(i).data(0, self.ui.ROLE_DATA) == "1001")
        row_id = item.data(0, self.ui.ROLE_ROW_ID)
        with mock.patch.object(self.page, "_run_async") as run:
            self.page.on_mail_clicked(item)
        _, done = run.call_args.args
        self.load_older()
        message = EmailMessage()
        message["Subject"] = "Loaded"
        message.set_content("Body")
        with mock.patch.object(self.page, "_render_message"), \
                mock.patch.object(self.page, "_run_async"):
            done(message, None)
        row = next(row for row in self.page._mail_rows if row["id"] == row_id)
        self.assertTrue(row["przeczytana"])
        self.assertTrue(storage.get_message_by_uid(1, "1001", storage.get_folder_id(1, "Archive"))["przeczytana"])

    def test_permanent_delete_uses_ids_captured_before_a_nested_dialog_refresh(self):
        self.load_first_page(1)
        item = self.page.mail_list.topLevelItem(0)
        row_id = item.data(0, self.ui.ROLE_ROW_ID)
        self.page.mail_list.blockSignals(True)
        item.setSelected(True)
        self.page.mail_list.blockSignals(False)
        refreshed = []

        def refresh():
            self.page.load_mail_list_from_db("INBOX", keep_view=True)
            refreshed.append(True)

        def confirm(*args):
            QTimer.singleShot(0, refresh)
            self.assertTrue(wait_until(lambda: refreshed))
            return QMessageBox.StandardButton.Yes

        with mock.patch.object(self.page, "_role_of", return_value="trash"), \
                mock.patch.object(self.page, "_run_async", side_effect=lambda work, done: work()), \
                mock.patch.object(QMessageBox, "question", side_effect=confirm), \
                mock.patch.object(self.ui, "delete_email_remote", return_value=True) as delete:
            self.page.on_delete()
        delete.assert_called_once_with(1, "INBOX", "1", expected_row_id=row_id)


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
                calendar = window.page_calendar
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
