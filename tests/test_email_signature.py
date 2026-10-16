import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.db.dao_employees import add_email_account_for_employee, assign_email_to_employee
from app.email import signature as sig
from app.email.compose_dialog import ComposeDialog
from app.email.email_storage import get_email_accounts_for_employee
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication([])


class SignatureTests(DbTestCase):
    def test_default_template(self):
        text, html = sig.build_default_signature("Anna", "Nowak")
        self.assertTrue(text.startswith("--\nPozdrawiam\nAnna Nowak\n"))
        self.assertIn("Biuro Rachunkowe EM-KA Sp. z o.o.\nul. Chopina 35\n71-450 Szczecin\ntel: 123456789", text)
        self.assertIn("biuro@szczecin-ksiegowosc.pl", text)
        self.assertIn("Prosimy o poinformowanie nadawcy", text)
        self.assertIn("Anna Nowak", html)

    def test_template_is_editable(self):
        sig.save_template({"stopka_szablon_telefon": "999"})
        text, _ = sig.build_default_signature("A", "B")
        self.assertIn("tel: 999", text)

    def test_employee_signature_default_and_saved(self):
        pid = self.make_employee(imie="Ewa", nazwisko="Lis")
        text, _ = sig.get_employee_signature(pid)
        self.assertIn("Ewa Lis", text)
        sig.save_employee_signature(pid, "Moja stopka", "<p>Moja stopka</p>")
        self.assertEqual(sig.get_employee_signature(pid), ("Moja stopka", "<p>Moja stopka</p>"))

    def test_merge_places_signature_before_quote_and_avoids_duplicates(self):
        quote = "<p><br></p><p>W dniu x, ktoś pisze:</p><blockquote>cytat</blockquote>"
        merged = sig.merge_signature(quote, "", "<p>Stopka Jana</p>", "Stopka Jana")
        self.assertLess(merged.index("Stopka Jana"), merged.index("cytat"))
        self.assertEqual(sig.merge_signature(merged, "", "<p>Stopka Jana</p>", "Stopka Jana"), "")

    def test_import_files(self):
        with tempfile.TemporaryDirectory() as d:
            html_path = os.path.join(d, "s.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><head><title>x</title></head><body><p>Jan</p><p>tel 1</p></body></html>")
            text, html = sig.load_signature_file(html_path)
            self.assertEqual(text, "Jan\n\ntel 1")
            self.assertNotIn("<body", html)
            txt_path = os.path.join(d, "s.txt")
            with open(txt_path, "w", encoding="cp1250") as f:
                f.write("Pozdrawiam\nŁukasz")
            self.assertIn("Łukasz", sig.load_signature_file(txt_path)[0])

    def test_compose_appends_signature_once(self):
        pid = self.make_employee(imie="Jan", nazwisko="Kowalski")
        dlg = ComposeDialog(pracownik_id=pid, body="Dzień dobry")
        plain = dlg.values()["body"]
        self.assertIn("Dzień dobry", plain)
        self.assertIn("Jan Kowalski", plain)
        self.assertEqual(plain.count("Pozdrawiam"), 1)
        reply = ComposeDialog(pracownik_id=pid, html=dlg.values()["html"])
        self.assertEqual(reply.values()["body"].count("Pozdrawiam"), 1)

    def test_compose_reply_signature_before_quote(self):
        pid = self.make_employee()
        html = "<p><br></p><p>Pisze:</p><blockquote>stary tekst</blockquote>"
        body = ComposeDialog(pracownik_id=pid, html=html).values()["body"]
        self.assertLess(body.index("Pozdrawiam"), body.index("stary tekst"))

    def test_sender_choice(self):
        pid = self.make_employee()
        own = add_email_account_for_employee(None, "jan@x.pl", "i", 993, "s", 465, "x")
        main = add_email_account_for_employee(None, "biuro@x.pl", "i", 993, "s", 465, "x")
        assign_email_to_employee(own, pid)
        accounts = get_email_accounts_for_employee(pid)
        self.assertEqual(sig.choose_sender(accounts, accounts[0])["id"], own)
        sig.set_default_sender_id(main)
        self.assertEqual(sig.choose_sender(accounts, accounts[0])["id"], own)  # brak dostępu
        assign_email_to_employee(main, pid)
        accounts = get_email_accounts_for_employee(pid)
        self.assertEqual(sig.choose_sender(accounts, accounts[0])["id"], main)
        dlg = ComposeDialog(pracownik_id=pid, accounts=accounts, account_id=main)
        self.assertEqual(dlg.values()["account_id"], main)


if __name__ == "__main__":
    unittest.main()
