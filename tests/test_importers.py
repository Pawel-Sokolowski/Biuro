import os
import unittest

from openpyxl import Workbook

from app.db.dao_conn import get_conn
from app.utils.importers import import_companies_from_excel_file
from app.utils.parsing import normalize_identifier, parse_date_value, split_multi_value
from tests.base import DbTestCase

HEADERS = [
    "NIP", "REGON", "KRS", "PESEL", "Nazwa Firmy", "Nazwa skrócona", "Forma działalności",
    "Forma opodatkowania", "Analityka FK", "Osoba kontaktowa", "Adres email", "Nr telefonu",
    "Miasto", "Kod pocztowy", "Ulica", "Adres zamieszkania - miasto",
    "Adres zamieszkania - kod pocztowy", "Adres zamieszkania - ulica",
    "Adres korespondencyjny - miasto", "Adres korespondencyjny - kod pocztowy",
    "Adres korespondencyjny - ulica", "Przypisani pracownicy",
    "Data rozpoczęcia działalności gospodarczej", "Data rozpoczęcia sprzedaży dla osoby fizycznej",
    "Data zakończenia ulgi ZUS na start", "Data zakończenia ZUS preferencyjny",
    "Data zakończenia małego ZUS PLUS", "Data zakończenia A1", "Data rozpoczęcia obsługi",
    "Data zakończenia obsługi", "Zawieszenia działalności", "Kody PKD",
    "Cenniki usług księgowych", "Cenniki usług kadrowych", "Cenniki czasowe",
]

ROW = [
    "6741006281", "331102247", "", "", "3 PROJEKTY NORBERT KUNDERA", "Kundera Norbert ",
    "Osoba fizyczna (KPiR)", "Skala podatkowa", "", "Norbert Kundera", "norbert.kundera@wp.pl",
    "500320800", "Drawsko Pomorskie", "78-500", "ul. Królewiecka 21", "Drawsko Pomorskie",
    "78-500", "ul. Królewiecka 21", "Drawsko Pomorskie", "78-500", "ul. Królewiecka 21", "",
    "2000-12-20", "", "", "", "", "", "2024-09-01", "", "", "7112Z, 6820Z",
    "Cena ponad limit 20 szt. 5,5 zł, Ryczałt 412,50 netto", "", "",
]


class ParsingTests(unittest.TestCase):
    def test_leading_zeros_restored(self):
        self.assertEqual(normalize_identifier("674100628", {10}), "0674100628")
        self.assertEqual(normalize_identifier(331102247, {9, 14}), "331102247")

    def test_multi_value_split(self):
        self.assertEqual(split_multi_value("7112Z, 6820Z"), ["7112Z", "6820Z"])

    def test_date_parsing(self):
        self.assertEqual(str(parse_date_value("2024-09-01"))[:10], "2024-09-01")


class ExcelImportTests(DbTestCase):
    def _make_workbook(self):
        wb = Workbook()
        ws = wb.active
        ws.append(HEADERS)
        ws.append(ROW)
        path = os.path.join(self._tmp, "firmy.xlsx")
        wb.save(path)
        return path

    def test_every_column_of_the_sheet_is_imported(self):
        path = self._make_workbook()
        import_companies_from_excel_file(path, self.db_path)

        conn = get_conn()
        row = conn.execute("SELECT * FROM firmy WHERE nip = '6741006281'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        expected = {
            "regon": "331102247",
            "nazwa": "3 PROJEKTY NORBERT KUNDERA",
            "nazwa_skrocona": "Kundera Norbert",
            "forma_dzialalnosci": "Osoba fizyczna (KPiR)",
            "forma_opodatkowania": "Skala podatkowa",
            "osoba_kontaktowa": "Norbert Kundera",
            "adres_email": "norbert.kundera@wp.pl",
            "nr_telefonu": "500320800",
            "miasto": "Drawsko Pomorskie",
            "kod_pocztowy": "78-500",
            "adres_zam_miasto": "Drawsko Pomorskie",
            "adres_zam_kod_pocztowy": "78-500",
            "adres_koresp_miasto": "Drawsko Pomorskie",
            "adres_koresp_kod_pocztowy": "78-500",
            "data_rozpoczecia_dg": "2000-12-20",
            "data_rozpoczecia_obslugi": "2024-09-01",
        }
        for column, value in expected.items():
            self.assertEqual(str(row[column]).strip(), value, column)
        self.assertIn("Królewiecka", row["ulica"])
        self.assertIn("Królewiecka", row["adres_zam_ulica"])
        self.assertIn("Królewiecka", row["adres_koresp_ulica"])
        self.assertIn("7112Z", row["kody_pkd"])
        self.assertIn("6820Z", row["kody_pkd"])
        self.assertIn("412,50", row["cennik_ksiegowy"])

    def test_reimport_does_not_duplicate_company(self):
        path = self._make_workbook()
        import_companies_from_excel_file(path, self.db_path)
        import_companies_from_excel_file(path, self.db_path)
        conn = get_conn()
        count = conn.execute("SELECT COUNT(*) FROM firmy WHERE nip = '6741006281'").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
