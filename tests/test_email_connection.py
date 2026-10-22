import os
import unittest
from unittest import mock

from cryptography.fernet import Fernet

from app.email import email_connection as ec
from app.email.email_storage import get_email_accounts_for_employee, get_known_uids
from tests.base import DbTestCase


class SecurityTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(ec.normalize_security("SSL/TLS"), ec.SECURITY_SSL)
        self.assertEqual(ec.normalize_security("starttls"), ec.SECURITY_STARTTLS)
        self.assertEqual(ec.normalize_security("Brak"), ec.SECURITY_NONE)

    def test_imap_starttls(self):
        with mock.patch("imaplib.IMAP4") as plain:
            conn = ec.open_imap("h", 143, "STARTTLS", "u", "p", 5)
        plain.return_value.starttls.assert_called_once()
        plain.return_value.login.assert_called_once_with("u", "p")
        self.assertIs(conn, plain.return_value)

    def test_imap_ssl_and_none(self):
        with mock.patch("imaplib.IMAP4_SSL") as ssl_cls:
            ec.open_imap("h", 993, "SSL/TLS", "u", "p", 5)
        ssl_cls.assert_called_once()
        with mock.patch("imaplib.IMAP4") as plain:
            ec.open_imap("h", 143, "Brak", "u", "p", 5)
        plain.return_value.starttls.assert_not_called()

    def test_smtp_modes(self):
        with mock.patch("smtplib.SMTP") as smtp:
            ec.open_smtp("h", 587, "STARTTLS", "u", "p", 5)
            smtp.return_value.starttls.assert_called_once()
        with mock.patch("smtplib.SMTP") as smtp:
            ec.open_smtp("h", 25, "Brak", "u", "p", 5)
            smtp.return_value.starttls.assert_not_called()
        with mock.patch("smtplib.SMTP_SSL") as smtp_ssl:
            ec.open_smtp("h", 465, "SSL/TLS", "u", "p", 5)
            smtp_ssl.assert_called_once()

    def test_check_reports_error(self):
        with mock.patch.object(ec, "open_imap", side_effect=OSError("boom")):
            ok, msg = ec.check_imap({"serwer_imap": "h", "haslo": "x"})
        self.assertFalse(ok)
        self.assertTrue(msg)


class AccessTests(DbTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"BIO_ENCRYPTION_KEY": Fernet.generate_key().decode()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_account_visible_only_after_assignment(self):
        from app.admin.global_settings_logic import GlobalSettingsLogic

        logic = GlobalSettingsLogic()
        acc = logic.add_email_account("a@x.pl", "imap.x.pl", "smtp.x.pl", "haslo")
        self.assertEqual(get_email_accounts_for_employee(1), [])
        logic.set_account_employees(acc, [1])
        self.assertEqual([a["id"] for a in get_email_accounts_for_employee(1)], [acc])
        self.assertEqual(logic.get_account_employee_ids(acc), [1])
        logic.set_account_employees(acc, [])
        self.assertEqual(get_email_accounts_for_employee(1), [])

    def test_known_uids_empty(self):
        self.assertEqual(get_known_uids(1, 1), set())

    def test_access_button_saves_selected_employee(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication, QDialog, QPushButton

        from app.admin.global_settings_logic import GlobalSettingsLogic
        from app.admin.settings import email_settings as es

        _app = QApplication.instance() or QApplication(["biuro-tests"])
        acc = GlobalSettingsLogic().add_email_account("b@x.pl", "i", "s", "haslo")
        page = es.EmailSettings()
        page.table.selectRow(0)

        def fake_exec(dlg):
            dlg.list.item(0).setCheckState(Qt.CheckState.Checked)
            return QDialog.DialogCode.Accepted

        with mock.patch.object(es.EmployeeAccessDialog, "exec", fake_exec):
            next(b for b in page.findChildren(QPushButton) if b.text() == "Dostęp pracowników").click()
        self.assertEqual(GlobalSettingsLogic().get_account_employee_ids(acc), [1])
        self.assertEqual([a["id"] for a in get_email_accounts_for_employee(1)], [acc])


if __name__ == "__main__":
    unittest.main()
