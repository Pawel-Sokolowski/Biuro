import sqlite3
import unittest

from tests.base import DbTestCase


class SchemaTests(DbTestCase):
    def tables(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()

    def test_required_tables_exist(self):
        for name in ("firmy", "pracownicy", "pelnomocnictwa", "wydarzenia", "wydarzenia_dostep",
                     "statusy_miesieczne", "zadania", "czasomierze", "udzialowcy", "app_settings"):
            self.assertIn(name, self.tables())

    def test_init_db_is_idempotent(self):
        from app.db.schema import init_db
        init_db()
        init_db()

    def test_monthly_status_columns_and_check(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(statusy_miesieczne)")}
            self.assertIn("dokumenty_dostarczone", cols)
            firma = self.make_company()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO statusy_miesieczne (firma_id, miesiac, etap) VALUES (?, '2026-01', 'Nieznany')",
                    (firma,),
                )
        finally:
            conn.close()

    def test_pelnomocnictwa_type_is_restricted(self):
        firma = self.make_company()
        emp = self.make_employee()
        conn = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO pelnomocnictwa (firma_id, pracownik_id, typ) VALUES (?, ?, 'XYZ')",
                    (firma, emp),
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
