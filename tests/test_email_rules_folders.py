import unittest
from unittest import mock

from PyQt6.QtWidgets import QApplication
from app.email import email_ui

from tests.base import DbTestCase

_app = QApplication.instance() or QApplication([])

TB_FILTERS = {
    "accountName": "imap://biuro%40x.pl@host",
    "filters": [
        {"filterName": "Spam", "temporary": True, "enabled": True,
         "actionList": [{"type": 1, "targetFolderUri": "imap://biuro%40x.pl@host/INBOX/spam"}],
         "searchTerms": [{"attrib": 53, "op": 9, "value": {"str": "Yes"}, "arbitraryHeader": "x-spam-status"}]},
        {"filterName": "Saldeo AND", "enabled": True,
         "actionList": [{"type": 1, "targetFolderUri": "imap://biuro%40x.pl@host/INBOX/Pir&APM-g Bartosz"}],
         "searchTerms": [
             {"attrib": 1, "value": {"str": "biuro@saldeo.pl"}, "booleanAnd": True},
             {"attrib": 6, "value": {"str": "a@gmail.com"}, "booleanAnd": True}]},
        {"filterName": "Pirog OR", "enabled": True,
         "actionList": [{"type": 1, "targetFolderUri": "imap://biuro%40x.pl@host/INBOX/Pir&APM-g Bartosz"}],
         "searchTerms": [
             {"attrib": 1, "value": {"str": "a@gmail.com"}, "booleanAnd": False},
             {"attrib": 6, "value": {"str": "a@gmail.com"}, "booleanAnd": False}]},
    ],
}


class RulesTests(DbTestCase):
    def setUp(self):
        super().setUp()
        from app.db.dao_conn import get_conn
        from app.email.email_storage import save_folder

        conn = get_conn()
        conn.execute("INSERT INTO konta_email (id, email) VALUES (1, 'a@b.pl')")
        conn.commit()
        conn.close()
        save_folder(1, "INBOX", "INBOX", ".", "")
        save_folder(1, "INBOX.Pir&APM-g Bartosz", "Piróg Bartosz", ".", "")

    def test_parse_and_import(self):
        from app.email.email_rules import import_thunderbird_filters, parse_thunderbird_filters

        rules, skipped = parse_thunderbird_filters(TB_FILTERS, ".")
        self.assertEqual(len(skipped), 1)
        self.assertEqual(len(rules), 3)  # AND = 1 reguła, OR = 2 reguły
        self.assertEqual(rules[0]["folder_docelowy"], "INBOX.Pir&APM-g Bartosz")
        self.assertEqual((rules[0]["nadawca_zawiera"], rules[0]["do"]), ("biuro@saldeo.pl", "a@gmail.com"))
        self.assertEqual(import_thunderbird_filters(1, TB_FILTERS)[:2], (3, 0))
        self.assertEqual(import_thunderbird_filters(1, TB_FILTERS)[:2], (0, 3))

    def test_apply_rules_moves_matching_to_existing_folder(self):
        from app.email import email_rules

        email_rules.import_thunderbird_filters(1, TB_FILTERS)
        headers = [
            {"uid": "1", "sender": "Jan <A@gmail.com>", "recipient": "x@y.pl", "subject": "s", "is_seen": True},
            {"uid": "2", "sender": "ktos@x.pl", "recipient": "x@y.pl", "subject": "s", "is_seen": True},
        ]
        with mock.patch("app.email.email_sync.move_emails") as mv:
            self.assertEqual(email_rules.apply_rules(1, "INBOX", headers), 1)
        mv.assert_called_once_with(1, "INBOX", ["1"], "INBOX.Pir&APM-g Bartosz")

    def test_rename_folder_local_updates_children_and_rules(self):
        from app.email import email_rules
        from app.email.email_storage import get_folders_for_account, rename_folder_local, save_folder

        save_folder(1, "INBOX.Pir&APM-g Bartosz.Sub", "Sub", ".", "")
        email_rules.import_thunderbird_filters(1, TB_FILTERS)
        rename_folder_local(1, "INBOX.Pir&APM-g Bartosz", "INBOX.Nowy")
        names = {f["server_name"] for f in get_folders_for_account(1)}
        self.assertEqual(names, {"INBOX", "INBOX.Nowy", "INBOX.Nowy.Sub"})
        self.assertTrue(all(r["target"] == "INBOX.Nowy" for r in email_rules._active_rules(1)))


