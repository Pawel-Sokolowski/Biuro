import os
import shutil
import tempfile
import unittest
import uuid

from app.config import get_schema, set_schema
from app.db.dao_conn import close_pool, get_conn
from app.db.schema import init_db
from app.security.bootstrap import create_initial_admin


def create_test_schema() -> str:
    """Tworzy osobny schemat PostgreSQL dla testu i ustawia go jako search_path."""
    name = "test_" + uuid.uuid4().hex[:12]
    set_schema(None)
    conn = get_conn()
    try:
        conn.execute(f"CREATE SCHEMA {name}")
        conn.commit()
    finally:
        conn.close()
    set_schema(name)
    return name


def drop_test_schema(name: str, previous: str | None = None) -> None:
    close_pool(name)
    set_schema(None)
    conn = get_conn()
    try:
        conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
        conn.commit()
    finally:
        conn.close()
    set_schema(previous)


class DbTestCase(unittest.TestCase):
    """Ka?dy test dostaje ?wie?y, pusty schemat PostgreSQL utworzony z aktualnego schema.py."""

    def setUp(self):
        self._previous_schema = get_schema()
        self.schema_name = create_test_schema()
        self._tmp = tempfile.mkdtemp(prefix="biuro_test_")
        self.db_path = os.path.join(self._tmp, "test.db")
        init_db()
        create_initial_admin('admin@biuro.pl', 'Admin12345')

    def tearDown(self):
        drop_test_schema(self.schema_name, self._previous_schema)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def make_employee(self, email="jan@example.com", imie="Jan", nazwisko="Kowalski", haslo="Haslo123"):
        from app.db.dao_employees import add_employee
        return add_employee(email, haslo, imie, nazwisko)

    def make_company(self, nazwa="Firma Testowa", **extra):
        from app.db.dao_companies import save_company
        return save_company({"nazwa": nazwa, **extra})
