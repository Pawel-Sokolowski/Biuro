import datetime
import unittest

from app.db.dao_conn import get_conn
from app.reports.reports_logic import build_daily_report
from app.reports.reports_ui import DAILY_HEADERS, daily_display_rows, daily_range
from tests.base import DbTestCase


class DailyReportTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.firma = self.make_company("Alfa Sp. z o.o.")
        self.a = self.make_employee("a@x.pl", "Anna", "Nowak")
        self.b = self.make_employee("b@x.pl", "Bartek", "Kowal")
        conn = get_conn()
        try:
            for emp, firma, typ, start, secs, opis in (
                (self.a, self.firma, "ksiegowosc", "2026-10-01T09:00:00", 3600, None),
                (self.a, self.firma, "kadry", "2026-10-01T11:00:00", 1800, None),
                (self.a, self.firma, "ksiegowosc", "2026-10-01T13:00:00", 600, None),
                (self.b, self.firma, "dodatkowe", "2026-10-01T10:00:00", 900, "Aktualizacja VAT-R"),
                (self.a, None, "ogolne", "2026-10-02T09:00:00", 1200, "Spotkanie"),
                (self.a, self.firma, "ksiegowosc", "2026-10-20T09:00:00", 300, None),
                (self.a, self.firma, "ksiegowosc", "2026-11-01T09:00:00", 300, None),
            ):
                conn.execute(
                    "INSERT INTO czasomierze (firma_id, pracownik_id, miesiac, typ, rozpoczecie, zakonczenie, sekundy, opis) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (firma, emp, start[:7], typ, start, start, secs, opis),
                )
            conn.commit()
        finally:
            conn.close()

    def rows(self, ids, start, end):
        return build_daily_report(ids, start, end, 1)

    def test_single_day_only_selected_employee(self):
        rows = self.rows([self.a], "2026-10-01", "2026-10-01")
        self.assertTrue(rows)
        self.assertTrue(all(r["pracownik"] == "Anna Nowak" and r["data"] == "2026-10-01" for r in rows))
        self.assertEqual(sum(r["sekundy"] for r in rows), 6000)

    def test_date_range_and_several_employees(self):
        rows = self.rows([self.a, self.b], "2026-10-01", "2026-10-03")
        self.assertTrue(any(r["pracownik"] == "Bartek Kowal" and r["czynnosc"] == "Aktualizacja VAT-R" for r in rows))
        self.assertTrue(any(r["data"] == "2026-10-02" and r["czynnosc"] == "Spotkanie" for r in rows))
        self.assertFalse(any(r["data"] == "2026-10-20" for r in rows))

    def test_whole_month_excludes_other_months(self):
        start, end = daily_range(datetime.date(2026, 10, 15), True)
        rows = self.rows([self.a], start, end)
        self.assertEqual((start, end), ("2026-10-01", "2026-10-31"))
        self.assertTrue(any(r["data"] == "2026-10-20" for r in rows))
        self.assertFalse(any(r["data"] == "2026-11-01" for r in rows))

    def test_no_employees_returns_nothing(self):
        self.assertEqual(self.rows([], "2026-10-01", "2026-10-31"), [])

    def test_monthly_counts_are_context_for_each_company_not_daily_production(self):
        from app.db.dao_monthly import save_month_status, save_month_contracts
        save_month_status(self.firma, '2026-10', {'ilosc_dok': 27})
        save_month_contracts(self.firma, '2026-10', 2, 3, 4)
        rows = self.rows([self.a], '2026-10-01', '2026-10-02')
        for row in rows:
            if row['firma_id'] is not None:
                self.assertEqual((row['miesiac'], row['dokumenty'], row['umowy']), ('2026-10', 27, 9))
            else:
                self.assertEqual((row['miesiac'], row['dokumenty'], row['umowy']), ('', None, None))

    def test_actual_document_count_precedes_manual_monthly_count(self):
        from app.db.dao_monthly import save_month_status
        save_month_status(self.firma, '2026-10', {'ilosc_dok': 27})
        with get_conn() as conn:
            conn.execute("INSERT INTO dokumenty(firma_id, data_wyst) VALUES (%s, '2026-10-15')", (self.firma,))
        rows = self.rows([self.a], '2026-10-01', '2026-10-01')
        self.assertTrue(all(row['dokumenty'] == 1 for row in rows))

    def test_accounting_month_comes_from_timer_and_is_not_merged_with_work_date_month(self):
        from app.db.dao_monthly import save_month_status
        save_month_status(self.firma, '2026-09', {'ilosc_dok': 7})
        save_month_status(self.firma, '2026-10', {'ilosc_dok': 13})
        with get_conn() as conn:
            conn.execute("UPDATE czasomierze SET miesiac = '2026-09' WHERE rozpoczecie = '2026-10-01T09:00:00'")
        rows = [row for row in self.rows([self.a], '2026-10-01', '2026-10-01') if row['czynnosc'] == 'Księgowość']
        self.assertEqual({(row['miesiac'], row['dokumenty'], row['sekundy']) for row in rows},
                         {('2026-09', 7, 3600), ('2026-10', 13, 600)})

    def test_contract_history_carries_forward_and_zero_is_preserved(self):
        from app.db.dao_monthly import save_month_contracts
        save_month_contracts(self.firma, '2026-09', 2, 1, 3)
        save_month_contracts(self.firma, '2026-11', 10, 10, 10)
        rows = self.rows([self.a], '2026-10-01', '2026-10-01')
        self.assertTrue(all(row['umowy'] == 6 for row in rows))
        save_month_contracts(self.firma, '2026-10', 0, 0, 0)
        self.assertTrue(all(row['umowy'] == 0 for row in self.rows([self.a], '2026-10-01', '2026-10-01')))

    def test_document_average_excludes_zero_months_and_future_periods(self):
        from app.db.dao_monthly import save_month_status
        for month, count in [('2026-08', 0), ('2026-09', 10), ('2026-10', 30), ('2026-11', 500)]:
            save_month_status(self.firma, month, {'ilosc_dok': count})
        rows = self.rows([self.a], '2026-10-01', '2026-10-01')
        self.assertTrue(all(row['dokumenty_srednia'] == 20 for row in rows))
        self.assertTrue(all(row['dokumenty_srednia_miesiace'] == 2 for row in rows))

    def test_document_average_uses_actual_counts_once_per_month(self):
        from app.db.dao_monthly import save_month_status
        save_month_status(self.firma, '2026-09', {'ilosc_dok': 10})
        save_month_status(self.firma, '2026-10', {'ilosc_dok': 100})
        with get_conn() as conn:
            for day in ('2026-10-15', '2026-10-16'):
                conn.execute('INSERT INTO dokumenty(firma_id, data_wyst) VALUES (%s, %s)', (self.firma, day))
        rows = self.rows([self.a], '2026-10-01', '2026-10-01')
        self.assertTrue(all(row['dokumenty_srednia'] == 6 for row in rows))
        self.assertTrue(all(row['dokumenty_srednia_miesiace'] == 2 for row in rows))

    def test_missing_nonzero_months_have_no_average(self):
        rows = self.rows([self.a], '2026-10-01', '2026-10-01')
        self.assertTrue(all(row['dokumenty_srednia'] is None for row in rows))

    def test_identical_company_names_do_not_merge_work_or_monthly_counts(self):
        other = self.make_company('Other', nazwa_skrocona='Same')
        with get_conn() as conn:
            conn.execute("UPDATE firmy SET nazwa_skrocona = 'Same' WHERE id = %s", (self.firma,))
            conn.execute("INSERT INTO czasomierze(firma_id, pracownik_id, miesiac, typ, rozpoczecie, zakonczenie, sekundy) "
                         "VALUES (%s, %s, '2026-10', 'ksiegowosc', '2026-10-01T09:00:00', '2026-10-01T09:00:20', 20)", (other, self.a))
        rows = [row for row in self.rows([self.a], '2026-10-01', '2026-10-01') if row['czynnosc'] == 'Księgowość']
        self.assertEqual({row['firma_id'] for row in rows}, {self.firma, other})
        self.assertEqual(len(rows), 2)

    def test_regular_employee_cannot_request_other_employees_rows(self):
        rows = build_daily_report([self.a, self.b], '2026-10-01', '2026-10-01', self.a)
        self.assertTrue(rows)
        self.assertTrue(all(row['pracownik'] == 'Anna Nowak' for row in rows))


