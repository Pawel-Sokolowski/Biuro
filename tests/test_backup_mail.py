import unittest
from email.message import EmailMessage
from unittest import mock

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn
from app.utils import backup_mail
from app.utils.backup_status import get_backup_status, parse_backup_log
from tests.base import DbTestCase

LOG = """  2026-10-15 01:15 The file "E:\\backup_baz\\Adamac_S_C__20261014.BAK" has been compressed
    2026-10-15 01:15 Compressing the file "E:\\backup_baz\\Adamkiewicz_Paweł_20261014.BAK" into "\\\\nas6t\\backup\\bazy_enova\\backup_baz 2026-10-15 01;14;37 (Full).zip"
    2026-10-15 01:15 The file "E:\\backup_baz\\Adamkiewicz_Paweł_20261014.BAK" has been compressed
    2026-10-15 01:15 Compressing the file "E:\\backup_baz\\Adamowicz_Monika_20261014.BAK" into "\\\\nas6t\\backup\\x.zip"
    2026-10-15 01:16 The file "E:\\backup_baz\\Adamowicz_Monika_20261014.BAK" has been compressed
    2026-10-15 01:16 Compressing the file "E:\\backup_baz\\Ajdukonis_Klaudia_20261014.BAK" into "\\\\nas6t\\backup\\x.zip"
"""


def plain_message(body):
    msg = EmailMessage()
    msg["From"] = "Backup <powiadomienia@szczecin-ksiegowosc.pl>"
    msg.set_content(body)
    return msg


class MailBodyTests(unittest.TestCase):
    def test_plain_body(self):
        self.assertIn("Adamkiewicz_Paweł", backup_mail.message_log_text(plain_message(LOG)))

    def test_html_body_keeps_lines(self):
        msg = EmailMessage()
        msg.set_content("x")
        msg.clear_content()
        html = "<html><body>" + "<br>".join(line.strip() for line in LOG.splitlines()) + "</body></html>"
        msg.add_alternative(html, subtype="html")
        parsed = parse_backup_log(backup_mail.message_log_text(msg))
        self.assertEqual(sorted(parsed["databases"]), ["Adamac_S_C", "Adamkiewicz_Paweł", "Adamowicz_Monika", "Ajdukonis_Klaudia"])

    def test_html_table_rows_keep_date_and_message_apart(self):
        html = ("<table><tr><td>2026-10-22 01:12</td><td>Compressing the file "
                '"E:\\backup_baz\\Adamac_S_C__20261021.BAK" into "x.zip"</td></tr>'
                "<tr><td>2026-10-22 01:13</td><td>The file "
                '"E:\\backup_baz\\Adamac_S_C__20261021.BAK" has been compressed</td></tr></table>')
        parsed = parse_backup_log(backup_mail.html_to_text(html))
        self.assertIsNotNone(parsed["databases"]["Adamac_S_C"]["last_success"])

    def test_log_attachment_used_when_body_empty(self):
        msg = EmailMessage()
        msg["From"] = "powiadomienia@szczecin-ksiegowosc.pl"
        msg.set_content(" ")
        msg.add_attachment(LOG.encode("utf-8"), maintype="text", subtype="plain", filename="backup.log")
        self.assertIn("Adamac_S_C", backup_mail.message_log_text(msg))

    def test_user_sample_per_database(self):
        dbs = parse_backup_log(LOG)["databases"]
        self.assertEqual(str(dbs["Adamac_S_C"]["file_date"]), "2026-10-14")
        self.assertIsNotNone(dbs["Adamac_S_C"]["last_success"])
        self.assertIsNotNone(dbs["Adamowicz_Monika"]["last_success"])
        self.assertIsNone(dbs["Ajdukonis_Klaudia"]["last_success"])


class MailRefreshTests(DbTestCase):
    def setUp(self):
        super().setUp()
        conn = get_conn()
        conn.execute("INSERT INTO konta_email (email, systemowe) VALUES ('Powiadomienia@szczecin-ksiegowosc.pl', 1)")
        conn.execute("INSERT INTO foldery_email (konto_id, server_name) VALUES (1, 'INBOX')")
        rows = [
            ("1", "Backup <noreply@enova.pl>", "Wed, 14 Oct 2026 09:00:00 +0200"),
            ("2", "powiadomienia@szczecin-ksiegowosc.pl", "Wed, 14 Oct 2026 02:00:00 +0200"),
            ("3", "powiadomienia@szczecin-ksiegowosc.pl", "Thu, 15 Oct 2026 02:00:00 +0200"),
        ]
        for uid, sender, date in rows:
            conn.execute(
                "INSERT INTO wiadomosci_email (konto_id, folder_id, uid, nadawca, data) VALUES (1, 1, %s, %s, %s)",
                (uid, sender, date),
            )
        conn.commit()
        conn.close()
        set_setting("password_reset_account_id", "1")

    def test_latest_mail_uses_stored_date_format_not_row_id(self):
        conn = get_conn()
        conn.execute("DELETE FROM wiadomosci_email WHERE konto_id = 1")
        # nagłówki są zapisywane od najnowszych, więc najnowszy mail ma najmniejsze id
        for uid, date in (("30", "2026-10-22 01:15"), ("20", "2026-10-21 01:15"), ("10", "2026-10-20 01:15")):
            conn.execute(
                "INSERT INTO wiadomosci_email (konto_id, folder_id, uid, nadawca, data) VALUES (1, 1, %s, 'x', %s)",
                (uid, date),
            )
        conn.commit()
        conn.close()
        self.assertEqual(backup_mail.find_latest_backup_mail()["uid"], "30")

    def test_latest_mail_from_sender(self):
        mail = backup_mail.find_latest_backup_mail()
        self.assertEqual(mail["uid"], "3")
        self.assertEqual(mail["server_name"], "INBOX")

    def test_refresh_stores_log_and_status_uses_it(self):
        with mock.patch("app.email.email_sync.fetch_message_from_imap", return_value=plain_message(LOG)) as fetch:
            result = backup_mail.refresh_backup_log_from_mail(sync_headers=False)
            again = backup_mail.refresh_backup_log_from_mail(sync_headers=False)
        self.assertTrue(result["ok"])
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(again["zmieniono"])
        self.assertEqual(get_setting("backup_mail_ref"), "1:3")
        from datetime import datetime
        status = get_backup_status(datetime(2026, 10, 15, 9, 0))
        states = {b["nazwa"]: b["stan"] for b in status["bazy"]}
        self.assertEqual(states["Adamowicz_Monika"], "ok")
        self.assertEqual(states["Ajdukonis_Klaudia"], "blad")

    def test_missing_account_reports_clearly(self):
        conn = get_conn()
        set_setting("password_reset_account_id", "")
        result = backup_mail.refresh_backup_log_from_mail(sync_headers=False)
        self.assertFalse(result["ok"])
        self.assertIn("konto systemowe", result["komunikat"].lower())

    def test_no_mail_reports_clearly(self):
        conn = get_conn()
        conn.execute("DELETE FROM wiadomosci_email WHERE konto_id = 1")
        conn.commit()
        conn.close()
        self.assertFalse(backup_mail.refresh_backup_log_from_mail(sync_headers=False)["ok"])


if __name__ == "__main__":
    unittest.main()
