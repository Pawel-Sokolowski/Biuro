"""Testy importu nazw Saldeo bez potrzeby połączenia z bazą PostgreSQL."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from app.utils import saldeo_contractors_import as saldeo


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.committed = False

    def execute(self, query, params=None):
        self.calls.append((query, params))
        if query.startswith("SELECT"):
            return FakeResult(self.rows)
        return FakeResult([])

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class SaldeoContractorsImportTests(unittest.TestCase):
    def test_csv_only_kd_matches_nip_fills_empty_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Saldeo.csv"
            path.write_text(
                "Typ;Nazwa skrócona:;Nazwa pełna;NIP:;Adres email:\n"
                "K/D;Klient Saldeo;Firma;123-456-32-18;nowy@example.com\n"
                "K;Dostawca;Inna;2223334445;d@example.com\n"
                "K/D;Drugi;Firma 2;9998887766;inny@example.com\n",
                encoding="utf-8-sig",
            )
            db = FakeConnection([
                {"id": 1, "nip": "1234563218", "nazwa_saldeo": "", "adres_email": ""},
                {"id": 2, "nip": "9998887766", "nazwa_saldeo": "", "adres_email": "stary@example.com"},
            ])
            with patch.object(saldeo, "is_administrator", return_value=True), \
                 patch.object(saldeo, "get_conn", return_value=db):
                result = saldeo.import_saldeo_contractors(str(path), actor_id=1)

            self.assertEqual(result["eligible"], 2)
            self.assertEqual(result["skipped_type"], 1)
            self.assertEqual(result["updated_names"], 2)
            self.assertEqual(result["filled_emails"], 1)
            self.assertTrue(db.committed)
            updates = [(sql, params) for sql, params in db.calls if sql.startswith("UPDATE")]
            self.assertEqual(len(updates), 2)
            self.assertIn("nazwa_saldeo", updates[0][0])
            self.assertIn("nowy@example.com", updates[0][1])
            self.assertNotIn("adres_email", updates[1][0])

    def test_conflicting_rows_do_not_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Saldeo.csv"
            path.write_text(
                "Typ,Nazwa skrócona,NIP\n"
                "K/D,Pierwszy,1234563218\n"
                "K/D,Drugi,1234563218\n",
                encoding="utf-8",
            )
            db = FakeConnection([{"id": 1, "nip": "1234563218",
                                  "nazwa_saldeo": "", "adres_email": ""}])
            with patch.object(saldeo, "is_administrator", return_value=True), \
                 patch.object(saldeo, "get_conn", return_value=db):
                result = saldeo.import_saldeo_contractors(str(path), actor_id=1)
            self.assertEqual(result["ambiguous"], 1)
            self.assertEqual(result["updated_names"], 0)
            self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in db.calls))

    def test_xlsx_saldeo_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Saldeo.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.append(["Typ", "Nazwa skrócona:", "Nazwa pełna", "NIP:", "Adres email:"])
            sheet.append(["K/D", "Skrót Saldeo", "Pełna nazwa", "1234563218", "test@example.com"])
            book.save(path)
            records = saldeo.read_saldeo_contractors(str(path))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["short_name"], "Skrót Saldeo")
            self.assertEqual(records[0]["email"], "test@example.com")

    def test_requires_administrator(self):
        with patch.object(saldeo, "is_administrator", return_value=False):
            with self.assertRaises(PermissionError):
                saldeo.import_saldeo_contractors("not-existing.csv", actor_id=2)


if __name__ == "__main__":
    unittest.main()
