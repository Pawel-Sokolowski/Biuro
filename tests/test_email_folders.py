import os
import json
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTextBrowser

from app.email import email_ui
from app.email import email_cache
from app.email.email_helpers import (
    decode_mailbox_utf7,
    encode_mailbox_utf7,
    folder_role,
    format_mail_date,
    parse_list_response,
    quote_mailbox,
)
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(["biuro-tests"])


class MailboxNameTests(unittest.TestCase):
    def test_utf7_roundtrip(self):
        for text in ("Łącka-Zabłocka Olga", "Urząd", "A & B", "Sokołowski Paweł"):
            self.assertEqual(decode_mailbox_utf7(encode_mailbox_utf7(text)), text)

    def test_decode_server_names(self):
        self.assertEqual(
            decode_mailbox_utf7("&AUEBBQ-cka-Zab&AUI-ocka Olga"), "Łącka-Zabłocka Olga"
        )
        self.assertEqual(decode_mailbox_utf7("Urz&AQU-d"), "Urząd")

    def test_parse_list_response(self):
        info = parse_list_response(b'(\\HasChildren \\Sent) "." "INBOX.Sent"')
        self.assertEqual(info["name"], "INBOX.Sent")
        self.assertEqual(info["delimiter"], ".")
        self.assertIn("\\Sent", info["flags"])
        slash = parse_list_response(b'(\\HasNoChildren) "/" INBOX/3 Pracownicy/Sokolowski')
        self.assertEqual(slash["delimiter"], "/")
        self.assertEqual(slash["name"], "INBOX/3 Pracownicy/Sokolowski")

    def test_parse_list_literal(self):
        info = parse_list_response((b'(\\HasNoChildren) "." {9}', b"INBOX.a b"))
        self.assertEqual(info["name"], "INBOX.a b")

    def test_quote_and_role(self):
        self.assertEqual(quote_mailbox('a "b"'), '"a \\"b\\""')
        self.assertEqual(folder_role("INBOX"), "inbox")
        self.assertEqual(folder_role("INBOX.spam", "", "."), "junk")
        self.assertEqual(folder_role("X.Foo", "\\Trash", "."), "trash")
        self.assertIsNone(folder_role("INBOX.3 Pracownicy", "", "."))

    def test_date_is_sortable(self):
        self.assertEqual(
            format_mail_date("Mon, 02 Jan 2023 10:00:00 +0000")[:10], "2023-01-02"
        )


class FolderTreeTests(DbTestCase):
    def test_tree_uses_server_delimiter(self):
        from app.email.email_ui import build_folder_tree

        folders = [
            {"server_name": n, "delimiter": ".", "attributes": a}
            for n, a in [
                ("INBOX", ""),
                ("INBOX.Sent", "\\Sent"),
                ("INBOX.3 Pracownicy", ""),
                ("INBOX.3 Pracownicy.Sokołowski Paweł".replace("ł", "&AUI-"), ""),
                ("INBOX.10 Inne", ""),
                ("INBOX.2 Dostawcy", ""),
            ]
        ]
        roots = build_folder_tree(folders)
        self.assertEqual([r["path"] for r in roots], ["INBOX"])
        children = roots[0]["children"]
        self.assertEqual(children[0]["role"], "sent")
        labels = [c["label"] for c in children[1:]]
        self.assertEqual(labels, ["2 Dostawcy", "3 Pracownicy", "10 Inne"])
        employees = children[2]["children"]
        self.assertEqual(employees[0]["label"], "Sokołowski Paweł")

    def test_save_folder_updates_metadata_and_special_lookup(self):
        from app.db.dao_conn import get_conn
        from app.email.email_storage import find_special_folder, get_folders_for_account, save_folder

        conn = get_conn()
        conn.execute("INSERT INTO konta_email (id, email) VALUES (1, 'a@b.pl')")
        conn.commit()
        conn.close()
        save_folder(1, "INBOX.Arch", "Arch")
        self.assertIsNone(find_special_folder(1, "archive"))
        save_folder(1, "INBOX.Arch", "Arch", ".", "\\Archive")
        save_folder(1, "INBOX.Kosz", "Kosz", ".", "")
        self.assertEqual(len(get_folders_for_account(1)), 2)
        self.assertEqual(find_special_folder(1, "archive"), "INBOX.Arch")
        self.assertEqual(find_special_folder(1, "trash"), "INBOX.Kosz")


class CacheTests(DbTestCase):
    def test_cache_keeps_last_limit(self):
        with mock.patch("app.email.email_cache.get_data_dir", return_value=self._tmp):
            for i in range(5):
                email_cache.write_message(1, "INBOX", str(i), b"x", limit=3)
                os.utime(email_cache._path(1, "INBOX", str(i)), (1000 + i, 1000 + i))
                email_cache.prune(1, 3)
            self.assertFalse(email_cache.has_message(1, "INBOX", "0"))
            self.assertTrue(email_cache.has_message(1, "INBOX", "4"))
            self.assertEqual(email_cache.read_message(1, "INBOX", "4"), b"x")


