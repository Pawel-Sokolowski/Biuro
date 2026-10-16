import unittest
from app.security.authorization import is_administrator
from app.security.bootstrap import create_initial_admin
from app.companies.company_logic import CompanyLogic
from tests.base import DbTestCase


class AdminSurvivesTests(DbTestCase):
    def test_admin_kept_after_company_reassign_and_delete(self):
        from app.db.dao_conn import get_conn
        conn = get_conn(); conn.execute("DELETE FROM pracownik_firma"); conn.execute("DELETE FROM pracownicy"); conn.commit(); conn.close()
        admin = create_initial_admin("a@b.pl", "haslo1234")
        self.assertTrue(is_administrator(admin))
        CompanyLogic().assign_employees(1, [])
        conn = get_conn(); conn.execute("DELETE FROM pracownik_firma"); conn.commit(); conn.close()
        self.assertTrue(is_administrator(admin))
        emp = self.make_employee()
        self.assertFalse(is_administrator(emp))
        from app.db.dao_global_settings import assign_role, get_user_role, get_user_permissions
        assign_role(emp, 3)
        conn = get_conn(); conn.execute("DELETE FROM pracownik_firma"); conn.commit(); conn.close()
        self.assertEqual(get_user_role(emp), 3)
        self.assertFalse(is_administrator(emp))
        assign_role(emp, 4)
        self.assertTrue(is_administrator(emp))
        assign_role(emp, 1)
        self.assertFalse(is_administrator(emp))


if __name__ == "__main__":
    unittest.main()
