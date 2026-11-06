import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.db.dao_conn import get_conn
from app.reports.import_dao import build_profitability_report
from tests.base import DbTestCase

MONTH = "2026-10"


class LiveDataReportTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.emp = self.make_employee("raport@example.com", "Anna", "Test")
        self.firma = self.make_company("Firma Raportowa")
        conn = get_conn()
        try:
            conn.execute("UPDATE pracownicy SET stawka_godzinowa = 60 WHERE id = %s", (self.emp,))
            conn.execute(
                "INSERT INTO czasomierze (firma_id, pracownik_id, miesiac, typ, rozpoczecie, zakonczenie, sekundy) "
                "VALUES (%s, %s, %s, 'ksiegowosc', '2026-10-05 10:00:00', '2026-10-05 12:00:00', 7200)",
                (self.firma, self.emp, MONTH),
            )
            positions = [{"nazwa": "Usługa księgowa", "netto": 300.0, "kategoria": "księgowość"}]
            conn.execute(
                "INSERT INTO faktury (firma_id, miesiac, status, rodzaj, pozycje_json, kwota_netto, kwota_brutto) "
                "VALUES (%s, %s, 'robocza', 'faktura', %s, 300, 369)",
                (self.firma, MONTH, json.dumps(positions)),
            )
            conn.commit()
        finally:
            conn.close()

    def test_report_includes_live_timer_and_invoice_data(self):
        rows = build_profitability_report([self.firma], [MONTH])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["sekundy"], 7200)
        self.assertEqual(row["cena"], 300.0)
        self.assertEqual(row["koszt"], 120.0)

    def test_imported_month_is_not_counted_twice(self):
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO import_czas_pracy (firma_id, data, miesiac, pracownik_nazwa, dzial, sekundy, hash) "
                "VALUES (%s, '2026-10-02', %s, 'Anna Test', 'ksiegowosc', 3600, 'h1')",
                (self.firma, MONTH),
            )
            conn.commit()
        finally:
            conn.close()
        rows = build_profitability_report([self.firma], [MONTH])
        self.assertEqual(sum(r["sekundy"] for r in rows), 3600)

    def test_payroll_service_by_accountant_stays_in_payroll_in_all_reports(self):
        from app.reports.reports_logic import build_report, build_summary_report, get_hours_for_company_month, build_profit_report, compare_companies
        with get_conn() as conn:
            service = conn.execute("INSERT INTO uslugi_dodatkowe(nazwa, cena_netto, stawka_vat, kategoria) "
                "VALUES ('Świadectwo pracy', 100, '23%', 'kadry') RETURNING id").fetchone()[0]
            conn.execute("INSERT INTO czasomierze(firma_id, pracownik_id, miesiac, typ, usluga_id, sekundy, rozpoczecie, zakonczenie) "
                         "VALUES (%s, %s, %s, 'dodatkowe', %s, 1800, '2026-10-06 10:00:00', '2026-10-06 10:30:00')", (self.firma, self.emp, MONTH, service))
            conn.execute("INSERT INTO uslugi_dodatkowe_firma(firma_id, miesiac, usluga_id, ilosc, cena_netto, fakturowac) "
                         "VALUES (%s, %s, %s, 1, 100, 1)", (self.firma, MONTH, service))
            position = dict(nazwa='Świadectwo pracy', netto=100, usluga_id=service, kategoria='kadry')
            conn.execute("UPDATE faktury SET pozycje_json = %s WHERE firma_id = %s", (
                json.dumps([dict(nazwa='Usługa księgowa', netto=300), position]), self.firma))
            conn.commit()
        rows = build_profitability_report([self.firma], [MONTH])
        payroll = [r for r in rows if r['sekcja'] == 'kadry']
        self.assertEqual(len(payroll), 1)
        self.assertEqual((payroll[0]['sek_dodatkowy'], payroll[0]['cena_dodatkowa'], payroll[0]['koszt']), (1800, 100, 30))
        accounting = [r for r in rows if r['sekcja'] == 'ksiegowosc']
        self.assertEqual([(r['sekundy'], r['cena']) for r in accounting], [(7200, 300)])
        monthly = build_report([self.firma], [MONTH])[self.firma][MONTH]
        self.assertEqual((monthly['koszt_kadry'], monthly['koszt_ksiegowosc']), (100, 0))
        self.assertEqual((monthly['godziny_kadry'], monthly['godziny_ksiegowosc']), (1800, 0))
        self.assertEqual(get_hours_for_company_month(self.firma, MONTH)['kadry'], 1800)
        self.assertEqual(build_profit_report([self.firma], [MONTH])[self.firma][MONTH]['profit_kadry'], 200)
        self.assertEqual(compare_companies([self.firma], MONTH)[self.firma]['koszt_kadry'], 100)
        summary = build_summary_report([self.firma], [MONTH])
        self.assertEqual({r['typ'] for r in summary}, {'ksiegowosc', 'kadry'})
        self.assertEqual(next(r for r in summary if r['typ'] == 'kadry')['sekundy'], 1800)
        # Legacy timers may already have counted the payroll service as accounting.
        from app.db.dao_monthly import save_month_status
        with get_conn() as conn:
            conn.execute("UPDATE czasomierze SET typ = 'ksiegowosc' WHERE usluga_id = %s", (service,))
            conn.commit()
        save_month_status(self.firma, MONTH, dict(sekundy_ksiegowosc=9000))
        monthly = build_report([self.firma], [MONTH])[self.firma][MONTH]
        self.assertEqual((monthly['godziny_ksiegowosc'], monthly['godziny_kadry']), (7200, 1800))


if __name__ == "__main__":
    unittest.main()
