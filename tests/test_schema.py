import unittest

import psycopg

from app.db.dao_conn import get_conn

from tests.base import DbTestCase


class SchemaTests(DbTestCase):
    def tables(self):
        conn = get_conn()
        try:
            return {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
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

    def test_deleted_demo_company_is_not_recreated(self):
        from app.db.schema import init_db

        conn = get_conn()
        try:
            conn.execute("DELETE FROM firmy WHERE nazwa = 'Domyślna firma'")
            conn.commit()
        finally:
            conn.close()

        init_db()

        conn = get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM firmy WHERE nazwa = 'Domyślna firma'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 0)

    def test_monthly_status_columns_and_check(self):
        conn = get_conn()
        try:
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'statusy_miesieczne'")}
            self.assertIn("dokumenty_dostarczone", cols)
            firma = self.make_company()
            with self.assertRaises(psycopg.IntegrityError):
                conn.execute(
                    "INSERT INTO statusy_miesieczne (firma_id, miesiac, etap) VALUES (%s, '2026-01', 'Nieznany')",
                    (firma,),
                )
        finally:
            conn.close()

    def test_pelnomocnictwa_type_is_restricted(self):
        firma = self.make_company()
        emp = self.make_employee()
        conn = get_conn()
        try:
            with self.assertRaises(psycopg.IntegrityError):
                conn.execute(
                    "INSERT INTO pelnomocnictwa (firma_id, pracownik_id, typ) VALUES (%s, %s, 'XYZ')",
                    (firma, emp),
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
