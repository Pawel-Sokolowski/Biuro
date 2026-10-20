import unittest
from tests.base import DbTestCase
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QImage

app = QApplication.instance() or QApplication([])


class LabelsAndImagesTests(DbTestCase):
    def test_label_map_validation_and_defaults(self):
        from app.email import email_labels
        emps = [{"id": 5}, {"id": 2}]
        self.assertEqual(email_labels.resolve_label_numbers(emps), {5: 1, 2: 2})
        many = [{"id": employee_id} for employee_id in range(1, 8)]
        self.assertEqual(
            email_labels.resolve_label_numbers(many),
            {1: 1, 2: 2, 3: 4, 4: 5, 5: 6, 6: 7},
        )
        email_labels.save_label_map({5: 4, 2: 1})
        self.assertEqual(email_labels.resolve_label_numbers(emps), {5: 4, 2: 1})
        with self.assertRaises(ValueError):
            email_labels.save_label_map({5: 1, 2: 1})
        with self.assertRaises(ValueError):
            email_labels.save_label_map({5: 3})

    def test_completed_status_follows_message_after_move(self):
        from app.email.email_storage import (
            count_completed_messages,
            get_completed_map,
            list_completed_rows,
            save_folder,
            set_messages_completed,
        )
        from app.db.dao_conn import get_conn

        conn = get_conn()
        conn.execute("INSERT INTO konta_email (id, email) VALUES (1, 'a@b.pl')")
        conn.commit()
        conn.close()
        inbox_id = save_folder(1, "INBOX", "INBOX", ".", "")
        archive_id = save_folder(1, "INBOX/Archive", "Archive", ".", "")

        conn = get_conn()
        conn.execute(
            "INSERT INTO wiadomosci_email "
            "(konto_id, folder_id, uid, message_id, temat) VALUES "
            "(1, %s, '1', '<same@x>', 'Temat'), "
            "(1, %s, '2', '<same@x>', 'Temat')",
            (inbox_id, archive_id),
        )
        conn.commit()
        row_ids = [row[0] for row in conn.execute(
            "SELECT id FROM wiadomosci_email ORDER BY id"
        ).fetchall()]
        conn.close()

        set_messages_completed([row_ids[0]], True)
        self.assertEqual(get_completed_map(row_ids), set(row_ids))
        self.assertEqual(count_completed_messages(1), 2)
        self.assertEqual(len(list_completed_rows(1)), 2)

        set_messages_completed([row_ids[0]], False)
        self.assertEqual(get_completed_map(row_ids), set())

    def test_browser_resolves_cid_and_data(self):
        import base64
        from PyQt6.QtCore import QBuffer, QIODevice
        from app.email.email_ui import MailBrowser
        img = QImage(4, 4, QImage.Format.Format_RGB32)
        img.fill(0xff0000)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        raw = bytes(buf.data())
        b = MailBrowser()
        b.resources = {"Logo@x": (raw, "image/png")}
        self.assertIsNotNone(b.loadResource(2, QUrl("cid:logo@x")))
        data = "data:image/png;base64," + base64.b64encode(raw).decode()
        self.assertIsNotNone(b.loadResource(2, QUrl(data)))
        self.assertIsNone(b.loadResource(2, QUrl("cid:missing")))

class ThreadGroupingTests(unittest.TestCase):
    def test_threads_only_for_real_replies(self):
        from app.email.email_ui import group_threads
        entries = [
            ("reply2", "<c@x>", "<a@x> <b@x>"),
            ("reply1", "<b@x>", "<a@x>"),
            ("same-subject-unrelated", "<d@x>", ""),
            ("root", "<a@x>", ""),
            ("orphan-reply", "<e@x>", "<gone@x>"),
        ]
        groups = group_threads(entries)
        names = sorted(sorted(i for i in g) for g in groups)
        self.assertEqual(names, [["orphan-reply"], ["reply1", "reply2", "root"], ["same-subject-unrelated"]])


class SentCopyTests(DbTestCase):
    def test_copy_goes_to_rule_folder_else_sent_and_threading_headers(self):
        from unittest import mock
        from app.email import email_actions
        from app.email.email_storage import save_folder

        from app.db.dao_conn import get_conn
        conn = get_conn()
        conn.execute("INSERT INTO konta_email (id, email) VALUES (1, 'a@b.pl')")
        conn.commit()
        save_folder(1, "INBOX", "INBOX", ".", "")
        save_folder(1, "INBOX/Klient", "Klient", ".", "")
        conn.execute(
            "INSERT INTO reguly_filtrow (konto_id, od, \"do\", folder_docelowy, aktywna, usun) "
            "VALUES (1, NULL, 'klient@x.pl', 'INBOX/Klient', 1, 0)"
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            email_actions.choose_copy_folder(1, "biuro@b.pl", ["klient@x.pl"], "T"), "INBOX/Klient")
        self.assertIsNone(email_actions.choose_copy_folder(1, "biuro@b.pl", ["inny@x.pl"], "T"))

        msg = email_actions.prepare_message(
            "biuro@b.pl", "Biuro", ["klient@x.pl"], "Re: T", "x",
            in_reply_to="<a@x>", references="<r@x>")
        self.assertEqual(msg["In-Reply-To"], "<a@x>")
        self.assertEqual(msg["References"], "<r@x>  <a@x>".replace("  ", " "))
        with mock.patch("app.email.email_sync.append_message", return_value=True) as app, \
                mock.patch("app.email.email_sync.sync_folder") as sync:
            email_actions.store_sent_copy(1, msg, "INBOX/Klient")
        app.assert_called_once()
        self.assertEqual(app.call_args[0][1], "INBOX/Klient")
        sync.assert_called_once_with(1, "INBOX/Klient")
