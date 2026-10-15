import unittest
from unittest.mock import patch

from app.db.dao_app_settings import set_setting
from app.db.dao_employees import login_employee
from app.utils import password_reset as pr
from tests.base import DbTestCase


class GeneratePasswordTests(unittest.TestCase):
    def test_strength_and_uniqueness(self):
        passwords = {pr.generate_password() for _ in range(50)}
        self.assertEqual(len(passwords), 50)
        for p in passwords:
            self.assertEqual(len(p), pr.PASSWORD_LENGTH)
            self.assertTrue(any(c.islower() for c in p))
            self.assertTrue(any(c.isupper() for c in p))
            self.assertTrue(any(c.isdigit() for c in p))


class ResetPasswordTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.emp = self.make_employee("jan@example.com", haslo="Stare123")

    def test_requires_configuration(self):
        ok, _ = pr.reset_password("jan@example.com")
        self.assertFalse(ok)

    def test_successful_reset_sends_and_changes_password(self):
        set_setting(pr.RESET_ACCOUNT_KEY, "7")
        with patch.object(pr, "send_email_smtp", return_value=(True, "")) as send:
            ok, _ = pr.reset_password("JAN@example.com")
        self.assertTrue(ok)
        account_id, recipients, _subject, body = send.call_args[0][:4]
        self.assertEqual(account_id, 7)
        self.assertEqual(recipients, ["JAN@example.com"])
        new_password = body.split("aplikacji Biuro: ")[1].split("\n")[0]
        self.assertIsNone(login_employee("jan@example.com", "Stare123"))
        self.assertEqual(login_employee("jan@example.com", new_password), self.emp)

    def test_failed_send_keeps_old_password(self):
        set_setting(pr.RESET_ACCOUNT_KEY, "7")
        with patch.object(pr, "send_email_smtp", return_value=(False, "SMTP down")):
            ok, _ = pr.reset_password("jan@example.com")
        self.assertFalse(ok)
        self.assertEqual(login_employee("jan@example.com", "Stare123"), self.emp)

    def test_unknown_email_gets_generic_answer_and_sends_nothing(self):
        set_setting(pr.RESET_ACCOUNT_KEY, "7")
        with patch.object(pr, "send_email_smtp") as send:
            ok, message = pr.reset_password("nikt@example.com")
        self.assertTrue(ok)
        self.assertEqual(message, pr.GENERIC_MESSAGE)
        send.assert_not_called()

    def test_inactive_employee_cannot_reset(self):
        from app.db.dao_employees import set_employee_active
        set_setting(pr.RESET_ACCOUNT_KEY, "7")
        set_employee_active(self.emp, False)
        with patch.object(pr, "send_email_smtp") as send:
            pr.reset_password("jan@example.com")
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class RateLimitTests(DbTestCase):
    def test_limit_per_email(self):
        set_setting(pr.RESET_ACCOUNT_KEY, "7")
        self.make_employee("jan@example.com")
        with patch.object(pr, "send_email_smtp", return_value=(True, "")) as send:
            results = [pr.reset_password("jan@example.com")[0] for _ in range(pr.MAX_ATTEMPTS_PER_EMAIL + 2)]
        self.assertEqual(results, [True] * pr.MAX_ATTEMPTS_PER_EMAIL + [False, False])
        self.assertEqual(send.call_count, pr.MAX_ATTEMPTS_PER_EMAIL)
