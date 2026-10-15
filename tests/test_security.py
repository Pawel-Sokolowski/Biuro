import unittest

from app.companies.company_logic import CompanyLogic
from app.db.dao_conn import get_conn
from app.security.bootstrap import create_initial_admin, needs_initial_admin
from app.security.authorization import is_administrator
from app.config import get_database, set_database
from app.db.schema import init_db
import os, shutil, tempfile


class InitialAdminTests(unittest.TestCase):
    def setUp(self):
        self._prev = get_database()
        self._tmp = tempfile.mkdtemp(prefix="biuro_admin_")
        set_database(os.path.join(self._tmp, "t.db"))
        init_db()

    def tearDown(self):
        set_database(self._prev)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_no_default_account_and_first_admin_flow(self):
        self.assertTrue(needs_initial_admin())
        with self.assertRaises(ValueError):
            create_initial_admin("a@b.pl", "krotki")
        admin_id = create_initial_admin("a@b.pl", "DlugieHaslo1")
        self.assertTrue(is_administrator(admin_id))
        self.assertFalse(needs_initial_admin())
        with self.assertRaises(PermissionError):
            create_initial_admin("x@y.pl", "DlugieHaslo1")


class CompanyLogicWhitelistTests(unittest.TestCase):
    def setUp(self):
        self._prev = get_database()
        self._tmp = tempfile.mkdtemp(prefix="biuro_wl_")
        set_database(os.path.join(self._tmp, "t.db"))
        init_db()

    def tearDown(self):
        set_database(self._prev)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_unknown_keys_are_ignored(self):
        logic = CompanyLogic()
        fid = logic.create_company({"nazwa": "A", "id) VALUES (1); DROP TABLE firmy; --": "x"})
        logic.save_company(fid, {"nazwa": "B", "zly = 1, nazwa": "x"})
        conn = get_conn()
        try:
            self.assertEqual(conn.execute("SELECT nazwa FROM firmy WHERE id=?", (fid,)).fetchone()[0], "B")
        finally:
            conn.close()
