import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.db.dao_conn import get_conn
from app.utils import saldeo_status
from app.utils.saldeo_contractors_import import import_saldeo_company_names
from tests.base import DbTestCase

NIP = "8522605060"


class SaldeoCompanyNamesTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.firma = self.make_company("Biuro Rachunkowe EM-KA sp. z o.o.", nip=NIP,
                                       nazwa_skrocona="EM-KA", nazwa_saldeo="0383")

    def _xlsx(self, rows):
        path = Path(self.tmp.name) / "saldeo.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.append(["Nazwa firmy", "Nazwa skrócona", "Login"])
        for row in rows:
            sheet.append(row)
        book.save(path)
        return str(path)

    def _names(self):
        conn = get_conn()
        try:
            return dict(conn.execute(
                "SELECT nazwa_saldeo, nazwa_saldeo_firmy FROM firmy WHERE id = %s", (self.firma,)).fetchone())
        finally:
            conn.close()

    def test_import_matches_company_by_nip_from_login_and_keeps_contractor_name(self):
        result = import_saldeo_company_names(
            self._xlsx([["Biuro Rachunkowe EM-KA sp. z o.o.", "EMKASP", "emka-" + NIP],
                        ["Nieznana", "XYZ", "emka-1234563218"], ["Bez NIP", "ABC", "emka-"]]), 1)
        self.assertEqual((result["updated"], result["missing_company"], result["invalid_login"]), (1, 1, 1))
        self.assertEqual(self._names(), {"nazwa_saldeo": "0383", "nazwa_saldeo_firmy": "EMKASP"})
        again = import_saldeo_company_names(self._xlsx([["x", "EMKASP", "emka-" + NIP]]), 1)
        self.assertEqual((again["updated"], again["unchanged"]), (0, 1))

    def test_only_administrator_can_import(self):
        employee = self.make_employee()
        with self.assertRaises(PermissionError):
            import_saldeo_company_names(self._xlsx([["x", "EMKASP", "emka-" + NIP]]), employee)

    def test_mail_subject_is_matched_by_company_name_not_contractor_name(self):
        import_saldeo_company_names(self._xlsx([["x", "EMKASP", "emka-" + NIP]]), 1)
        conn = get_conn()
        try:
            account = conn.execute("INSERT INTO konta_email (email) VALUES ('biuro@example.test') RETURNING id").fetchone()["id"]
            folder = conn.execute("INSERT INTO foldery_email (konto_id, server_name) VALUES (%s, 'INBOX') RETURNING id",
                                  (account,)).fetchone()["id"]
            for uid, subject in enumerate((
                    "0383 Rozliczenie wyników firmy, podatków za miesiąc wrzesień 2026",
                    "EMKASP Rozliczenie wyników firmy, podatków za miesiąc wrzesień 2026"), 1):
                conn.execute("INSERT INTO wiadomosci_email (konto_id, folder_id, uid, temat) "
                             "VALUES (%s, %s, %s, %s)", (account, folder, str(uid), subject))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(saldeo_status.apply_saldeo_emails(), 1)
        conn = get_conn()
        try:
            row = conn.execute("SELECT firma_id, etap FROM statusy_miesieczne WHERE miesiac = '2026-09'").fetchone()
        finally:
            conn.close()
        self.assertEqual((row["firma_id"], row["etap"]), (self.firma, "Wysłane wyniki"))

    def test_one_time_rescan_fills_statuses_for_current_and_previous_month_only(self):
        import_saldeo_company_names(self._xlsx([["x", "EMKASP", "emka-" + NIP]]), 1)
        conn = get_conn()
        try:
            account = conn.execute("INSERT INTO konta_email (email) VALUES ('biuro@example.test') RETURNING id").fetchone()["id"]
            folder = conn.execute("INSERT INTO foldery_email (konto_id, server_name) VALUES (%s, 'INBOX') RETURNING id",
                                  (account,)).fetchone()["id"]
            for uid, subject in enumerate((
                    "EMKASP Rozliczenie wyników firmy, podatków za miesiąc październik 2026",
                    "EMKASP Rozliczenie ZUS za miesiąc październik 2026",
                    "EMKASP Rozliczenie ZUS za miesiąc styczeń 2020",
                    "Inna Rozliczenie ZUS za miesiąc październik 2026"), 1):
                conn.execute("INSERT INTO wiadomosci_email (konto_id, folder_id, uid, temat) "
                             "VALUES (%s, %s, %s, %s)", (account, folder, str(uid), subject))
            conn.commit()
        finally:
            conn.close()
        result = saldeo_status.rescan_saldeo_emails({"2026-10"})
        self.assertEqual((result["checked"], result["matched"], result["unknown_company"], result["changed"]),
                         (3, 2, 1, 2))
        self.assertEqual(saldeo_status.rescan_saldeo_emails({"2026-10"})["changed"], 0)
        conn = get_conn()
        try:
            row = conn.execute("SELECT etap, etap_kadry FROM statusy_miesieczne WHERE miesiac = '2026-10'").fetchone()
        finally:
            conn.close()
        self.assertEqual((row["etap"], row["etap_kadry"]), ("Wysłane wyniki", "Wysłane wyniki"))

    def test_startup_rescan_runs_only_once_per_launch(self):
        from unittest.mock import patch
        saldeo_status._startup_rescan_done = False
        with patch.object(saldeo_status, "rescan_saldeo_emails", return_value={"changed": 0}) as scan:
            saldeo_status.rescan_on_startup()
            saldeo_status.rescan_on_startup()
        self.assertEqual(scan.call_count, 1)

    def test_sent_results_automatically_tick_complete_documents(self):
        from app.db.dao_monthly import get_month_status, save_month_status
        import_saldeo_company_names(self._xlsx([["x", "EMKASP", "emka-" + NIP]]), 1)
        conn = get_conn()
        try:
            account = conn.execute("INSERT INTO konta_email (email) VALUES ('biuro@example.test') RETURNING id").fetchone()["id"]
            folder = conn.execute("INSERT INTO foldery_email (konto_id, server_name) VALUES (%s, 'INBOX') RETURNING id",
                                  (account,)).fetchone()["id"]
            conn.execute("INSERT INTO wiadomosci_email (konto_id, folder_id, uid, temat) VALUES (%s, %s, '1', %s)",
                         (account, folder, "EMKASP Rozliczenie wyników firmy, podatków za miesiąc październik 2026"))
            conn.commit()
        finally:
            conn.close()
        saldeo_status.apply_saldeo_emails()
        status = get_month_status(self.firma, "2026-10")
        self.assertEqual(status["dokumenty_dostarczone"], 1)
        self.assertTrue(status["data_dostarczenia"])

        save_month_status(self.firma, "2026-11", {"etap": "Wysłane wyniki"})
        manual = get_month_status(self.firma, "2026-11")
        self.assertEqual(manual["dokumenty_dostarczone"], 1)
        save_month_status(self.firma, "2026-12", {"etap": "Zaksięgowane"})
        self.assertEqual(get_month_status(self.firma, "2026-12")["dokumenty_dostarczone"], 0)


if __name__ == "__main__":
    unittest.main()
