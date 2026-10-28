import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.db.dao_employees import add_email_account_for_employee
from app.email import email_sync
from app.email.email_storage import (
    account_has_headers, get_cached_messages, get_folder_id, save_folder,
)
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(["biuro-tests"])


def header(uid):
    return {
        "uid": str(uid), "message_id": f"<{uid}@x>", "sender": "a@b.pl", "recipient": "c@d.pl",
        "subject": f"Temat {uid}", "date": "2026-01-01 10:00:00", "is_seen": False,
        "has_attachments": False,
    }


class ProgressTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.konto = add_email_account_for_employee(None, "biuro@x.pl", "imap", 993, "smtp", 465, "x")

    def fake_list(self, konto_id):
        for name, flags in (("INBOX", ""), ("INBOX.Sent", "\\Sent"), ("Dzienne", "\\Noselect"),
                            ("INBOX.Archiwum", "")):
            save_folder(konto_id, name, name, ".", flags)
        return True

    def test_first_sync_detection(self):
        self.assertTrue(email_sync.needs_initial_sync(self.konto))
        save_folder(self.konto, "INBOX", "INBOX", ".", "")
        self.assertTrue(email_sync.needs_initial_sync(self.konto))  # foldery bez nagłówków
        folder_id = get_folder_id(self.konto, "INBOX")
        from app.email.email_storage import save_message_headers
        save_message_headers(self.konto, folder_id, [header(1)])
        self.assertTrue(account_has_headers(self.konto))
        self.assertFalse(email_sync.needs_initial_sync(self.konto))

    def test_all_folders_progress(self):
        calls = []
        with mock.patch.object(email_sync, "list_folders_from_imap_and_store", self.fake_list), \
                mock.patch.object(email_sync, "fetch_headers", lambda k, f, **kw: [header(1), header(2)]):
            total = email_sync.sync_and_prefetch(
                self.konto, ["INBOX"], first_run=True, all_folders=True,
                on_progress=lambda *a: calls.append(a),
            )
        # INBOX + Sent + Archiwum (bez \Noselect)
        self.assertEqual(total, 6)
        self.assertEqual(calls[0], (0, 3, "INBOX", 0))
        self.assertEqual(calls[-1], (3, 3, "", 6))
        self.assertEqual([c[0] for c in calls], [0, 1, 2, 3])
        self.assertFalse(email_sync.needs_initial_sync(self.konto))

    def test_incremental_only_listed_folders(self):
        self.fake_list(self.konto)
        synced = []
        with mock.patch.object(email_sync, "sync_folder", lambda k, f, **kwargs: synced.append(f) or 0):
            email_sync.sync_and_prefetch(self.konto, ["INBOX.Sent", "INBOX"])
        self.assertEqual(synced, ["INBOX.Sent", "INBOX"])

    def test_full_mailbox_only_when_requested(self):
        """Pełna synchronizacja pobiera wszystkie foldery bez ograniczenia daty."""
        history_windows = []

        def fetch(konto_id, folder, **kwargs):
            history_windows.append((folder, kwargs["history_days"]))
            return [header(1), header(2)]

        with mock.patch.object(email_sync, "list_folders_from_imap_and_store", self.fake_list), \
             mock.patch.object(email_sync, "fetch_headers", side_effect=fetch), \
             mock.patch.object(email_sync, "backfill_refs", return_value=0), \
             mock.patch.object(email_sync, "prefetch_messages", return_value=2) as prefetch:
            email_sync.sync_and_prefetch(self.konto, ["INBOX"], full_mailbox=True)

        self.assertEqual(
            {name for name, _ in history_windows},
            {"INBOX", "INBOX.Sent", "INBOX.Archiwum"},
        )
        self.assertTrue(all(days is None for _, days in history_windows))
        self.assertEqual(prefetch.call_count, 3)

    def test_two_year_manual_sync_generates_filters_after_all_folders(self):
        self.fake_list(self.konto)
        from app.email import email_auto_filters, email_rules
        ordered = []
        reports = []

        def sync(konto_id, folder, **kwargs):
            ordered.append(("sync", folder, kwargs["full_sync"]))
            return 0

        def learn(konto_id, days):
            ordered.append(("learn", konto_id, days))
            return {"added": 2, "addresses": 1, "candidates": 1}

        def apply(konto_id, folder, history_days=None):
            ordered.append(("move", folder, history_days))
            return 3

        with (mock.patch.object(email_sync, "sync_folder", side_effect=sync),
              mock.patch.object(email_sync, "get_cached_messages", return_value=[]),
              mock.patch.object(email_auto_filters, "learn_filters_from_mail_history", side_effect=learn),
              mock.patch.object(email_rules, "apply_rules_to_folder", side_effect=apply)):
            email_sync.sync_and_prefetch(
                self.konto, ["INBOX"], all_folders=True, learn_filters=True,
                on_filters=reports.append,
            )

        self.assertTrue(all(item[0] == "sync" for item in ordered[:-2]))
        self.assertEqual(ordered[-2], ("learn", self.konto, 730))
        self.assertEqual(ordered[-1], ("move", "INBOX", 730))
        self.assertEqual(reports, [{
            "added": 2, "addresses": 1, "candidates": 1, "moved": 3,
        }])

    def test_incremental_sync_does_not_learn_filters(self):
        self.fake_list(self.konto)
        from app.email import email_auto_filters
        with (mock.patch.object(email_sync, "sync_folder", return_value=0),
              mock.patch.object(email_sync, "get_cached_messages", return_value=[]),
              mock.patch.object(email_auto_filters, "learn_filters_from_mail_history") as learn):
            email_sync.sync_and_prefetch(self.konto, ["INBOX"], learn_filters=True)
        learn.assert_not_called()

    def test_ordinary_sync_only_recent_messages_and_selected_folder(self):
        self.fake_list(self.konto)
        history_windows = []

        def fetch(konto_id, folder, **kwargs):
            history_windows.append(kwargs["history_days"])
            return [header(uid) for uid in range(1, 26)]

        with mock.patch.object(email_sync, "fetch_headers", side_effect=fetch), \
             mock.patch.object(email_sync, "backfill_refs", return_value=0), \
             mock.patch.object(email_sync, "prefetch_messages", return_value=0) as prefetch:
            email_sync.sync_and_prefetch(self.konto, ["INBOX"])

        self.assertEqual(history_windows, [email_sync.EMAIL_HISTORY_DAYS])
        self.assertEqual(prefetch.call_count, 1)
        self.assertEqual(len(prefetch.call_args.args[2]), email_sync.INCREMENTAL_PREFETCH_LIMIT)

    def test_prepopulated_db_skips_progress(self):
        self.fake_list(self.konto)
        from app.email.email_storage import save_message_headers
        save_message_headers(self.konto, get_folder_id(self.konto, "INBOX"), [header(1)])
        self.assertFalse(email_sync.needs_initial_sync(self.konto))


if __name__ == "__main__":
    unittest.main()
