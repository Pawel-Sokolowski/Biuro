import os
import unittest
from unittest import mock
from datetime import datetime

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
    "Cenniki usług księgowych", "Cenniki usług kadrowych", "Cenniki czasowe", "Urz\u0105d Skarbowy", "Rachunek bankowy",
]

ROW = [
    "8522605060", "321442130", "", "", "BIURO RACHUNKOWE EM-KA SP. Z O.O.", "EM-KA ",
    "Osoba fizyczna (KPiR)", "Skala podatkowa", "", "Jan Kowalski", "biuro@example.com",
    "500320800", "Drawsko Pomorskie", "78-500", "ul. Królewiecka 21", "Drawsko Pomorskie",
    "78-500", "ul. Królewiecka 21", "Drawsko Pomorskie", "78-500", "ul. Królewiecka 21", "",
    "2000-12-20", "", "", "", "", "", "2024-09-01", "", "", "7112Z, 6820Z",
    "Cena ponad limit 20 szt. 5,5 zł, Ryczałt 412,50 netto", "", "", "Drugi Urz\u0105d Skarbowy w Szczecinie", "12 3456 7890",
]


class ParsingTests(unittest.TestCase):
    def test_leading_zeros_restored(self):
        self.assertEqual(normalize_identifier("674100628", {10}), "0674100628")
        self.assertEqual(normalize_identifier(321442130, {9, 14}), "321442130")

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
        row = conn.execute("SELECT * FROM firmy WHERE nip = '8522605060'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        expected = {
            "regon": "321442130",
            "nazwa": "BIURO RACHUNKOWE EM-KA SP. Z O.O.",
            "nazwa_skrocona": "EM-KA",
            "forma_dzialalnosci": "Osoba fizyczna (KPiR)",
            "forma_opodatkowania": "Skala podatkowa",
            "osoba_kontaktowa": "Jan Kowalski",
            "adres_email": "biuro@example.com",
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
        self.assertEqual(row["urzad_skarbowy"], "Drugi Urz\u0105d Skarbowy w Szczecinie")
        self.assertEqual(row["konto_bankowe"], "12 3456 7890")

    def test_reimport_does_not_duplicate_company(self):
        path = self._make_workbook()
        import_companies_from_excel_file(path, self.db_path)
        import_companies_from_excel_file(path, self.db_path)
        conn = get_conn()
        count = conn.execute("SELECT COUNT(*) FROM firmy WHERE nip = '8522605060'").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_same_name_with_another_nip_does_not_overwrite_existing_company(self):
        existing = self.make_company(ROW[4], nip='1234563218', adres_email='original@example.test')
        result = import_companies_from_excel_file(self._make_workbook())
        self.assertEqual((result['created'], result['updated']), (1, 0))
        with get_conn() as conn:
            original = conn.execute('SELECT nip, adres_email FROM firmy WHERE id = %s', (existing,)).fetchone()
        self.assertEqual((original['nip'], original['adres_email']), ('1234563218', 'original@example.test'))

    def test_blank_company_name_is_skipped_not_counted_as_an_update(self):
        wb = Workbook(); wb.active.append(['NIP', 'Nazwa Firmy']); wb.active.append(['8522605060', ''])
        path = os.path.join(self._tmp, 'empty-name.xlsx'); wb.save(path)
        result = import_companies_from_excel_file(path)
        self.assertEqual((result['created'], result['updated']), (0, 0))
        self.assertTrue(any('pustą nazwę' in w for w in result['warnings']))

    def test_master_excel_numeric_ids_dates_addresses_contact_and_assignments(self):
        employee = self.make_employee('excel@example.test', 'Anna', 'Importowa')
        values = dict(zip(HEADERS, ROW))
        values.update({'NIP': 674100628, 'KRS': 1234, 'PESEL': 1010112345,
                       'Przypisani pracownicy': 'Anna Importowa', 'Ulica': 'Długa 12/3',
                       'Data rozpoczęcia działalności gospodarczej': datetime(2020, 2, 3),
                       'Data rozpoczęcia sprzedaży dla osoby   fizycznej': datetime(2021, 3, 4),
                       'Rachunki walutowe': 'EUR: PL00123456789012345678901234'})
        values.pop('Data rozpoczęcia sprzedaży dla osoby fizycznej')
        wb = Workbook(); wb.active.append(list(values)); wb.active.append(list(values.values()))
        path = os.path.join(self._tmp, 'master.xlsx'); wb.save(path)
        from app.utils.importers import import_companies_fast_from_excel_file, import_companies_from_excel_file_payloads
        payloads, warnings = import_companies_from_excel_file_payloads(path)
        self.assertEqual(payloads[0]['data_rozpoczecia_sprzedazy'], '2021-03-04')
        self.assertTrue(any('Rachunki walutowe' in w for w in warnings))
        result = import_companies_from_excel_file(path)
        self.assertTrue(any('Rachunki walutowe' in w for w in result['warnings']))
        with mock.patch('app.utils.importers.enrich_payload', return_value=None), \
                mock.patch('app.utils.importers.check_ceidg_status_for_company', return_value='pominięto'), \
                mock.patch('app.utils.importers.check_vat_for_company'):
            repeated = import_companies_fast_from_excel_file(path)
        self.assertEqual((repeated['created'], repeated['updated']), (0, 1))
        self.assertTrue(any('Rachunki walutowe' in w for w in repeated['warnings']))
        with get_conn() as conn:
            firm = conn.execute("SELECT * FROM firmy WHERE nip = '0674100628'").fetchone()
            contacts = conn.execute('SELECT * FROM company_contacts WHERE firma_id = %s', (firm['id'],)).fetchall()
            assigned = conn.execute('SELECT pracownik_id FROM pracownik_firma WHERE firma_id = %s', (firm['id'],)).fetchall()
        self.assertEqual((firm['krs'], firm['pesel']), ('0000001234', '01010112345'))
        self.assertEqual((firm['ulica'], firm['adres_budynek'], firm['adres_lokal']), ('Długa', '12', '3'))
        self.assertEqual(str(firm['data_rozpoczecia_dg']), '2020-02-03')
        self.assertEqual(len(contacts), 1)
        self.assertEqual((contacts[0]['email'], contacts[0]['imie'], contacts[0]['nazwisko']), ('biuro@example.com', 'Jan', 'Kowalski'))
        self.assertIn(employee, [r['pracownik_id'] for r in assigned])


class ImportDetailsTests(DbTestCase):
    def test_csv_with_semicolons_and_decimal_commas(self):
        from app.utils.importers import import_companies_from_excel_file_payloads

        path = os.path.join(self._tmp, "firmy.csv")
        with open(path, "w", encoding="cp1250", newline="") as f:
            f.write("NIP;Nazwa Firmy;Cenniki usług księgowych;VAT\n")
            f.write("8522605060;Test;Ryczałt 230,00 netto, Cena ponad limit 5 szt. 5,5 zł;Tak\n")
        payloads, warnings = import_companies_from_excel_file_payloads(path)
        self.assertEqual(len(payloads), 1)
        parsed = payloads[0]["cennik_ksiegowy_parsed"]
        self.assertEqual(parsed["ryczalt_ksiegowy"], 230.0)
        self.assertEqual(parsed["limit_dokumentow"], 5)
        self.assertEqual(payloads[0]["vat_czynny"], 1)
        self.assertEqual(payloads[0]["glowny_cennik"], "KPIR_T_5_230")
        self.assertEqual(warnings, [])

    def test_unknown_column_is_reported(self):
        from app.utils.importers import import_companies_from_excel_file_payloads

        path = os.path.join(self._tmp, "firmy2.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("NIP;Nazwa Firmy;Coś nowego\n8522605060;Test;x\n")
        _, warnings = import_companies_from_excel_file_payloads(path)
        self.assertTrue(any("Coś nowego" in w for w in warnings))

    def test_wizard_payload_with_form_keys_keeps_address_vat_and_contact(self):
        from app.utils.importers import save_imported_payload

        payload = {
            "nazwa": "Wizard Sp.", "nip": "8522605060",
            "dz_miasto": "Szczecin", "dz_kod": "70-001", "dz_ulica": "Długa",
            "status_vat": "Czynny", "forma_opodatkowania": "Podatek liniowy",
            "forma_dzialalnosci": "Osoba fizyczna (KPiR)",
            "kont_imie": "Jan", "kont_nazwisko": "Kowalski",
            "kont_email": "jan@example.com", "kont_tel": "123",
            "cennik_ksiegowy": "Ryczałt do 350 zł 10 szt, Cena ponad limit 10 szt. 6,6 zł",
            "cennik_ksiegowy_parsed": {
                "limit_dokumentow": 10, "cena_za_dokument": 6.6,
                "ryczalt_ksiegowy": 350.0, "zdrowotna": None,
            },
        }
        save_imported_payload(payload, self.db_path)
        conn = get_conn()
        firm = conn.execute("SELECT * FROM firmy WHERE nip = '8522605060'").fetchone()
        contacts = conn.execute("SELECT * FROM company_contacts WHERE firma_id = %s", (firm["id"],)).fetchall()
        price = conn.execute("SELECT * FROM cenniki WHERE klucz = 'KPIR_T_10_350'").fetchone()
        conn.close()
        self.assertEqual(firm["miasto"], "Szczecin")
        self.assertEqual(firm["kod_pocztowy"], "70-001")
        self.assertEqual(firm["glowny_cennik"], "KPIR_T_10_350")
        self.assertIsNotNone(price)
        self.assertEqual([c["email"] for c in contacts], ["jan@example.com"])


if __name__ == "__main__":
    unittest.main()
