import unittest
from datetime import date, timedelta

from app.db.dao_companies import get_company
from app.db.dao_monthly import get_month_status
from app.monthly_view.monthly_view_logic import MonthlyViewLogic
from app.utils import ceidg_suspension as cs
from tests.base import DbTestCase


class CeidgSuspensionTests(DbTestCase):
    def _company(self, nazwa, nip, status="Aktywne"):
        from app.db.dao_companies import save_company
        return save_company({"nazwa": nazwa, "nip": nip, "status": status})

    def test_marks_only_suspended(self):
        a = self._company("A", "1234563218")
        b = self._company("B", "5260001246")
        c = self._company("C", "bad")
        answers = {
            "1234563218": {"status": "ZAWIESZONY", "data_zawieszenia": "2026-03-01"},
            "5260001246": {"status": "AKTYWNY"},
        }
        marked = cs.check_suspensions(lambda nip: answers[nip])
        self.assertEqual(marked, [a])
        self.assertEqual(get_company(a)["status"], "Zawieszone")
        self.assertEqual(get_company(b)["status"], "Aktywne")
        self.assertEqual(get_company(c)["status"], "Aktywne")

    def test_errors_are_skipped_and_inactive_not_queried(self):
        self._company("A", "1234563218")
        self._company("Z", "5260001246", status="Archiwalne")
        asked = []

        def fetch(nip):
            asked.append(nip)
            raise RuntimeError("boom")

        self.assertEqual(cs.check_suspensions(fetch), [])
        self.assertEqual(asked, ["1234563218"])

    def test_is_check_due(self):
        self.assertTrue(cs.is_check_due(date(2026, 5, 10)))
        cs.set_setting(cs.LAST_RUN_KEY, date(2026, 5, 10).isoformat())
        self.assertFalse(cs.is_check_due(date(2026, 5, 10)))
        self.assertFalse(cs.is_check_due(date(2026, 5, 12)))
        self.assertTrue(cs.is_check_due(date(2026, 5, 31)))
        self.assertTrue(cs.is_check_due(date(2026, 5, 10) + timedelta(days=16)))


class DeliveredDateTests(DbTestCase):
    def test_date_defaults_to_today_and_can_change(self):
        firma = self.make_company()
        logic = MonthlyViewLogic.__new__(MonthlyViewLogic)
        logic.aktualny_miesiac = "2026-01"
        logic.save_status_for_company(firma, dokumenty_dostarczone=1)
        self.assertEqual(get_month_status(firma, "2026-01")["data_dostarczenia"], date.today().isoformat())
        logic.save_status_for_company(firma, dokumenty_dostarczone=1, data_dostarczenia="2026-01-05")
        self.assertEqual(get_month_status(firma, "2026-01")["data_dostarczenia"], "2026-01-05")
        logic.save_status_for_company(firma, dokumenty_dostarczone=0)
        self.assertIsNone(get_month_status(firma, "2026-01")["data_dostarczenia"])

    def test_model_shows_date_in_docs_cell(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from app.monthly_view.monthly_view_model import MonthlyViewModel
        m = MonthlyViewModel([{"id": 1, "nazwa": "A", "dokumenty_dostarczone": 1,
                               "data_dostarczenia": "2026-01-05"}], "ksiegowosc")
        self.assertEqual(m.data(m.index(0, 1), Qt.ItemDataRole.DisplayRole), "05.01.2026")


if __name__ == "__main__":
    unittest.main()
