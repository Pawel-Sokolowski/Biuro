import unittest

from app.db.dao_conn import get_conn
from app.email.email_storage import get_email_filters_for_company
from tests.base import DbTestCase


class EmailFilterTests(DbTestCase):
    def test_company_filter_is_scoped_to_company(self):
        f7 = self.make_company("A")
        f8 = self.make_company("B")
        conn = get_conn()
        try:
            konto = conn.execute(
                "INSERT INTO konta_email (email) VALUES ('office@example.com') RETURNING id"
            ).fetchone()[0]
            ids = []
            for firma, od, folder in ((f7, "client@example.com", "Client"), (f8, "other@example.com", "Other")):
                ids.append(conn.execute(
                    "INSERT INTO reguly_filtrow (konto_id, firma_id, od, folder_docelowy, aktywna) "
                    "VALUES (%s, %s, %s, %s, 1) RETURNING id", (konto, firma, od, folder),
                ).fetchone()[0])
            conn.commit()
        finally:
            conn.close()
        rows = get_email_filters_for_company(f7)
        self.assertEqual([row["id"] for row in rows], [ids[0]])


if __name__ == "__main__":
    unittest.main()
