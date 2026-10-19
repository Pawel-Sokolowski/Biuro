import os
import unittest
from unittest import mock

from cryptography.fernet import Fernet

from app.db.dao_employees import get_all_email_accounts, get_email_accounts_for_employee
from app.email.email_storage import get_email_accounts_for_employee as storage_accounts
from app.utils.system_mailbox import get_system_account, get_system_account_id, save_system_account
from tests.base import DbTestCase


class SystemMailboxTests(DbTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"BIO_ENCRYPTION_KEY": Fernet.generate_key().decode()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, password="Sekret1"):
        return save_system_account("system@x.pl", password, "imap.x.pl", "smtp.x.pl")

    def test_account_is_saved_and_used_as_reset_sender(self):
        account_id = self.save()
        self.assertEqual(get_system_account_id(), account_id)
        account = get_system_account()
        self.assertEqual((account["email"], account["systemowe"]), ("system@x.pl", 1))
        self.assertNotEqual(account["haslo_enc"], "Sekret1")

    def test_account_is_hidden_from_every_list(self):
        emp = self.make_employee()
        self.save()
        visible = self.save_normal_account(emp)
        self.assertEqual([a["email"] for a in get_all_email_accounts()], ["jan@x.pl"])
        self.assertEqual([a["id"] for a in get_email_accounts_for_employee(emp)], [visible])
        self.assertEqual(storage_accounts(emp), [])

    def test_hidden_even_when_assigned_to_employee(self):
        emp = self.make_employee()
        account_id = self.save()
        from app.db.dao_conn import get_conn
        conn = get_conn()
        conn.execute("INSERT INTO pracownik_konto_email (pracownik_id, konto_id) VALUES (%s, %s)", (emp, account_id))
        conn.commit()
        conn.close()
        self.assertEqual(storage_accounts(emp), [])

    def test_update_keeps_password_when_blank_and_single_row(self):
        first = self.save()
        old = get_system_account()["haslo_enc"]
        second = save_system_account("nowy@x.pl", "", "imap.x.pl", "smtp.x.pl")
        self.assertEqual(first, second)
        self.assertEqual(get_system_account()["haslo_enc"], old)
        self.assertEqual(get_system_account()["email"], "nowy@x.pl")

    def test_requires_password_on_first_save(self):
        with self.assertRaises(ValueError):
            save_system_account("a@x.pl", "", "i", "s")

    def save_normal_account(self, emp):
        from app.db.dao_employees import add_email_account_for_employee
        return add_email_account_for_employee(emp, "jan@x.pl", "i", 993, "s", 465, "enc")


if __name__ == "__main__":
    unittest.main()