import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.base import DbTestCase
from app.chat import chat_model as cm
from app.chat.chat_notifications import ChatNotifier


class ChatAccessTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_employee("o@x.pl", "Olga", "Owner")
        self.a = self.make_employee("a@x.pl", "Adam", "Alfa")
        self.b = self.make_employee("b@x.pl", "Beata", "Beta")
        self.inactive = self.make_employee("i@x.pl", "Ignacy", "Nieaktywny")
        from app.db.dao_conn import get_conn
        conn = get_conn()
        conn.execute("UPDATE pracownicy SET aktywny = 0 WHERE id = %s", (self.inactive,))
        conn.commit()
        conn.close()
        self.admin = cm_admin_id()

    def test_creator_auto_added_and_visibility(self):
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        self.assertCountEqual(cm.get_participant_ids(cid), [self.owner, self.a])
        self.assertEqual([c["id"] for c in cm.get_conversations(self.a)], [cid])
        self.assertEqual(cm.get_conversations(self.b), [])

    def test_active_employees_only(self):
        ids = [e["id"] for e in cm.get_active_employees()]
        self.assertNotIn(self.inactive, ids)
        self.assertIn(self.a, ids)

    def test_only_participants_can_send(self):
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        cm.add_message(cid, self.a, "cześć")
        with self.assertRaises(PermissionError):
            cm.add_message(cid, self.b, "hej")
        self.assertEqual(len(cm.get_messages(cid)), 1)

    def test_attachments_roundtrip_and_access(self):
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        mid = cm.add_message_with_attachments(
            cid, self.a, "", [{"nazwa": "zażółć.txt", "dane": b"abc\x00\xff"}])
        atts = cm.get_attachments([mid])[mid]
        self.assertEqual((atts[0]["nazwa"], atts[0]["rozmiar"]), ("zażółć.txt", 5))
        got = cm.get_attachment_data(atts[0]["id"], self.owner)
        self.assertEqual(got["dane"], b"abc\x00\xff")
        with self.assertRaises(PermissionError):
            cm.get_attachment_data(atts[0]["id"], self.b)
        with self.assertRaises(PermissionError):
            cm.add_message_with_attachments(cid, self.b, "x", [])
        with self.assertRaises(ValueError):
            cm.add_message_with_attachments(
                cid, self.a, "", [{"nazwa": "x", "dane": b"0" * (cm.MAX_ATTACHMENT_BYTES + 1)}])

    def test_manage_permissions_and_update(self):
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        self.assertTrue(cm.can_manage_conversation(cid, self.owner))
        self.assertTrue(cm.can_manage_conversation(cid, self.admin))
        self.assertFalse(cm.can_manage_conversation(cid, self.a))
        with self.assertRaises(PermissionError):
            cm.update_conversation(cid, "X", [self.b], self.a)
        cm.update_conversation(cid, "Nowa", [self.b], self.admin)
        self.assertCountEqual(cm.get_participant_ids(cid), [self.owner, self.b])
        self.assertEqual(cm.get_conversation(cid)["nazwa"], "Nowa")
        self.assertFalse(cm.is_participant(cid, self.a))

    def test_delete_channel_and_clear_direct(self):
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        cm.add_message(cid, self.owner, "x")
        with self.assertRaises(PermissionError):
            cm.delete_conversation(cid, self.a)
        self.assertEqual(cm.delete_conversation(cid, self.owner), "deleted")
        self.assertIsNone(cm.get_conversation(cid))
        cm.ensure_direct_conversations(self.owner)
        direct = cm.get_direct_conversations(self.owner)[0]["id"]
        cm.add_message(direct, self.owner, "hej")
        with self.assertRaises(PermissionError):
            cm.delete_conversation(direct, 999999)
        self.assertEqual(cm.delete_conversation(direct, self.owner), "cleared")
        self.assertEqual(cm.get_messages(direct), [])
        self.assertIsNotNone(cm.get_conversation(direct))

    def test_notifier_only_for_participants(self):
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        cm.add_message(cid, self.owner, "x")
        got = []
        n_ok = ChatNotifier(cid, self.a)
        n_ok.new_message.connect(got.append)
        n_ok.check_updates()
        n_no = ChatNotifier(cid, self.b)
        n_no.new_message.connect(got.append)
        n_no.check_updates()
        self.assertEqual(got, [cid])

    def test_ui_smoke(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import Qt
        from app.chat.chat_ui import ChatPage
        _app = QApplication.instance() or QApplication(["biuro-tests"])
        cid = cm.create_conversation("Kanał", self.owner, [self.a])

        def select(page, konw_id):
            lst = page.chat_conversations
            for i in range(lst.count()):
                if lst.item(i).data(Qt.ItemDataRole.UserRole) == konw_id:
                    lst.setCurrentRow(i)
                    return
            self.fail("brak konwersacji na liście")

        def ids(page):
            lst = page.chat_conversations
            return [lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.count())
                    if lst.item(i).data(Qt.ItemDataRole.UserRole) is not None]

        page = ChatPage(self.a)
        select(page, cid)
        self.assertFalse(page.btn_manage.isEnabled())
        page_owner = ChatPage(self.owner)
        select(page_owner, cid)
        self.assertTrue(page_owner.btn_manage.isEnabled())
        page_b = ChatPage(self.b)
        self.assertNotIn(cid, ids(page_b))

    def test_direct_conversations_created_automatically(self):
        directs = cm.get_direct_conversations(self.a)
        names = sorted(d["nazwa"] for d in directs)
        self.assertEqual(len(directs), 3)  # owner, b, admin; bez siebie i nieaktywnego
        self.assertNotIn("Ignacy Nieaktywny", names)
        self.assertIn("Olga Owner", names)
        again = cm.get_direct_conversations(self.a)
        self.assertEqual([d["id"] for d in directs], [d["id"] for d in again])
        owner_view = {d["partner_id"]: d["id"] for d in cm.get_direct_conversations(self.owner)}
        a_view = {d["partner_id"]: d["id"] for d in directs}
        self.assertEqual(owner_view[self.a], a_view[self.owner])
        self.assertTrue(cm.is_participant(a_view[self.owner], self.a))
        self.assertFalse(cm.is_participant(a_view[self.owner], self.b))
        self.assertFalse(cm.can_manage_conversation(a_view[self.owner], self.owner))

    def _chat_page(self, cid, user):
        from unittest import mock
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from app.chat.chat_ui import ChatPage
        _app = QApplication.instance() or QApplication(["biuro-tests"])
        for name in ("warning", "critical", "information"):
            patcher = mock.patch.object(QMessageBox, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        page = ChatPage(user)
        self._pages = getattr(self, "_pages", []) + [page]
        self.addCleanup(page._poll_timer.stop)
        page.current_konwersacja = cid
        return page

    def test_ui_sends_attachment_without_text_and_clears_selection(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from PyQt6.QtWidgets import QFileDialog
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        page = self._chat_page(cid, self.a)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raport.pdf"
            path.write_bytes(b"%PDF-1")
            with mock.patch.object(QFileDialog, "getOpenFileNames", return_value=([str(path)], "")):
                page.attach_files()
            self.assertFalse(page.pending_label.isHidden())
            self.assertIn("raport.pdf", page.pending_label.text())
            page.send_message()
        self.assertTrue(page.pending_label.isHidden())
        messages = cm.get_messages(cid)
        self.assertEqual(len(messages), 1)
        atts = cm.get_attachments([messages[0]["id"]])[messages[0]["id"]]
        self.assertEqual(atts[0]["nazwa"], "raport.pdf")

    def test_ui_attach_click_again_clears_selection(self):
        from unittest import mock
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        page = self._chat_page(cid, self.a)
        page._set_pending_files(["C:\\x\\a.txt"])
        with mock.patch("app.chat.chat_ui.QFileDialog.getOpenFileNames") as dialog:
            page.attach_files()
        dialog.assert_not_called()
        self.assertEqual(page._pending_files, [])
        self.assertTrue(page.pending_label.isHidden())

    def test_ui_does_not_send_empty_message_and_rejects_too_big_file(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from PyQt6.QtWidgets import QFileDialog
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        page = self._chat_page(cid, self.a)
        page.send_message()
        self.assertEqual(cm.get_messages(cid), [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duzy.bin"
            path.write_bytes(b"1")
            with mock.patch.object(QFileDialog, "getOpenFileNames", return_value=([str(path)], "")), \
                    mock.patch("app.chat.chat_ui.os.path.getsize", return_value=cm.MAX_ATTACHMENT_BYTES + 1):
                page.attach_files()
        self.assertEqual(page._pending_files, [])

    def test_ui_download_saves_file_and_checks_access(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from PyQt6.QtWidgets import QFileDialog
        cid = cm.create_conversation("Kanał", self.owner, [self.a])
        mid = cm.add_message_with_attachments(cid, self.a, "", [{"nazwa": "a.txt", "dane": b"tresc"}])
        att = cm.get_attachments([mid])[mid][0]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "zapis.txt"
            page = self._chat_page(cid, self.owner)
            with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
                page.download_attachment(att)
            self.assertEqual(target.read_bytes(), b"tresc")
            stranger = self._chat_page(cid, self.b)
            other = Path(directory) / "obcy.txt"
            with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(other), "")):
                stranger.download_attachment(att)
            self.assertFalse(other.exists())

    def test_format_size(self):
        from app.chat.chat_ui import _format_size
        self.assertIn("KB", _format_size(2048).upper())


def cm_admin_id():
    from app.db.dao_conn import get_conn
    conn = get_conn()
    try:
        return conn.execute("SELECT id FROM pracownicy WHERE email = 'admin@biuro.pl'").fetchone()[0]
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()
