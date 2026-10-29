"""Regresja: raporty księgowości liczą dokumenty niezależnie od limitu firmy."""
import unittest

from app.db.dao_conn import get_conn
from app.db.dao_monthly import save_month_status
from app.reports.reports_logic import build_report, build_yearly_report
from tests.base import DbTestCase


class AccountingDocumentReportsTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.firma_bez_limitu = self.make_company("Bez limitu", limit_dokumentow=0)
        self.firma_z_limitem = self.make_company("Z limitem", limit_dokumentow=5)

    def _status(self, firma_id, month, documents, seconds=0):
        save_month_status(
            firma_id, month,
            {"ilosc_dok": documents, "sekundy_ksiegowosc": seconds},
        )

    def test_without_limit_monthly_view_count_is_in_accounting_report(self):
        self._status(self.firma_bez_limitu, "2026-01", 13, 7200)
        row = build_report([self.firma_bez_limitu], ["2026-01"])[self.firma_bez_limitu]["2026-01"]
        self.assertEqual(row["dokumenty"], 13)
        self.assertEqual(row["godziny_ksiegowosc"], 7200)

    def test_with_limit_show_total_documents_not_limit_or_overage(self):
        self._status(self.firma_z_limitem, "2026-02", 12)
        row = build_report([self.firma_z_limitem], ["2026-02"])[self.firma_z_limitem]["2026-02"]
        self.assertEqual(row["dokumenty"], 12)  # nie 5 i nie 7

    def test_actual_documents_take_priority_over_status_as_in_monthly_view(self):
        self._status(self.firma_bez_limitu, "2026-03", 25)
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO dokumenty (firma_id, data_wyst) VALUES (%s, '2026-03-10')",
                (self.firma_bez_limitu,),
            )
            conn.execute(
                "INSERT INTO dokumenty (firma_id, data_wyst) VALUES (%s, '2026-03-11')",
                (self.firma_bez_limitu,),
            )
            conn.commit()
        finally:
            conn.close()
        row = build_report([self.firma_bez_limitu], ["2026-03"])[self.firma_bez_limitu]["2026-03"]
        self.assertEqual(row["dokumenty"], 2)

    def test_counts_are_separate_by_company_and_month(self):
        self._status(self.firma_bez_limitu, "2026-04", 3)
        self._status(self.firma_z_limitem, "2026-04", 7)
        self._status(self.firma_bez_limitu, "2026-05", 4)

        data = build_report(
            [self.firma_bez_limitu, self.firma_z_limitem],
            ["2026-04", "2026-05"],
        )
        self.assertEqual(data[self.firma_bez_limitu]["2026-04"]["dokumenty"], 3)
        self.assertEqual(data[self.firma_bez_limitu]["2026-05"]["dokumenty"], 4)
        self.assertEqual(data[self.firma_z_limitem]["2026-04"]["dokumenty"], 7)
        self.assertEqual(data[self.firma_z_limitem]["2026-05"]["dokumenty"], 0)

    def test_annual_report_contains_monthly_view_counts(self):
        self._status(self.firma_bez_limitu, "2026-07", 33)
        report = build_yearly_report(self.firma_bez_limitu, 2026)
        self.assertEqual(len(report), 12)
        self.assertEqual(report["2026-07"]["dokumenty"], 33)
        self.assertEqual(report["2026-08"]["dokumenty"], 0)


if __name__ == "__main__":
    unittest.main()
