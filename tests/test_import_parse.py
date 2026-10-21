import datetime
import unittest

from app.reports.import_parse import (
    classify_invoice_item,
    classify_work,
    compute_employee_report,
    compute_profitability,
    month_from_invoice_number,
    parse_invoice_rows,
    parse_work_rows,
)

WORK_HEADER = ("Firma", "NIP", "Użytkownik", "Miesiąc dokumentów", "Data", "Od", "Do",
               "Czas pracy (h:m:s)", "Czas pracy (min)", "Czas pauzy (h:m:s)",
               "Czas pauzy (min)", "Czynność", "Komentarz")
INV_HEADER = ("L.p.", "Nazwa kontrahenta", "NIP kontrahenta", "Nr faktury", "Pozycja faktury",
              "Indeks pozycji", "Wartość netto", "Waluta")


class ImportParseTests(unittest.TestCase):
    def test_work_classification(self):
        self.assertEqual(classify_work("Czasomierz ogólny", "Kadry/Płace/ZUS", "Katarzyna Czajkowska"), "kadry")
        self.assertEqual(classify_work("Rozmowy telefoniczne", "", "Katarzyna Czajkowska"), "kadry")
        self.assertEqual(classify_work("inne", "", "Katarzyna Czajkowska"), "inne_kadry")
        self.assertEqual(classify_work("inne", "", "Wiktoria Stanisławowska"), "inne_ksiegowosc")
        self.assertEqual(classify_work("Rozmowy telefoniczne", "", "Wiktoria Stanisławowska"), "ksiegowosc")

    def test_invoice_classification(self):
        self.assertEqual(classify_invoice_item("Usługa księgowa"), "ksiegowosc")
        self.assertEqual(classify_invoice_item("Usługa kadrowo-płacowa - UoP"), "kadry")
        self.assertEqual(classify_invoice_item("rabat"), "rabat")
        self.assertEqual(classify_invoice_item("Cena ponad limit dokumentów 10 szt."), "ponad_limit")
        self.assertEqual(classify_invoice_item("zdrowotna właściciela"), "zdrowotna")

    def test_month_from_number(self):
        self.assertEqual(month_from_invoice_number("FV 3/2026"), "2026-03")
        self.assertEqual(month_from_invoice_number("FV/0001/01/2026"), "2026-01")
        self.assertEqual(month_from_invoice_number("FV/0123/12/2026"), "2026-12")
        self.assertEqual(month_from_invoice_number("2026/11/5"), "2026-11")
        self.assertIsNone(month_from_invoice_number("FV 15"))

    def test_parse_work_rows_skips_before_2026(self):
        rows = [
            WORK_HEADER,
            ("Czasomierz ogólny", "Czasomierz ogólny", "Katarzyna Czajkowska", "", "2026-01-05",
             "08:31", "08:36", "0:04:12", 4, "0:00:00", 0, "Kadry/Płace/ZUS", ""),
            ("FirmaB", "123-456-78-90", "Jan Kowalski", "", datetime.date(2025, 12, 5),
             "08:00", "08:10", "0:10:00", 10, "0:00:00", 0, "x", ""),
        ]
        parsed = parse_work_rows(rows)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["sekundy"], 252)
        self.assertEqual(parsed[0]["dzial"], "kadry")
        self.assertEqual(parsed[0]["miesiac"], "2026-01")

    def test_parse_invoice_rows_default_month(self):
        rows = [INV_HEADER, (1, "FirmaB", "1234567890", "FV 15", "Usługa księgowa", "", "1 234,50", "PLN")]
        parsed = parse_invoice_rows(rows, "2026-02")
        self.assertEqual(parsed[0]["wartosc_netto"], 1234.5)
        self.assertEqual(parsed[0]["miesiac"], "2026-02")
        self.assertEqual(parsed[0]["nip"], "1234567890")

    def test_profitability(self):
        work = [{"firma_id": 1, "firma_nazwa": "B", "miesiac": "2026-01",
                 "pracownik_nazwa": "Jan Kowalski", "dzial": "ksiegowosc", "sekundy": 7200}]
        inv = [
            {"firma_id": 1, "kontrahent": "B", "miesiac": "2026-01", "rodzaj": "ksiegowosc", "wartosc_netto": 500},
            {"firma_id": 1, "kontrahent": "B", "miesiac": "2026-01", "rodzaj": "rabat", "wartosc_netto": -50},
        ]
        res = compute_profitability(work, inv, {"jan kowalski": 50})
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["przychod"], 450)
        self.assertEqual(res[0]["koszt"], 100)
        self.assertEqual(res[0]["zysk"], 350)
        self.assertEqual(res[0]["przychod_na_godzine"], 225)


    def test_employee_report(self):
        work = [
            {"firma_id": 1, "firma_nazwa": "B", "miesiac": "2026-01",
             "pracownik_nazwa": "Jan Kowalski", "dzial": "ksiegowosc", "sekundy": 3600},
            {"firma_id": 1, "firma_nazwa": "B", "miesiac": "2026-01",
             "pracownik_nazwa": "Ewa Nowak", "dzial": "inne_ksiegowosc", "sekundy": 3600},
            {"firma_id": 1, "firma_nazwa": "B", "miesiac": "2026-01",
             "pracownik_nazwa": "Katarzyna Czajkowska", "dzial": "kadry", "sekundy": 1800},
        ]
        inv = [
            {"firma_id": 1, "kontrahent": "B", "miesiac": "2026-01", "rodzaj": "ksiegowosc", "wartosc_netto": 500},
            {"firma_id": 1, "kontrahent": "B", "miesiac": "2026-01", "rodzaj": "rabat", "wartosc_netto": -100},
            {"firma_id": 1, "kontrahent": "B", "miesiac": "2026-01", "rodzaj": "kadry", "wartosc_netto": 200},
        ]
        rates = {"jan kowalski": 40, "ewa nowak": 60, "katarzyna czajkowska": 80}
        res = compute_employee_report(work, inv, rates, {("ksiegowosc", 1, "2026-01"): 12, ("kadry", 1, "2026-01"): 3})
        ks = [r for r in res if r["sekcja"] == "ksiegowosc"]
        kad = [r for r in res if r["sekcja"] == "kadry"]
        self.assertEqual(len(ks), 2)
        self.assertEqual(sum(r["cena"] for r in ks), 400)
        self.assertEqual({r["pracownik"]: r["koszt"] for r in ks}, {"Jan Kowalski": 40, "Ewa Nowak": 60})
        by = {r["pracownik"]: r for r in ks}
        self.assertEqual(by["Jan Kowalski"]["cena_glowna"], 400)
        self.assertEqual(by["Jan Kowalski"]["sek_glowny"], 3600)
        self.assertEqual(by["Ewa Nowak"]["sek_dodatkowy"], 3600)
        self.assertEqual(ks[0]["ilosc"], 12)
        self.assertEqual(len(kad), 1)
        self.assertEqual((kad[0]["cena"], kad[0]["koszt"], kad[0]["zysk"]), (200, 40, 160))
        self.assertEqual(kad[0]["ilosc"], 3)

    def test_annual_fee_spread_over_12_months(self):
        inv = [
            {"firma_id": 5, "kontrahent": "E", "miesiac": "2026-01", "rodzaj": "ksiegowosc", "wartosc_netto": 12000},
            {"firma_id": 5, "kontrahent": "E", "miesiac": "2026-03", "rodzaj": "inne", "wartosc_netto": 50},
        ]
        res = compute_employee_report([], inv, {}, months=["2026-02", "2026-03"])
        by = {r["miesiac"]: r for r in res}
        self.assertEqual(by["2026-02"]["cena_glowna"], 1000)
        self.assertEqual(by["2026-03"]["cena_glowna"], 1000)
        self.assertEqual(by["2026-03"]["cena_dodatkowa"], 50)

    def test_monthly_fees_not_spread(self):
        inv = [
            {"firma_id": 6, "kontrahent": "F", "miesiac": "2026-01", "rodzaj": "ksiegowosc", "wartosc_netto": 300},
            {"firma_id": 6, "kontrahent": "F", "miesiac": "2026-02", "rodzaj": "ksiegowosc", "wartosc_netto": 300},
        ]
        res = compute_employee_report([], inv, {})
        self.assertEqual([r["cena_glowna"] for r in res], [300, 300])

    def test_zdrowotna_without_kadry_time_goes_to_accounting(self):
        inv = [
            {"firma_id": 3, "kontrahent": "D", "miesiac": "2026-02", "rodzaj": "zdrowotna", "wartosc_netto": 70},
            {"firma_id": 3, "kontrahent": "D", "miesiac": "2026-01", "rodzaj": "zdrowotna", "wartosc_netto": 70},
        ]
        work = [{"firma_id": 3, "firma_nazwa": "D", "miesiac": "2026-01",
                 "pracownik_nazwa": "Katarzyna Czajkowska", "dzial": "kadry", "sekundy": 480}]
        res = compute_employee_report(work, inv, {})
        by = {(r["sekcja"], r["miesiac"]): r["cena"] for r in res}
        self.assertEqual(by[("ksiegowosc", "2026-02")], 70)
        self.assertEqual(by[("kadry", "2026-01")], 70)
        self.assertNotIn(("kadry", "2026-02"), by)

    def test_employee_report_price_without_work(self):
        inv = [{"firma_id": 2, "kontrahent": "C", "miesiac": "2026-02", "rodzaj": "ksiegowosc", "wartosc_netto": 300}]
        res = compute_employee_report([], inv, {})
        self.assertEqual(len(res), 1)
        self.assertEqual((res[0]["sekundy"], res[0]["cena"], res[0]["zysk"]), (0, 300, 300))


if __name__ == "__main__":
    unittest.main()
