import os
import unittest

from app.utils.import_assignments import choose_roles, employee_code, import_role_assignments_from_file
from tests.base import DbTestCase


class RoleRulesTests(unittest.TestCase):
    def test_code(self):
        self.assertEqual(employee_code("Katarzyna", "Czarnecka"), "CZK")
        self.assertEqual(employee_code("Łukasz", "Śliwa"), "SLL")

    def test_rules(self):
        self.assertEqual(choose_roles(["SOM"]), ("SOM", "SOM"))
        self.assertEqual(choose_roles(["CZK", "PUK"]), ("PUK", "CZK"))
        self.assertEqual(choose_roles(["CZK", "PUK", "SOM"]), ("SOM", "CZK"))
        self.assertEqual(choose_roles(["PUK", "SOM", "STW"]), ("SOM", None))
        self.assertEqual(choose_roles([]), (None, None))


class ImportAssignmentsTests(DbTestCase):
    def test_import(self):
        ids = {
            "CZK": self.make_employee("c@x.pl", "Katarzyna", "Czarnecka"),
            "SOM": self.make_employee("s@x.pl", "Marta", "Sowa"),
            "PUK": self.make_employee("p@x.pl", "Karol", "Pukowski"),
        }
        a = self.make_company("Alfa", nip="1234563218")
        b = self.make_company("Beta")
        path = os.path.join(self._tmp, "p.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Nazwa firmy;NIP;CZK;PUK;SOM;sp. z o.o.\n")
            f.write("Alfa;123-456-32-18;TAK;TAK;TAK;\n")
            f.write("Beta;;;;tak;\n")
            f.write("Gamma;;tak;;;\n")
        res = import_role_assignments_from_file(path)
        self.assertEqual(res["updated"], 2)
        self.assertEqual(len(res["warnings"]), 1)
        from app.db.dao_conn import get_conn
        conn = get_conn()
        try:
            ra = conn.execute("SELECT ksiegowa_id, kadrowa_id FROM firmy WHERE id = %s", (a,)).fetchone()
            rb = conn.execute("SELECT ksiegowa_id, kadrowa_id FROM firmy WHERE id = %s", (b,)).fetchone()
        finally:
            conn.close()
        self.assertEqual((ra["ksiegowa_id"], ra["kadrowa_id"]), (ids["SOM"], ids["CZK"]))
        self.assertEqual((rb["ksiegowa_id"], rb["kadrowa_id"]), (ids["SOM"], ids["SOM"]))


if __name__ == "__main__":
    unittest.main()
