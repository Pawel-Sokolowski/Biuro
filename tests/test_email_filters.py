import os
import sqlite3
import tempfile
import unittest

from app.config import set_database
from app.email.email_storage import get_email_filters_for_company


class EmailFilterTests(unittest.TestCase):
    def test_company_filter_is_scoped_to_company(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            set_database(path)
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE reguly_filtrow (
                    id INTEGER PRIMARY KEY, pracownik_id INTEGER, konto_id INTEGER,
                    firma_id INTEGER, od TEXT, do TEXT, nadawca_zawiera TEXT,
                    temat_zawiera TEXT, tresc_zawiera TEXT, folder_docelowy TEXT,
                    aktywna INTEGER
                );
                CREATE TABLE konta_email (id INTEGER PRIMARY KEY, email TEXT);
                INSERT INTO konta_email (id, email) VALUES (1, 'office@example.com');
                INSERT INTO reguly_filtrow
                    (id, konto_id, firma_id, od, folder_docelowy, aktywna)
                VALUES (1, 1, 7, 'client@example.com', 'Client', 1);
                INSERT INTO reguly_filtrow
                    (id, konto_id, firma_id, od, folder_docelowy, aktywna)
                VALUES (2, 1, 8, 'other@example.com', 'Other', 1);
                """
            )
            conn.commit()
            conn.close()
            rows = get_email_filters_for_company(7)
            self.assertEqual([row["id"] for row in rows], [1])
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