class FolderUiTests(RulesTests):
    def make_page(self):
        from app.db.dao_conn import get_conn
        from app.email import email_ui

        conn = get_conn()
        conn.execute("INSERT INTO pracownicy (id, email, haslo_hash, imie, nazwisko, aktywny) VALUES (7, 'p@b.pl', 'x', 'Pan', 'Test', 1)")
        conn.commit()
        conn.close()
        with mock.patch.object(email_ui, "get_email_accounts_for_employee", return_value=[{"id": 1, "email": "a@b.pl"}]), \
                mock.patch.object(email_ui, "sync_and_prefetch", return_value=0), \
                mock.patch.object(email_ui, "prefetch_recent", return_value=0):
            page = email_ui.EmailPage(1)
        page.current_account = page.accounts[0]
        page.current_folder_local = "INBOX"
        return page

    def find(self, page, path):
        def walk(item):
            if item.data(0, 0x100 + 4) == path:
                return item
            for i in range(item.childCount()):
                found = walk(item.child(i))
                if found:
                    return found
        for i in range(page.folder_tree.topLevelItemCount()):
            found = walk(page.folder_tree.topLevelItem(i))
            if found:
                return found

    def test_number_key_toggles_assignment(self):
        from app.email import email_ui
        from app.email.email_storage import get_folder_id, save_message_header

        page = self.make_page()
        save_message_header(1, get_folder_id(1, "INBOX"), "1", "<1@x>", "x@y.pl", "a@b.pl", "T", "2999-01-01 10:00", False, False)
        page.load_mail_list_from_db("INBOX")
        page.mail_list.topLevelItem(0).setSelected(True)
        number = [e["id"] for e in page.employees].index(7) + 1
        page._toggle_employee_by_number(number)
        self.assertTrue(page.mail_list.topLevelItem(0).toolTip(0).endswith("Pan T."))
        page._toggle_employee_by_number(number)
        self.assertEqual(page.mail_list.topLevelItem(0).toolTip(0), "")
        page._toggle_employee_by_number(99)  # brak pracownika - bez błędu

    def test_drop_folder_renames_on_server_and_mail_drop_moves(self):
        from app.email import email_ui
        from app.email.email_storage import get_folder_id, save_folder, save_message_header

        save_folder(1, "INBOX.A", "A", ".", "")
        save_folder(1, "INBOX.B", "B", ".", "")
        save_message_header(1, get_folder_id(1, "INBOX"), "1", "<1@x>", "x@y.pl", "a@b.pl", "T", "2999-01-01 10:00", False, False)
        page = self.make_page()
        page.load_folders_from_db()
        page.load_mail_list_from_db("INBOX")
        a, b = self.find(page, "INBOX.A"), self.find(page, "INBOX.B")

        calls = []
        with mock.patch.object(email_ui, "rename_folder", side_effect=lambda k, o, n: calls.append((k, o, n)) or "INBOX.B.A"), \
                mock.patch.object(email_ui.QMessageBox, "question", return_value=email_ui.QMessageBox.StandardButton.Yes), \
                mock.patch.object(page, "_run_async", side_effect=lambda w, d: d(w(), None)):
            page._on_folder_dropped(a, b)
        self.assertEqual(calls, [(1, "INBOX.A", "INBOX.B.A")])

        page.mail_list.topLevelItem(0).setSelected(True)
        moved = []
        with mock.patch.object(page, "_move_refs", side_effect=lambda refs, t, title: moved.append((len(refs), t))):
            page._on_mails_dropped(self.find(page, "INBOX.B"))
        self.assertEqual(moved, [(1, "INBOX.B")])


class SyncFolderOpsTests(unittest.TestCase):
    def test_create_and_rename_use_imap_commands(self):
        from app.email import email_sync

        class Conn:
            def __init__(self):
                self.cmds = []
                self.capabilities = ()

            def create(self, name):
                self.cmds.append(("create", name))
                return "OK", [b""]

            def rename(self, a, b):
                self.cmds.append(("rename", a, b))
                return "OK", [b""]

            def subscribe(self, n):
                self.cmds.append(("subscribe", n))
                return "OK", [b""]

            unsubscribe = subscribe

            def noop(self):
                return "OK", []

            def logout(self):
                pass

        conn = Conn()
        email_sync.close_sessions()
        with mock.patch.object(email_sync, "_get_account_info", return_value={"id": 5}), \
                mock.patch.object(email_sync, "_connect_imap", return_value=conn), \
                mock.patch.object(email_sync, "save_folder") as save, \
                mock.patch.object(email_sync, "get_folders_for_account", return_value=[{"delimiter": "."}]), \
                mock.patch.object(email_sync, "rename_folder_local") as local:
            self.assertEqual(email_sync.create_folder(5, "INBOX.Żółw"), email_sync.encode_mailbox_utf7("INBOX.Żółw"))
            email_sync.rename_folder(5, "INBOX.A", "INBOX.B")
        self.assertIn(("rename", '"INBOX.A"', '"INBOX.B"'), conn.cmds)
        local.assert_called_once_with(5, "INBOX.A", "INBOX.B")
        save.assert_called_once()
        email_sync.close_sessions()


if __name__ == "__main__":
    unittest.main()
