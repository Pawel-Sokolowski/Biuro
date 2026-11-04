"""Stable row selection and consistent list reads across concurrent UI changes."""
import threading
from unittest import mock

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QMessageBox, QTextBrowser

from app.db.dao_conn import get_conn
from app.email import email_storage as storage, email_ui
from tests.test_review_followups import MailboxFixture
from tests.test_review_optimizations import wait_until


class MailListConsistencyTests(MailboxFixture):
    def setUp(self):
        super().setUp()
        storage.record_mailbox_generation(1, "INBOX", "10")
        self.add_messages(502)
        self.archive = storage.save_folder(1, "Archive")
        storage.save_message_headers(1, self.archive, [
            {"uid": uid, "message_id": f"<archive-{uid}@review>", "subject": f"Archive {uid}",
             "date": "2026-10-01 10:00", "is_seen": True}
            for uid in ("501", "502")
        ])
        with get_conn() as conn:
            self.rows = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM wiadomosci_email")}
        self.ids = {(row["folder_id"], row["uid"]): row_id for row_id, row in self.rows.items()}
        storage.set_employee_assignment(list(self.rows), 1, True)
        storage.set_messages_completed(list(self.rows), True)
        for patcher in (
            mock.patch.object(email_ui, "get_email_accounts_for_employee", return_value=[{"id": 1, "email": "review@example.com"}]),
            mock.patch.object(email_ui.EmailPage, "start_background_sync"),
            mock.patch.object(email_ui, "MailBrowser", QTextBrowser),
            mock.patch.object(email_ui.EmailPage, "_run_async"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = email_ui.EmailPage(1)
        self.page.current_folder_local = "INBOX"
        self.addCleanup(self.close_page)

    def close_page(self):
        self.page._loading_message_key = None
        self.page._load_token += 1
        self.page.stop_background_sync()
        self.page.deleteLater()

    def load(self, *, account_wide=True, employee=False):
        self.page.employee_filter = 1 if employee else None
        self.page.completed_filter = account_wide and not employee
        self.page.load_mail_list_from_db("INBOX")

    def items(self):
        result = {}
        for item in self.page.mail_list.all_items():
            result[item.data(0, email_ui.ROLE_ROW_ID)] = item
        return result

    def select(self, row_ids, *, current=None):
        items = self.items()
        current = current if current is not None else (row_ids[0] if row_ids else None)
        self.page.mail_list.blockSignals(True)
        self.page.mail_list.clearSelection()
        if current is not None:
            self.page.mail_list.setCurrentItem(items[current], 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        for row_id in row_ids:
            items[row_id].setSelected(True)
        self.page.mail_list.blockSignals(False)
        if current is not None:
            row = self.rows[current]
            self.page.current_uid = row["uid"]
            self.page.current_folder_local = "INBOX" if row["folder_id"] == self.folder else "Archive"
            self.page._current_message_row_id = current
            self.page._current_message_account_id = 1
            self.page.cached_message = object()

    def selected_ids(self):
        return {item.data(0, email_ui.ROLE_ROW_ID) for item in self.page.mail_list.selectedItems()}

    def older(self):
        self.page._load_mail_async(more=True)
        self.assertTrue(wait_until(lambda: self.page.older_messages.isEnabled()))
        self.assertEqual(len(self.page._mail_rows), 504)

    def check_selection(self, employee=False):
        self.load(employee=employee)
        selected = [self.ids[self.folder, uid] for uid in ("502", "501")]
        self.select(selected)
        cached = self.page.cached_message
        self.older()
        self.assertEqual(self.selected_ids(), set(selected))
        self.assertEqual(self.page.mail_list.currentItem().data(0, email_ui.ROLE_ROW_ID), selected[0])
        self.assertIs(self.page.cached_message, cached)
        self.page.load_mail_list_from_db("INBOX", keep_view=True)
        self.assertEqual(self.selected_ids(), set(selected))
        self.assertEqual(self.page.mail_list.currentItem().data(0, email_ui.ROLE_ROW_ID), selected[0])
        return selected

    def test_completed_paging_and_refresh_restore_exact_row_selection(self):
        self.check_selection()

    def test_employee_paging_and_refresh_restore_exact_row_selection(self):
        self.check_selection(employee=True)

    def test_paged_move_does_not_include_other_folders_with_the_same_uids(self):
        selected = self.check_selection()
        with mock.patch.object(self.page, "_run_async", side_effect=lambda work, done: work()), \
                mock.patch.object(email_ui, "move_emails") as move:
            self.page._move_refs(self.page._targets(), "Target", "Move")
        move.assert_called_once_with(1, "INBOX", ["502", "501"], "Target",
                                     expected_rows={"502": selected[0], "501": selected[1]})

    def test_paged_delete_does_not_include_other_folders_with_the_same_uids(self):
        selected = self.check_selection()
        with mock.patch.object(self.page, "_role_of", return_value="trash"), \
                mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(self.page, "_run_async", side_effect=lambda work, done: work()), \
                mock.patch.object(email_ui, "delete_email_remote", return_value=True) as delete:
            self.page.on_delete()
        self.assertEqual(delete.call_args_list, [mock.call(1, "INBOX", uid, expected_row_id=row_id)
                                                for uid, row_id in zip(("502", "501"), selected)])

    def test_refresh_preserves_focus_without_creating_a_selection(self):
        self.load()
        row_id = self.ids[self.folder, "502"]
        self.select([], current=row_id)
        self.page.load_mail_list_from_db("INBOX", keep_view=True)
        self.assertEqual(self.selected_ids(), set())
        self.assertEqual(self.page.mail_list.currentItem().data(0, email_ui.ROLE_ROW_ID), row_id)

    def test_retirement_drops_only_invalid_selection_and_never_selects_reused_uids(self):
        self.load()
        self.older()
        old_id = self.ids[self.folder, "502"]
        survivor = self.ids[self.archive, "502"]
        self.select([old_id, survivor])
        storage.record_mailbox_generation(1, "INBOX", "11")
        replacement = storage.save_message_headers(1, self.folder, [{"uid": "502", "subject": "Replacement"}])["502"]
        storage.set_messages_completed([replacement], True)
        self.page.load_mail_list_from_db("INBOX", keep_view=True)
        self.assertEqual(self.selected_ids(), {survivor})
        self.assertNotIn(replacement, self.selected_ids())
        self.assertIsNone(self.page.cached_message)

    def test_opening_another_folders_equal_uid_loads_its_own_preview(self):
        self.load()
        self.older()
        self.select([self.ids[self.archive, "502"]])
        self.page._current_message_row_id = self.ids[self.folder, "502"]
        self.page.current_folder_local = "INBOX"
        with mock.patch.object(self.page, "on_mail_clicked", wraps=self.page.on_mail_clicked) as clicked:
            self.page._open_selected_now()
        clicked.assert_called_once()
        self.assertEqual(self.page._current_message_row_id, self.ids[self.archive, "502"])
        self.assertEqual(self.page.current_folder_local, "Archive")

    def test_unread_state_is_independent_for_equal_uids_in_different_folders(self):
        self.load()
        self.older()
        inbox, archive = self.ids[self.folder, "502"], self.ids[self.archive, "502"]
        self.assertTrue(self.page.unread_map[inbox])
        self.assertFalse(self.page.unread_map[archive])
        self.page._mark_opened_as_read(inbox, 1, "INBOX", "502")
        self.assertFalse(self.page.unread_map[inbox])
        self.assertFalse(self.page.unread_map[archive])
        self.select([archive])
        self.page._set_read_selected(False)
        self.assertFalse(self.page.unread_map[inbox])
        self.assertTrue(self.page.unread_map[archive])

    def fetch_during_change(self, mutate):
        original = email_ui.get_mail_page
        calls = []

        def query(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(True)
            if len(calls) == 1:
                mutate()
            return result

        with mock.patch.object(email_ui, "get_mail_page", side_effect=query):
            result = self.page._fetch_mail_data(self.page._mail_request(), existing=tuple(self.page._mail_rows),
                                                through=self.page._mail_cursor)
        self.assertEqual(len(calls), 2)
        return result

    def test_range_merge_never_combines_retired_and_replacement_generations(self):
        self.load(account_wide=False)
        previous = {row["id"] for row in self.page._mail_rows}

        def retire():
            storage.record_mailbox_generation(1, "INBOX", "11")
            storage.save_message_headers(1, self.folder, [{"uid": "1", "subject": "Replacement", "date": "2026-11-01 10:00"}])

        rows, *_ = self.fetch_during_change(retire)
        self.assertEqual({row["id"] for row in rows}, previous)
        self.assertFalse(any(str(row["uid"]).startswith("retired:") for row in rows))
        self.page.load_mail_list_from_db("INBOX", keep_view=True)
        self.assertFalse(previous & {row["id"] for row in self.page._mail_rows})

    def test_range_merge_cannot_resurrect_deleted_rows_alongside_newer_read_flags(self):
        self.load(account_wide=False)
        read_id, deleted_id = [row["id"] for row in self.page._mail_rows[:2]]

        def change():
            with get_conn() as conn:
                conn.execute("UPDATE wiadomosci_email SET przeczytana=1 WHERE id=%s", (read_id,))
                conn.execute("UPDATE wiadomosci_email SET usunieta=1 WHERE id=%s", (deleted_id,))

        rows, *_ = self.fetch_during_change(change)
        snapshot = {row["id"]: row for row in rows}
        self.assertIn(deleted_id, snapshot)
        self.assertFalse(snapshot[read_id]["przeczytana"])

    def test_assignments_and_completion_maps_use_the_same_snapshot_as_the_rows(self):
        self.load(account_wide=False)
        row_id = self.page._mail_rows[0]["id"]

        def change():
            storage.set_employee_assignment([row_id], 1, False)
            storage.set_messages_completed([row_id], False)

        rows, _, _, _, assigned, completed = self.fetch_during_change(change)
        row = next(row for row in rows if row["id"] == row_id)
        self.assertTrue(row["email_done"])
        self.assertEqual(assigned[row_id], [1])
        self.assertIn(row_id, completed)

    def test_stale_notification_target_is_not_reintroduced_after_retirement(self):
        self.load(account_wide=False)
        old = storage.get_message_by_uid(1, "1", self.folder)
        storage.record_mailbox_generation(1, "INBOX", "11")
        self.page.load_mail_list_from_db("INBOX", ensure_row=old)
        self.assertNotIn(old["id"], {row["id"] for row in self.page._mail_rows})

    def test_snapshot_isolation_and_read_only_state_do_not_leak_into_the_pool(self):
        for fail in (False, True):
            try:
                with storage.mail_read_snapshot() as conn:
                    self.assertEqual(conn.execute("SHOW transaction_isolation").fetchone()[0], "repeatable read")
                    self.assertEqual(conn.execute("SHOW transaction_read_only").fetchone()[0], "on")
                    if fail:
                        raise ValueError("rollback this read")
            except ValueError:
                pass
            with get_conn() as conn:
                self.assertEqual(conn.execute("SHOW transaction_isolation").fetchone()[0], "read committed")
                self.assertEqual(conn.execute("SHOW transaction_read_only").fetchone()[0], "off")
                conn.execute("UPDATE wiadomosci_email SET temat='Writable' WHERE id=%s", (self.ids[self.folder, "1"],))

    def check_pending_read_change(self, *, opened=False, read=True):
        row_id = self.ids[self.folder, "502"]
        storage.update_message_read_flag(1, "502", not read, self.folder, expected_row_id=row_id)
        self.load(account_wide=False)
        self.select([row_id])
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            old_work, old_loaded, _ = submit.call_args.args
            stale = old_work()
            if opened:
                self.page._mark_opened_as_read(row_id, 1, "INBOX", "502")
            else:
                self.page._set_read_selected(read)
            self.assertEqual(submit.call_count, 2)
            old_loaded(stale)
            self.assertEqual(self.page.unread_map[row_id], not read)
            work, loaded, _ = submit.call_args.args
            loaded(work())
        self.assertEqual(len(self.page._mail_rows), 502)
        self.assertEqual(self.page.unread_map[row_id], not read)
        self.assertEqual(bool(next(row for row in self.page._mail_rows if row["id"] == row_id)["przeczytana"]), read)
        self.assertTrue(self.page.older_messages.isEnabled())

    def test_body_read_completion_restarts_pending_paging_and_rejects_its_stale_result(self):
        self.check_pending_read_change(opened=True)

    def test_mark_read_restarts_pending_paging_and_rejects_its_stale_result(self):
        self.check_pending_read_change(read=True)

    def test_mark_unread_restarts_pending_paging_and_rejects_its_stale_result(self):
        self.check_pending_read_change(read=False)

    def test_real_worker_finishing_after_a_newer_read_change_cannot_overwrite_it(self):
        self.load(account_wide=False)
        row_id = self.ids[self.folder, "502"]
        self.select([row_id])
        captured, release = threading.Event(), threading.Event()
        original = self.page._fetch_mail_data
        counter_lock = threading.Lock()
        calls = []

        def fetch(*args, **kwargs):
            with counter_lock:
                first = not calls
                calls.append(True)
            payload = original(*args, **kwargs)
            if first:
                captured.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release the stale list result")
            return payload

        with mock.patch.object(self.page, "_fetch_mail_data", side_effect=fetch):
            try:
                self.page._load_mail_async(more=True)
                self.assertTrue(wait_until(captured.is_set))
                self.page._mark_opened_as_read(row_id, 1, "INBOX", "502")
                self.assertTrue(wait_until(lambda: len(self.page._mail_rows) == 502 and self.page.older_messages.isEnabled()))
                self.assertFalse(self.page.unread_map[row_id])
            finally:
                release.set()
            self.assertTrue(wait_until(lambda: not self.page._list_jobs._jobs))
        self.assertFalse(self.page.unread_map[row_id])

    def test_multiple_read_changes_keep_the_latest_state_and_pending_request(self):
        self.load(account_wide=False)
        row_id = self.ids[self.folder, "502"]
        self.select([row_id])
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            first_work, first_loaded, first_failed = submit.call_args.args
            first = first_work()
            self.page._set_read_selected(True)
            second_work, second_loaded, _ = submit.call_args.args
            second = second_work()
            self.page._set_read_selected(False)
            self.assertEqual(submit.call_count, 3)
            first_loaded(first)
            second_loaded(second)
            first_failed(RuntimeError("obsolete request"))
            self.assertTrue(self.page.unread_map[row_id])
            self.assertFalse(self.page.older_messages.isEnabled())
            work, loaded, _ = submit.call_args.args
            loaded(work())
        self.assertTrue(self.page.unread_map[row_id])
        self.assertTrue(self.page.older_messages.isEnabled())

    def test_failed_read_write_does_not_optimistically_change_the_row_state(self):
        self.load(account_wide=False)
        row_id = self.ids[self.folder, "502"]
        self.select([row_id])
        with mock.patch.object(email_ui, "update_message_read_flag", return_value=False):
            self.page._set_read_selected(True)
        self.assertTrue(self.page.unread_map[row_id])
        self.assertFalse(next(row for row in self.page._mail_rows if row["id"] == row_id)["przeczytana"])

    def test_synchronous_reload_reenables_paging_and_rejects_the_cancelled_callback(self):
        self.load()
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            work, old_loaded, _ = submit.call_args.args
            stale = work()
            self.assertFalse(self.page.older_messages.isEnabled())
            self.page.load_mail_list_from_db("INBOX", keep_view=True)
            self.assertTrue(self.page.older_messages.isEnabled())
            self.assertTrue(self.page.older_messages.isHidden())
            old_loaded(stale)
            self.assertEqual(len(self.page._mail_rows), 504)
            self.assertTrue(self.page.older_messages.isHidden())

    def test_synchronous_filter_reload_reenables_paging(self):
        self.load()
        with mock.patch.object(self.page._list_jobs, "submit"):
            self.page._load_mail_async(more=True)
        self.page.employee_filter, self.page.completed_filter = 1, False
        self.page.load_mail_list_from_db("INBOX")
        self.assertTrue(self.page.older_messages.isEnabled())
        self.assertFalse(self.page.older_messages.isHidden())
        self.older()

    def check_refresh_preserves_paging(self, refresh):
        self.load()
        row_id = self.ids[self.folder, "502"]
        self.select([row_id])
        cached = self.page.cached_message
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            old_work, old_loaded, old_failed = submit.call_args.args
            stale = old_work()
            refresh()
            self.assertEqual(submit.call_count, 2)
            self.assertTrue(self.page._pending_mail_load[1])
            old_loaded(stale)
            old_failed(RuntimeError("obsolete page request"))
            self.assertFalse(self.page.older_messages.isEnabled())
            work, loaded, _ = submit.call_args.args
            loaded(work())
        self.assertEqual(len(self.page._mail_rows), 504)
        self.assertEqual(self.selected_ids(), {row_id})
        self.assertIs(self.page.cached_message, cached)
        self.assertTrue(self.page.older_messages.isEnabled())

    def test_header_refresh_preserves_the_pending_older_page(self):
        self.check_refresh_preserves_paging(self.page._on_headers_ready)

    def test_successful_sync_completion_preserves_the_pending_older_page(self):
        self.check_refresh_preserves_paging(lambda: self.page._on_sync_finished(True))

    def test_failed_sync_completion_preserves_the_pending_older_page(self):
        self.check_refresh_preserves_paging(lambda: self.page._on_sync_finished(False))

    def test_direct_preserving_refresh_keeps_the_pending_older_page(self):
        self.check_refresh_preserves_paging(lambda: self.page._load_mail_async(keep_view=True))

    def test_assignment_refresh_fulfills_the_pending_older_page(self):
        self.load(account_wide=False)
        row_id = self.ids[self.folder, "502"]
        self.select([row_id])
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            old_work, old_loaded, _ = submit.call_args.args
            stale = old_work()
            self.page._assign_selected(1, False)
            self.assertEqual(len(self.page._mail_rows), 502)
            self.assertNotIn(row_id, self.page.assign_map)
            old_loaded(stale)
            self.assertEqual(len(self.page._mail_rows), 502)
            self.assertNotIn(row_id, self.page.assign_map)
        self.assertTrue(self.page.older_messages.isEnabled())

    def test_completion_refresh_fulfills_the_pending_older_page(self):
        self.load()
        row_id = self.ids[self.folder, "502"]
        self.select([row_id])
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            old_work, old_loaded, _ = submit.call_args.args
            stale = old_work()
            self.page._set_selected_completed(False)
            self.assertEqual(len(self.page._mail_rows), 503)
            self.assertNotIn(row_id, self.items())
            old_loaded(stale)
            self.assertEqual(len(self.page._mail_rows), 503)
            self.assertNotIn(row_id, self.items())
        self.assertTrue(self.page.older_messages.isEnabled())

    def test_repeated_refreshes_advance_the_requested_page_once(self):
        storage.save_message_headers(1, self.folder, [
            {"uid": str(uid), "message_id": f"<older-{uid}@review>", "date": "2026-09-01 10:00"}
            for uid in range(503, 1003)
        ])
        # Use the folder view so the added rows need no label/completion state.
        self.load(account_wide=False)
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            first_work, first_loaded, _ = submit.call_args.args
            first = first_work()
            self.page._on_headers_ready()
            second_work, second_loaded, _ = submit.call_args.args
            second = second_work()
            self.page._on_sync_finished(True)
            first_loaded(first)
            second_loaded(second)
            work, loaded, _ = submit.call_args.args
            loaded(work())
            self.assertEqual(len(self.page._mail_rows), 1000)
            self.assertFalse(self.page.older_messages.isHidden())
            # Once the click is fulfilled, later refreshes must not request an
            # extra page on the user's behalf.
            self.page._on_headers_ready()
            work, loaded, _ = submit.call_args.args
            loaded(work())
            self.assertEqual(len(self.page._mail_rows), 1000)
            self.page._load_mail_async(more=True)
            work, loaded, _ = submit.call_args.args
            loaded(work())
        self.assertEqual(len(self.page._mail_rows), 1002)
        self.assertTrue(self.page.older_messages.isHidden())

    def test_refresh_does_not_carry_paging_into_a_different_list_context(self):
        self.load()
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            old_work, old_loaded, _ = submit.call_args.args
            stale = old_work()
            self.page.completed_filter = False
            self.page._on_headers_ready()
            self.assertFalse(self.page._pending_mail_load[1])
            old_loaded(stale)
            work, loaded, _ = submit.call_args.args
            loaded(work())
        self.assertEqual(len(self.page._mail_rows), 500)
        self.assertEqual(self.page._mail_context, self.page._mail_request())

    def test_explicit_nonpreserving_reload_cancels_pending_paging(self):
        self.load()
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            self.page._load_mail_async()
            self.assertFalse(self.page._pending_mail_load[1])
            work, loaded, _ = submit.call_args.args
            loaded(work())
        self.assertEqual(len(self.page._mail_rows), 500)

    def test_real_worker_refresh_keeps_the_older_page_and_reloads_new_headers(self):
        self.load(account_wide=False)
        captured, release = threading.Event(), threading.Event()
        original = self.page._fetch_mail_data
        counter_lock, calls = threading.Lock(), []

        def fetch(*args, **kwargs):
            with counter_lock:
                first = not calls
                calls.append(True)
            payload = original(*args, **kwargs)
            if first:
                captured.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release the superseded older-page result")
            return payload

        with mock.patch.object(self.page, "_fetch_mail_data", side_effect=fetch):
            try:
                self.page._load_mail_async(more=True)
                self.assertTrue(wait_until(captured.is_set))
                storage.save_message_headers(1, self.folder, [{
                    "uid": "503", "message_id": "<incoming@review>", "date": "2026-11-02 10:00",
                }])
                self.page._on_sync_finished(True)
                self.assertTrue(wait_until(lambda: len(self.page._mail_rows) == 503 and self.page.older_messages.isEnabled()))
            finally:
                release.set()
            self.assertTrue(wait_until(lambda: not self.page._list_jobs._jobs))
        self.assertEqual(len(self.page._mail_rows), 503)
        self.assertIn("503", {row["uid"] for row in self.page._mail_rows})

    def test_fresh_reload_clears_the_previous_cursor_and_paging_controls_immediately(self):
        self.load(account_wide=False)
        self.assertIsNotNone(self.page._mail_cursor)
        with mock.patch.object(self.page._list_jobs, "submit"):
            self.page._load_mail_async()
        self.assertEqual(self.page._mail_rows, [])
        self.assertIsNone(self.page._mail_cursor)
        self.assertIsNone(self.page._mail_context)
        self.assertTrue(self.page.older_messages.isHidden())

    def test_failed_fresh_reload_retries_from_the_top_and_includes_recent_messages(self):
        self.load(account_wide=False)
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async()
            _, _, failed = submit.call_args.args
            failed(RuntimeError("reload failed"))
            storage.save_message_headers(1, self.folder, [{
                "uid": "503", "message_id": "<recent@review>", "date": "2026-11-02 10:00",
            }])
            self.page._load_mail_async(more=True)
            work, loaded, _ = submit.call_args.args
            loaded(work())
        self.assertEqual(len(self.page._mail_rows), 500)
        self.assertIn("503", {row["uid"] for row in self.page._mail_rows})

    def test_failed_preserving_refresh_retains_its_valid_range_for_retry(self):
        self.load(account_wide=False)
        previous = {row["id"] for row in self.page._mail_rows}
        cursor, context = self.page._mail_cursor, self.page._mail_context
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(keep_view=True)
            _, _, failed = submit.call_args.args
            failed(RuntimeError("refresh failed"))
            self.assertEqual(self.page._mail_cursor, cursor)
            self.assertEqual(self.page._mail_context, context)
            self.assertEqual({row["id"] for row in self.page._mail_rows}, previous)
            storage.save_message_headers(1, self.folder, [{
                "uid": "503", "message_id": "<recent@review>", "date": "2026-11-02 10:00",
            }])
            self.page._load_mail_async(more=True)
            work, loaded, _ = submit.call_args.args
            loaded(work())
        self.assertEqual(len(self.page._mail_rows), 503)
        self.assertIn("503", {row["uid"] for row in self.page._mail_rows})

    def test_late_page_completion_cannot_restore_state_after_a_failed_fresh_reload(self):
        self.load(account_wide=False)
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            self.page._load_mail_async(more=True)
            old_work, old_loaded, _ = submit.call_args.args
            old = old_work()
            self.page._load_mail_async()
            _, _, failed = submit.call_args.args
            failed(RuntimeError("fresh reload failed"))
            old_loaded(old)
        self.assertEqual(self.page._mail_rows, [])
        self.assertIsNone(self.page._mail_cursor)
        self.assertIsNone(self.page._mail_context)
        self.assertTrue(self.page.older_messages.isHidden())

    def check_failed_scope_reload(self, *, account=False):
        self.load(account_wide=False)
        original_account = self.page.current_account
        with mock.patch.object(self.page._list_jobs, "submit") as submit:
            if account:
                self.page.current_account = {"id": 2, "email": "second@example.com"}
            else:
                self.page.current_folder_local = "Archive"
            self.page._load_mail_async()
            _, _, failed = submit.call_args.args
            failed(RuntimeError("new scope failed"))
            self.page.current_account = original_account
            self.page.current_folder_local = "INBOX"
            self.page._load_mail_async(more=True)
            work, loaded, _ = submit.call_args.args
            loaded(work())
        self.assertEqual(len(self.page._mail_rows), 500)
        self.assertIn("502", {row["uid"] for row in self.page._mail_rows})

    def test_failed_folder_reload_cannot_reuse_the_previous_folders_cursor(self):
        self.check_failed_scope_reload()

    def test_failed_account_reload_cannot_reuse_the_previous_accounts_cursor(self):
        self.check_failed_scope_reload(account=True)