class EmailPageTests(DbTestCase):
    def test_folder_visibility_is_saved_and_applied_to_tree(self):
        from app.db.dao_conn import get_conn
        from app.email.email_storage import save_folder

        conn = get_conn()
        conn.execute("INSERT INTO konta_email (id, email) VALUES (1, 'a@b.pl')")
        conn.commit()
        conn.close()
        save_folder(1, "INBOX", "INBOX", ".", "")
        save_folder(1, "INBOX.Sent", "Sent", ".", "\\Sent")
        stored = {}
        with mock.patch.object(
            email_ui, "get_email_accounts_for_employee",
            return_value=[{"id": 1, "email": "a@b.pl"}],
        ), mock.patch.object(email_ui.EmailPage, "start_background_sync"), \
                mock.patch.object(email_ui, "MailBrowser", QTextBrowser), \
                mock.patch.object(email_ui, "get_setting", return_value="{}"), \
                mock.patch.object(
                    email_ui, "set_setting", side_effect=lambda key, value: stored.update({key: value})
                ):
            page = email_ui.EmailPage(5)

            account = page.folder_tree.topLevelItem(0)
            self.assertEqual(account.childCount(), 2)
            page._set_folder_hidden(1, "INBOX.Sent", True)
            account = page.folder_tree.topLevelItem(0)
            self.assertEqual(account.childCount(), 1)
            self.assertEqual(
                json.loads(stored["email_ukryte_foldery_5"]), {"1": ["INBOX.Sent"]}
            )

            page.show_hidden_button.setChecked(True)
            account = page.folder_tree.topLevelItem(0)
            self.assertEqual(account.childCount(), 2)
            self.assertIn("(ukryty)", account.child(1).text(0))
            page.close()

    def test_page_builds_tree_and_renders_cached_message(self):
        from app.db.dao_conn import get_conn
        from app.email import email_ui
        from app.email.email_storage import get_folder_id, save_folder, save_message_header

        conn = get_conn()
        conn.execute("INSERT INTO konta_email (id, email) VALUES (1, 'a@b.pl')")
        conn.commit()
        conn.close()
        save_folder(1, "INBOX", "INBOX", ".", "")
        save_folder(1, "INBOX.Sent", "Sent", ".", "\\Sent")
        fid = get_folder_id(1, "INBOX")
        save_message_header(1, fid, "7", None, "x@y.pl", "a@b.pl", "Temat", "2024-01-01 10:00", False, False)

        raw = b"From: x@y.pl\r\nTo: a@b.pl\r\nSubject: Temat\r\n\r\nTresc listu"
        with mock.patch.object(email_ui, "get_email_accounts_for_employee", return_value=[{"id": 1, "email": "a@b.pl"}]), \
                mock.patch.object(email_ui, "sync_and_prefetch", return_value=0), \
                mock.patch("app.email.email_cache.get_data_dir", return_value=self._tmp), \
                mock.patch("app.email.email_sync.mark_read_remote", return_value=True), \
                mock.patch.object(email_ui, "mark_read_remote", return_value=True):
            email_cache.write_message(1, "INBOX", "7", raw)
            page = email_ui.EmailPage(1)
            account = page.folder_tree.topLevelItem(0)
            self.assertEqual(account.text(0), "a@b.pl")
            top = account.child(0)
            self.assertEqual(top.text(0), "Odebrane (1)")
            self.assertEqual(top.childCount(), 0)
            self.assertEqual(account.child(1).text(0), "Wysłane")
            page.folder_tree.setCurrentItem(top)  # pojedyncze kliknięcie wystarcza
            self.assertEqual(page.mail_list.topLevelItemCount(), 1)
            item = page.mail_list.topLevelItem(0)
            self.assertTrue(item.font(0).bold())
            self.assertEqual(item.text(2), "01.01.2024, 10:00")
            page.mail_list.setCurrentItem(item)
            page._open_selected_now()
            from PyQt6.QtCore import QEventLoop, QTimer
            text = ""
            for _ in range(50):
                result = []
                loop = QEventLoop()
                page.mail_view.page().toPlainText(lambda t: (result.append(t), loop.quit()))
                QTimer.singleShot(2000, loop.quit)
                loop.exec()
                text = result[0] if result else ""
                if "Tresc listu" in text:
                    break
                wait = QEventLoop()
                QTimer.singleShot(200, wait.quit)
                wait.exec()
            self.assertIn("Tresc listu", text)
            self.assertFalse(page.mail_list.topLevelItem(0).font(0).bold())
            self.assertFalse(hasattr(page, "btn_download_message"))

    def test_attachments_bar_inline_image_and_multi_save(self):
        import base64
        import tempfile
        from email.message import EmailMessage
        from app.email import email_ui
        from app.email.email_helpers import parse_message

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        m = EmailMessage()
        m["Subject"] = "S"
        m.set_content("tekst")
        m.add_alternative('<p>Hej <img src="cid:img1"></p>', subtype="html")
        m.get_payload()[1].add_related(png, "image", "png", cid="<img1>")
        m.add_attachment(b"aaa", maintype="application", subtype="pdf", filename="a.pdf")
        m.add_attachment(b"bbb", maintype="application", subtype="pdf", filename="a.pdf")
        msg = parse_message(m.as_bytes())

        with mock.patch.object(email_ui, "get_email_accounts_for_employee", return_value=[]), \
                mock.patch.object(email_ui.QMessageBox, "warning"), \
                mock.patch.object(email_ui.QMessageBox, "information"):
            page = email_ui.EmailPage(1)
            page._render_message(msg)
            self.assertFalse(page.att_bar.isHidden())
            self.assertEqual(page.att_list.count(), 2)  # obrazek cid nie jest załącznikiem
            self.assertIn("img1", page.mail_view.resources)
            rendered = email_ui.MailBrowser.prepare_html(
                '<body><img src="cid:img1"></body>', page.mail_view.resources
            )
            self.assertIn("data:image/png;base64,", rendered)
            with tempfile.TemporaryDirectory() as d, \
                    mock.patch.object(email_ui.QFileDialog, "getExistingDirectory", return_value=d):
                page.on_save_attachments_clicked()
                self.assertEqual(sorted(os.listdir(d)), ["a (1).pdf", "a.pdf"])
            page._clear_view()
            self.assertTrue(page.att_bar.isHidden())

    def test_labels_and_batched_headers(self):
        from app.email.email_ui import clean_folder_label, employee_label
        from app.email import email_sync

        self.assertEqual(clean_folder_label("3 Pracownicy"), "Pracownicy")
        emps = [{"imie": "Paweł", "nazwisko": "Sokołowski"}]
        self.assertEqual(employee_label(clean_folder_label("EM-KA Sokolowski Paweł"), emps), "Paweł Sokołowski")

        class FakeImap:
            calls = []

            def uid(self, cmd, *args):
                self.calls.append((cmd, args))
                if cmd == "SEARCH":
                    return "OK", [b"1 2"]
                h1 = b"From: a@b.pl\r\nSubject: Jeden\r\nDate: Thu, 15 Oct 2026 08:00:00 +0200\r\n\r\n"
                h2 = b"From: c@d.pl\r\nSubject: Dwa\r\n\r\n"
                return "OK", [(b"1 (UID 1 FLAGS (\\Seen) BODY[HEADER.FIELDS (FROM)] {5}", h1), b")",
                              (b"2 (UID 2 BODY[HEADER.FIELDS (FROM)] {5}", h2), b" FLAGS ())"]

            def logout(self):
                pass

        with mock.patch.object(email_sync, "_get_account_info", return_value={"id": 1}), \
                mock.patch.object(email_sync, "_connect_imap", return_value=FakeImap()), \
                mock.patch.object(email_sync, "_select_folder", return_value=True):
            res = email_sync.fetch_headers(1, "INBOX")
        self.assertEqual([r["uid"] for r in res], ["1", "2"])
        self.assertEqual([r["is_seen"] for r in res], [True, False])
        self.assertEqual(len([c for c in FakeImap.calls if c[0] == "FETCH"]), 1)


