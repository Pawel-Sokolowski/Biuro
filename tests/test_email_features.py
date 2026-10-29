import unittest
from unittest import mock

from app.email import email_ui
from PyQt6.QtWidgets import QApplication

from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(["biuro-tests"])


class HelperTests(unittest.TestCase):
    def test_display_names_and_dates(self):
        from datetime import datetime
        from app.email.email_ui import display_names, format_list_date

        self.assertEqual(
            display_names("Agnieszka Zakrzewska <biuro@szczecin-ksiegowosc.pl>"), "Agnieszka Zakrzewska"
        )
        self.assertEqual(display_names("a@b.pl"), "a@b.pl")
        self.assertEqual(display_names('"Kowalski, Jan" <j@k.pl>, Ewa <e@k.pl>'), "Kowalski, Jan, Ewa")
        self.assertEqual(format_list_date("2024-01-01 10:00"), "01.01.2024, 10:00")
        self.assertEqual(format_list_date(datetime.now().strftime("%Y-%m-%d 09:42")), "09:42")


class ServerStateTests(DbTestCase):
    def setUp(self):
        super().setUp()
        from app.db.dao_conn import get_conn
        from app.email.email_storage import get_folder_id, save_folder, save_message_header

        conn = get_conn()
        conn.execute("INSERT INTO konta_email (id, email) VALUES (1, 'a@b.pl')")
        conn.commit()
        conn.close()
        save_folder(1, "INBOX", "INBOX", ".", "")
        save_folder(1, "INBOX.Archiwum", "Archiwum", ".", "")
        self.inbox = get_folder_id(1, "INBOX")
        self.arch = get_folder_id(1, "INBOX.Archiwum")
        for uid in ("1", "2", "3"):
            save_message_header(1, self.inbox, uid, f"<m{uid}@x>", "x@y.pl", "a@b.pl", f"T{uid}",
                                "2999-01-0%s 10:00" % uid, uid == "1", False)

    def rows(self):
        from app.email.email_storage import get_cached_messages
        return {r["uid"]: r for r in get_cached_messages(1, self.inbox)}

    def test_unseen_on_server_marks_unread_and_missing_marked_deleted(self):
        from app.email.email_storage import apply_server_state

        apply_server_state(1, self.inbox, {"1", "2"}, {"1"}, "2000-01-01")
        rows = self.rows()
        self.assertEqual(rows["1"]["przeczytana"], 0)  # nieprzeczytana w Thunderbirdzie
        self.assertEqual(rows["2"]["przeczytana"], 1)
        self.assertNotIn("3", rows)  # usuniÄ™ta/przeniesiona poza programem

    def test_assignments_follow_message_id_after_move(self):
        from app.db.dao_conn import get_conn
        from app.email.email_storage import (
            count_assigned_by_employee, get_assignment_map, list_assigned_rows,
            mark_deleted, save_message_header, set_employee_assignment,
        )

        conn = get_conn()
        conn.execute("INSERT INTO pracownicy (id, email, haslo_hash, imie, nazwisko, aktywny) VALUES (7, 'p@b.pl', 'x', 'Pan', 'Test', 1)")
        conn.commit()
        row_id = self.rows()["2"]["id"]
        conn.close()

        set_employee_assignment([row_id], 7, True)
        set_employee_assignment([row_id], 7, True)  # bez duplikatĂłw
        self.assertEqual(get_assignment_map([row_id]), {row_id: [7]})
        self.assertEqual(count_assigned_by_employee(1), {7: 1})

        # przeniesienie do archiwum: stary wiersz usuniÄ™ty, nowy z tym samym Message-ID
        mark_deleted(1, "2", self.inbox)
        save_message_header(1, self.arch, "55", "<m2@x>", "x@y.pl", "a@b.pl", "T2", "2999-01-02 10:00", True, False)
        moved = list_assigned_rows(1, 7)
        self.assertEqual([(r["uid"], r["server_name"]) for r in moved], [("55", "INBOX.Archiwum")])
        self.assertEqual(count_assigned_by_employee(1), {7: 1})

        set_employee_assignment([moved[0]["id"]], 7, False)
        self.assertEqual(list_assigned_rows(1, 7), [])


class PageFeatureTests(ServerStateTests):
    def test_page_chips_assign_move_menu(self):
        from PyQt6.QtWidgets import QMenu
        from app.db.dao_conn import get_conn
        from app.email import email_ui
        from app.email.email_storage import get_folders_for_account

        conn = get_conn()
        conn.execute("INSERT INTO pracownicy (id, email, haslo_hash, imie, nazwisko, aktywny) VALUES (7, 'p@b.pl', 'x', 'Pan', 'Test', 1)")
        conn.commit()
        conn.close()
        with mock.patch.object(email_ui, "get_email_accounts_for_employee", return_value=[{"id": 1, "email": "a@b.pl"}]), \
                mock.patch.object(email_ui.EmailPage, "start_background_sync"):
            page = email_ui.EmailPage(1)
            page.current_account = page.accounts[0]
            page.current_folder_local = "INBOX"
            page.load_mail_list_from_db("INBOX")
            self.assertEqual(page.mail_list.columnCount(), 3)
            self.assertEqual(page.mail_list.topLevelItem(0).text(1), "x@y.pl")
            page.mail_list.topLevelItem(0).setSelected(True)
            page.mail_list.topLevelItem(1).setSelected(True)
            self.assertEqual(len(page._selected_refs()), 2)
            page._assign_selected(7, True)
            tags = ["Pan T." for i in range(page.mail_list.topLevelItemCount()) if page.mail_list.topLevelItem(i).toolTip(0).endswith("Pan T.")]
            self.assertEqual(tags.count("Pan T."), 2)
            self.assertIn("Pan T.", [b.text() for b in page.chips_group.buttons()])
            employee_chip = next(b for b in page.chips_group.buttons() if b.text() == "Pan T.")
            employee_chip.click()
            self.assertEqual(page.mail_list.topLevelItemCount(), 2)
            menu = QMenu()
            tree = email_ui.build_folder_tree(get_folders_for_account(1))
            page._fill_move_menu(menu, tree, {"INBOX"}, False)
            self.assertEqual([a.text() for a in menu.actions()], ["Archiwum"])


class SessionTests(unittest.TestCase):
    def test_shared_session_reused_and_replaced_after_failure(self):
        from app.email import email_sync

        class Conn:
            created = 0

            def __init__(self):
                Conn.created += 1
                self.dead = False

            def noop(self):
                if self.dead:
                    raise OSError("closed")

            def logout(self):
                pass

        email_sync.close_sessions()
        with mock.patch.object(email_sync, "_get_account_info", return_value={"id": 9}), \
                mock.patch.object(email_sync, "_connect_imap", side_effect=lambda a, t: Conn()):
            with email_sync._shared_session(9) as s1:
                pass
            with email_sync._shared_session(9) as s2:
                pass
            self.assertIs(s1, s2)
            self.assertEqual(Conn.created, 1)
            s2.conn.dead = True
            with email_sync._shared_session(9) as s3:
                pass
            self.assertIsNot(s3, s2)
            self.assertEqual(Conn.created, 2)
        email_sync.close_sessions()


if __name__ == "__main__":
    unittest.main()