class DailyDisplayTests(unittest.TestCase):
    def test_blank_row_between_days_and_hidden_repeats(self):
        rows = [
            {"data": "2026-10-01", "pracownik": "A", "firma": "F", "czynnosc": "X", "sekundy": 3600},
            {"data": "2026-10-01", "pracownik": "A", "firma": "F", "czynnosc": "Y", "sekundy": 60},
            {"data": "2026-10-02", "pracownik": "A", "firma": "F", "czynnosc": "X", "sekundy": 60},
        ]
        out = daily_display_rows(rows)
        self.assertEqual(out[0][0], "01/10/2026")
        self.assertEqual(out[1][:2], ["", ""])
        self.assertEqual(out[2], [""] * len(DAILY_HEADERS))
        self.assertEqual(out[3][0], "02/10/2026")

    def test_monthly_context_is_in_the_display_and_export_columns(self):
        row = {'data': '2026-10-01', 'pracownik': 'A', 'firma': 'F', 'czynnosc': 'Kadry', 'sekundy': 3600,
               'miesiac': '2026-09', 'dokumenty_srednia': 12.5, 'umowy': 8}
        display = daily_display_rows([row])[0]
        self.assertEqual(len(display), len(DAILY_HEADERS))
        self.assertEqual(display[3], '2026-09')
        self.assertEqual(display[5:7], ['12.50', '8'])

    def test_december_and_single_day(self):
        self.assertEqual(daily_range(datetime.date(2026, 12, 5), True), ("2026-12-01", "2026-12-31"))
        self.assertEqual(daily_range(datetime.date(2026, 12, 5), False), ("2026-12-05", "2026-12-05"))


if __name__ == "__main__":
    unittest.main()
