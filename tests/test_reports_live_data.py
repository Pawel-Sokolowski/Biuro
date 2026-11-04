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


if __name__ == "__main__":
    unittest.main()
