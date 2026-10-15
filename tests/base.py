import os
import shutil
import tempfile
import unittest

from app.config import get_database, set_database
from app.db.schema import init_db
from app.security.bootstrap import create_initial_admin


class DbTestCase(unittest.TestCase):
    """Każdy test dostaje świeżą, pustą bazę SQLite utworzoną z aktualnego schema.py."""

    def setUp(self):
        self._previous_db = get_database()
        self._tmp = tempfile.mkdtemp(prefix="biuro_test_")
        self.db_path = os.path.join(self._tmp, "test.db")
        set_database(self.db_path)
        init_db()
        create_initial_admin('admin@biuro.pl', 'Admin12345')

    def tearDown(self):
        set_database(self._previous_db)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def make_employee(self, email="jan@example.com", imie="Jan", nazwisko="Kowalski", haslo="Haslo123"):
        from app.db.dao_employees import add_employee
        return add_employee(email, haslo, imie, nazwisko)

    def make_company(self, nazwa="Firma Testowa", **extra):
        from app.db.dao_companies import save_company
        return save_company({"nazwa": nazwa, **extra})
