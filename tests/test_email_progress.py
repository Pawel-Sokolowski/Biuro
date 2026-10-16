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

_app = QApplication.instance() or QApplication([])


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
        with mock.patch.object(email_sync, "sync_folder", lambda k, f: synced.append(f) or 0):
            email_sync.sync_and_prefetch(self.konto, ["INBOX.Sent", "INBOX"])
        self.assertEqual(synced, ["INBOX.Sent", "INBOX"])

    def test_prepopulated_db_skips_progress(self):
        self.fake_list(self.konto)
        from app.email.email_storage import save_message_headers
        save_message_headers(self.konto, get_folder_id(self.konto, "INBOX"), [header(1)])
        self.assertFalse(email_sync.needs_initial_sync(self.konto))


if __name__ == "__main__":
    unittest.main()
