import csv
import imaplib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import openpyxl

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.email import email_sync
from tests.base import DbTestCase

ACCOUNT = {"serwer_imap": "imap.example.test", "email": "biuro@example.test", "port_imap": 993,
           "ssl_imap": "SSL/TLS", "haslo_enc": "zaszyfrowane"}


class ImapAuthFailureTests(unittest.TestCase):
    def setUp(self):
        email_sync._AUTH_FAILURES.clear()

    def test_wrong_password_is_ignored_quietly_and_not_retried(self):
        error = imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Authentication failed.")
        with mock.patch.object(email_sync, "decrypt_password", return_value="zle"), \
                mock.patch.object(email_sync, "open_imap", side_effect=error) as opened, \
                mock.patch.object(email_sync.logger, "exception") as logged_exception:
            self.assertIsNone(email_sync._connect_imap(dict(ACCOUNT)))
            self.assertIsNone(email_sync._connect_imap(dict(ACCOUNT)))
        self.assertEqual(opened.call_count, 1)
        logged_exception.assert_not_called()

    def test_undecryptable_password_does_not_try_to_log_in(self):
        with mock.patch.object(email_sync, "decrypt_password", return_value=""), \
                mock.patch.object(email_sync, "open_imap") as opened:
            self.assertIsNone(email_sync._connect_imap(dict(ACCOUNT)))
        opened.assert_not_called()

    def test_changed_password_is_tried_again(self):
        error = imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Authentication failed.")
        with mock.patch.object(email_sync, "decrypt_password", return_value="x"), \
                mock.patch.object(email_sync, "open_imap", side_effect=[error, "polaczenie"]) as opened:
            self.assertIsNone(email_sync._connect_imap(dict(ACCOUNT)))
            self.assertEqual(email_sync._connect_imap(dict(ACCOUNT, haslo_enc="nowe")), "polaczenie")
        self.assertEqual(opened.call_count, 2)

    def test_retry_is_allowed_after_cooldown(self):
        error = imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Authentication failed.")
        with mock.patch.object(email_sync, "decrypt_password", return_value="x"), \
                mock.patch.object(email_sync, "open_imap", side_effect=[error, "ok"]) as opened:
            email_sync._connect_imap(dict(ACCOUNT))
            for key in email_sync._AUTH_FAILURES:
                email_sync._AUTH_FAILURES[key] -= email_sync.AUTH_RETRY_SECONDS + 1
            self.assertEqual(email_sync._connect_imap(dict(ACCOUNT)), "ok")
        self.assertEqual(opened.call_count, 2)

    def test_network_errors_are_still_logged(self):
        with mock.patch.object(email_sync, "decrypt_password", return_value="x"), \
                mock.patch.object(email_sync, "open_imap", side_effect=OSError("timeout")), \
                mock.patch.object(email_sync.logger, "exception") as logged_exception:
            self.assertIsNone(email_sync._connect_imap(dict(ACCOUNT)))
        logged_exception.assert_called_once()


class ExtraServicesImportTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.logic = GlobalSettingsLogic()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _csv(self, rows, delimiter=";", encoding="utf-8"):
        path = Path(self.tmp.name) / "uslugi.csv"
        with path.open("w", newline="", encoding=encoding) as handle:
            csv.writer(handle, delimiter=delimiter).writerows(rows)
        return str(path)

    def _services(self):
        return {str(r["nazwa"]): r for r in self.logic.get_extra_services()}

    def test_csv_with_header_uses_default_vat(self):
        self.logic.set_setting("uslugi_domyslny_vat", "8%")
        result = self.logic.import_extra_services_from_csv(
            self._csv([["Nazwa", "Netto", "Kategoria"], ["Zmiana adresu", "123,50", "kadry"], ["Wniosek", "50", ""]]))
        self.assertEqual(result, {"dodane": 2, "zaktualizowane": 0, "pominiete": 0})
        services = self._services()
        self.assertEqual(float(services["Zmiana adresu"]["cena_netto"]), 123.5)
        self.assertEqual(services["Zmiana adresu"]["stawka_vat"], "8%")
        self.assertEqual(services["Zmiana adresu"]["kategoria"], "kadry")
        self.assertEqual(services["Wniosek"]["kategoria"], "księgowość")

    def test_vat_falls_back_to_23_percent(self):
        self.logic.import_extra_services_from_csv(self._csv([["Nazwa", "Netto", "Kategoria"], ["Usługa", "10", "inne"]]))
        self.assertEqual(self._services()["Usługa"]["stawka_vat"], "23%")

    def test_csv_without_header_comma_delimiter_and_cp1250(self):
        path = self._csv([["Zaświadczenie", "99.90", "inne"]], delimiter=",", encoding="cp1250")
        self.logic.import_extra_services_from_csv(path)
        self.assertEqual(float(self._services()["Zaświadczenie"]["cena_netto"]), 99.9)

    def test_existing_service_is_updated_keeping_its_vat(self):
        self.logic.add_extra_service("Raport", 10, "zw", "inne")
        result = self.logic.import_extra_services_from_csv(
            self._csv([["Nazwa", "Netto", "Kategoria"], ["raport", "77", "kadry"], ["", "5", ""], ["Raport", "1", ""]]))
        self.assertEqual(result, {"dodane": 0, "zaktualizowane": 1, "pominiete": 2})
        service = self._services()["raport"]
        self.assertEqual((float(service["cena_netto"]), service["stawka_vat"], service["kategoria"]),
                         (77.0, "zw", "kadry"))

    def test_xlsx_import(self):
        path = Path(self.tmp.name) / "uslugi.xlsx"
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["Nazwa", "Netto", "Kategoria"])
        sheet.append(["Rozliczenie roczne", 250, "księgowość"])
        sheet.append([None, None, None])
        workbook.save(path)
        result = self.logic.import_extra_services_from_csv(str(path))
        self.assertEqual(result["dodane"], 1)
        self.assertEqual(float(self._services()["Rozliczenie roczne"]["cena_netto"]), 250.0)

    def test_unsupported_and_missing_files(self):
        with self.assertRaises(FileNotFoundError):
            self.logic.import_extra_services_from_csv(str(Path(self.tmp.name) / "brak.csv"))
        other = Path(self.tmp.name) / "uslugi.txt"
        other.write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.logic.import_extra_services_from_csv(str(other))


if __name__ == "__main__":
    unittest.main()
