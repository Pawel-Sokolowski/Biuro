import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from tests.base import DbTestCase


class CompanyBatchTests(DbTestCase):
    def test_assignees_sync_to_pracownik_firma(self):
        from app.db.dao_companies import save_company, get_company_employees
        from app.db.dao_conn import get_conn

        conn = get_conn()
        conn.execute("INSERT INTO pracownicy (imie, nazwisko, email, haslo_hash, aktywny) VALUES ('A','B','a@x.pl','h',1)")
        conn.execute("INSERT INTO pracownicy (imie, nazwisko, email, haslo_hash, aktywny) VALUES ('C','D','c@x.pl','h',1)")
        ids = [r[0] for r in conn.execute("SELECT id FROM pracownicy ORDER BY id").fetchall()][-2:]
        conn.commit()
        conn.close()

        fid = save_company({"nazwa": "Test", "nip": "1234567890", "ksiegowa_id": ids[0], "kadrowa_id": ids[1]})
        self.assertEqual({e["id"] for e in get_company_employees(fid)}, set(ids))

        save_company({"id": fid, "nazwa": "Test", "ksiegowa_id": 0, "kadrowa_id": ids[1]})
        self.assertEqual({e["id"] for e in get_company_employees(fid)}, {ids[1]})

    def test_krs_parse(self):
        from app.companies.krs_api import parse_odpis
        data = {"odpis": {"naglowekA": {"numerKRS": "0000123456"}, "dane": {
            "dzial1": {
                "danePodmiotu": {"nazwa": "ABC SP. Z O.O.", "identyfikatory": {"nip": "1111111111", "regon": "222222222"}},
                "siedziba": {"wojewodztwo": "POMORSKIE", "powiat": "gdański", "gmina": "Gdańsk"},
                "siedzibaIAdres": {"adres": {"ulica": "ul. Długa", "nrDomu": "5", "nrLokalu": "2",
                                             "miejscowosc": "Gdańsk", "kodPocztowy": "80001"}},
            },
            "dzial3": {"przedmiotDzialalnosci": {"przedmiotPrzewazajacejDzialalnosci": [
                {"kodDzial": "62", "kodKlasa": "01", "kodPodklasa": "Z"}]}},
        }}}
        r = parse_odpis(data)
        self.assertEqual(r["nip"], "1111111111")
        self.assertEqual(r["dz_kod"], "80-001")
        self.assertEqual(r["dz_ulica"], "ul. Długa 5/2")
        self.assertEqual(r["pkd"], ["6201Z"])

    def test_gus_parse_and_missing_key(self):
        from app.companies import gus_api
        r = gus_api.parse_search_result(
            "<root><dane><Regon>123</Regon><Nip>999</Nip><Nazwa>X</Nazwa><Miejscowosc>Y</Miejscowosc>"
            "<KodPocztowy>78500</KodPocztowy><Ulica>ul. Z</Ulica><NrNieruchomosci>1</NrNieruchomosci></dane></root>")
        self.assertEqual(r["dz_kod"], "78-500")
        old = os.environ.pop("GUS_API_KEY", None)
        try:
            self.assertTrue(gus_api.get_gus_firma_by_nip("6741006281")["error"])
        finally:
            if old is not None:
                os.environ["GUS_API_KEY"] = old

    def test_form_pesel_required_copy_and_wheel(self):
        app = QApplication.instance() or QApplication([])
        from app.companies.company_form import CompanyForm
        f = CompanyForm()
        f.inputs["nazwa"].setText("X")
        f.inputs["forma_dzialalnosci"].setCurrentText("JDG")
        self.assertIn("PESEL", f._validate())
        f.inputs["pesel"].setText("12345678901")
        self.assertIsNone(f._validate())
        self.assertGreater(f.inputs["miesiac_dok_fakturowanie"].count(), 1)

        f.inputs["dz_miasto"].setText("Gdynia")
        f.addr_copy_from.setCurrentIndex(0)
        f.addr_copy_to.setCurrentIndex(1)
        f._copy_address()
        self.assertEqual(f.inputs["ko_miasto"].text(), "Gdynia")

    def test_checkboxes_persist_after_save_and_reopen(self):
        app = QApplication.instance() or QApplication([])
        from app.companies.company_form import CompanyForm
        from app.db.dao_companies import save_company
        fid = save_company({"nazwa": "Chk", "wliczac_zdrowotna": 1, "fakturowac": 1, "metoda_kasowa": 1})
        f = CompanyForm(fid)
        self.assertTrue(f.inputs["wliczac_zdrowotna"].isChecked())
        self.assertTrue(f.inputs["fakturowac"].isChecked())
        self.assertTrue(f.inputs["metoda_kasowa"].isChecked())
        self.assertFalse(f.inputs["wysylac_do_ksef"].isChecked())

    def test_zdrowotna_on_monthly_invoice(self):
        from app.db.dao_companies import save_company
        from app.db.dao_conn import get_conn
        from app.db.dao_invoices import generate_monthly_invoices_for_month
        import sqlite3
        conn = get_conn()
        conn.execute("INSERT INTO cenniki (klucz, cena_netto_ksiegowosc, netto, brutto) VALUES ('K1', 300, 300, 369)")
        conn.commit()
        conn.close()
        with_h = save_company({"nazwa": "Z1", "glowny_cennik": "K1", "fakturowac": 1,
                               "zdrowotna": 60, "wliczac_zdrowotna": 1})
        without = save_company({"nazwa": "Z2", "glowny_cennik": "K1", "fakturowac": 1,
                                "zdrowotna": 60, "wliczac_zdrowotna": 0})
        generate_monthly_invoices_for_month("2026-09")
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        netto = {r["firma_id"]: r["kwota_netto"] for r in conn.execute(
            "SELECT firma_id, kwota_netto FROM faktury WHERE miesiac = '2026-09'")}
        conn.close()
        self.assertEqual(netto[with_h], 360.0)
        self.assertEqual(netto[without], 360.0)
        import json
        conn = get_conn()
        cnt = {r[0]: len(json.loads(r[1])) for r in conn.execute(
            "SELECT firma_id, pozycje_json FROM faktury WHERE miesiac = '2026-09'")}
        conn.close()
        self.assertEqual(cnt[with_h], 1)
        self.assertEqual(cnt[without], 2)

    def test_monthly_columns(self):
        app = QApplication.instance() or QApplication([])
        from app.monthly_view.monthly_view_model import MonthlyViewModel
        m = MonthlyViewModel([{"id": 1, "nazwa": "A", "etap": "Brak", "ilosc_dok": 4}], "ksiegowosc")
        heads = [m.headerData(i, m.headerData.__self__ and __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.Orientation.Horizontal) for i in range(4)]
        self.assertEqual(heads, ["Firma", "Dostarczone", "Etap", "Ilość dokumentów"])


if __name__ == "__main__":
    unittest.main()