class ComposeDialogTests(unittest.TestCase):
    def test_body_editable_and_prefilled(self):
        from app.email.compose_dialog import ComposeDialog

        dlg = ComposeDialog(to="a@b.pl", subject="Re: x", body="\n\n> cytat")
        self.assertFalse(dlg.body_edit.isReadOnly())
        dlg.body_edit.setPlainText("Odpowiedź\n\n> cytat")
        vals = dlg.values()
        self.assertEqual(vals["to"], ["a@b.pl"])
        self.assertTrue(vals["body"].startswith("Odpowiedź"))

    def test_reply_quote_and_bcc(self):
        import email as _email
        from app.email.compose_dialog import ComposeDialog
        from app.email.email_ui import EmailPage

        msg = _email.message_from_string(
            "From: Quest <q@q.com>\nDate: Thu, 15 Oct 2026 08:00:00 +0200\nSubject: X\n\nHej"
        )
        html = EmailPage._quote_html(msg, "reply")
        self.assertIn("W dniu Thu, 15 Oct 2026 08:00:00 +0200, Quest &lt;q@q.com&gt; pisze:", html)
        dlg = ComposeDialog(to="a@b.pl", html=html)
        dlg.bcc_edit.setText("c@d.pl")
        vals = dlg.values()
        self.assertEqual(vals["bcc"], ["c@d.pl"])
        self.assertIn("Hej", vals["body"])


if __name__ == "__main__":
    unittest.main()
