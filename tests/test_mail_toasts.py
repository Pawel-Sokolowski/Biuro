import unittest

from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.email.email_storage import get_new_unread_messages, save_folder
from app.mail_toasts import _sender_name


class MailToastTests(DbTestCase):
    def _insert(self, folder_id, uid, read=0, deleted=0):
        conn = get_conn()
        conn.execute(
            "INSERT INTO wiadomosci_email (konto_id, folder_id, uid, nadawca, temat, przeczytana, usunieta) "
            "VALUES (1, %s, %s, 'Jan <j@x.pl>', 'Temat', %s, %s)", (folder_id, uid, read, deleted))
        conn.commit()
        conn.close()

    def test_only_new_unread_visible_messages_after_baseline(self):
        conn = get_conn()
        conn.execute("INSERT INTO konta_email (id, email) VALUES (1, 'a@b.pl')")
        conn.commit()
        conn.close()
        inbox = save_folder(1, "INBOX", "INBOX", ".", "")
        sent = save_folder(1, "Sent", "Sent", ".", "\\Sent")
        hidden = save_folder(1, "INBOX.Ukryty", "Ukryty", ".", "")
        self._insert(inbox, "1")
        baseline, found = get_new_unread_messages(1, None)
        self.assertEqual(found, [])

        self._insert(inbox, "2")
        self._insert(inbox, "3", read=1)
        self._insert(inbox, "4", deleted=1)
        self._insert(sent, "5")
        self._insert(hidden, "6")
        max_id, found = get_new_unread_messages(1, baseline, {"INBOX.Ukryty"})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], baseline + 1)
        self.assertEqual(get_new_unread_messages(1, max_id)[1], [])

    def test_sender_name(self):
        self.assertEqual(_sender_name('"Jan Kowalski" <j@x.pl>'), "Jan Kowalski")
        self.assertEqual(_sender_name("j@x.pl"), "j@x.pl")


if __name__ == "__main__":
    unittest.main()
