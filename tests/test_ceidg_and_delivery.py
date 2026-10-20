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

    def test_marks_only_suspended_and_moves_to_suspended(self):
        a = self._company("A", "1234563218")
        b = self._company("B", "5260001246")
        c = self._company("C", "bad")
        emp = self.make_employee()
        from app.db.dao_companies import assign_employee_to_company
        assign_employee_to_company(emp, a)
        answers = {
            "1234563218": {"status": "ZAWIESZONY", "data_zawieszenia": "2026-03-01"},
            "5260001246": {"status": "AKTYWNY"},
        }
        marked = cs.check_suspensions(lambda nip: answers[nip])
        self.assertEqual(marked, [a])
        self.assertEqual(get_company(a)["status"], "Zawieszone")
        self.assertEqual(get_company(a)["zawieszenia_dzialalnosci"], "2026-03-01")
        self.assertEqual(get_company(b)["status"], "Aktywne")
        self.assertEqual(get_company(c)["status"], "Aktywne")
        tasks = self._tasks(a)
        self.assertEqual(len(tasks), 1)
        self.assertTrue("zawiesił" in tasks[0][0] and tasks[0][1] == emp)
        self.assertEqual(self._tasks(b), [])
        self.assertEqual(self._tasks(c), [])

    def test_errors_are_skipped_and_inactive_not_queried(self):
        self._company("A", "1234563218")
        self._company("Z", "5260001246", status="Archiwalne")
        asked = []

        def fetch(nip):
            asked.append(nip)
            raise RuntimeError("boom")

        self.assertEqual(cs.check_suspensions(fetch), [])
        self.assertEqual(asked, ["1234563218"])

    def _tasks(self, firma_id):
        from app.db.dao_conn import get_conn
        conn = get_conn()
        try:
            return conn.execute(
                "SELECT tytul, pracownik_id FROM zadania WHERE firma_id = %s", (firma_id,)
            ).fetchall()
        finally:
            conn.close()

    def test_ceidg_resumption_restores_active_and_notifies_once(self):
        from app.db.dao_companies import assign_employee_to_company
        a = self._company("A", "1234563218")
        emp = self.make_employee()
        assign_employee_to_company(emp, a)
        state = {"status": "ZAWIESZONY"}
        fetch = lambda nip: dict(state)
        self.assertEqual(cs.check_ceidg_state(fetch), ([a], []))
        self.assertEqual(get_company(a)["status"], "Zawieszone")
        state["status"] = "AKTYWNY"
        self.assertEqual(cs.check_ceidg_state(fetch), ([], [a]))
        self.assertEqual(get_company(a)["status"], "Aktywne")
        tasks = self._tasks(a)
        self.assertEqual(len(tasks), 2)
        self.assertTrue(any("odwiesił" in t[0] and t[1] == emp for t in tasks))
        self.assertEqual(cs.check_ceidg_state(fetch), ([], []))
        self.assertEqual(len(self._tasks(a)), 2)

    def test_manually_suspended_company_is_not_auto_reactivated(self):
        a = self._company("A", "1234563218", status="Zawieszone")
        fetch = lambda nip: {"status": "AKTYWNY"}
        self.assertEqual(cs.check_ceidg_state(fetch), ([], []))
        self.assertEqual(get_company(a)["status"], "Zawieszone")
        self.assertEqual(self._tasks(a), [])

    def test_resumption_falls_back_to_admins(self):
        a = self._company("A", "1234563218")
        admin = self.make_employee(email="adm@example.com")
        from app.db.dao_conn import get_conn
        conn = get_conn()
        conn.execute("UPDATE pracownicy SET is_admin = 1 WHERE id = %s", (admin,))
        conn.commit()
        conn.close()
        state = {"status": "ZAWIESZONY"}
        fetch = lambda nip: dict(state)
        cs.check_ceidg_state(fetch)
        state["status"] = "AKTYWNY"
        cs.check_ceidg_state(fetch)
        owners = [t[1] for t in self._tasks(a)]
        self.assertIn(admin, owners)

    def test_services_suspension_notifies_everyone_and_keeps_status(self):
        from app.db.dao_companies import set_services_suspended, save_company
        a = self._company("A", "1234563218")
        e1 = self.make_employee()
        e2 = self.make_employee(email="b@example.com")
        self.assertTrue(set_services_suspended(a, True))
        self.assertFalse(set_services_suspended(a, True))
        firma = get_company(a)
        self.assertEqual(firma["status"], "Aktywne")
        self.assertEqual(int(firma["uslugi_zawieszone"]), 1)
        owners = {t[1] for t in self._tasks(a)}
        self.assertTrue({e1, e2} <= owners)
        n = len(self._tasks(a))
        save_company({"id": a, "uslugi_zawieszone": 0})
        self.assertEqual(int(get_company(a)["uslugi_zawieszone"]), 0)
        self.assertGreater(len(self._tasks(a)), n)

    def test_manual_company_checks(self):
        from app.utils.vat_check import check_company_vat
        a = self._company("A", "1234563218")
        emp = self.make_employee()
        res = check_company_vat(
            a, emp,
            wl_fetch=lambda nip: {"status_vat": "Czynny"},
            vies_fetch=lambda nip: {"valid": False},
        )
        self.assertEqual((res["vat"], res["vat_ue"], res["errors"]), ("czynny", "zwolniony", []))
        f = get_company(a)
        self.assertEqual((f["vat_status"], f["vat_ue_status"]), ("czynny", "zwolniony"))
        self.assertTrue(f["vat_sprawdzono"] and f["vat_sprawdzil"])
        res = check_company_vat(
            a, emp,
            wl_fetch=lambda nip: {"error": True, "message": "x"},
            vies_fetch=lambda nip: {"valid": True},
        )
        self.assertIsNone(res["vat"])
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(get_company(a)["vat_status"], "czynny")

        out = cs.check_company_ceidg(a, lambda nip: {"status": "ZAWIESZONY"})
        self.assertEqual((out["state"], out["change"]), ("zawieszona", "suspended"))
        self.assertEqual(get_company(a)["status"], "Zawieszone")
        out = cs.check_company_ceidg(a, lambda nip: {"status": "AKTYWNY"})
        self.assertEqual((out["state"], out["change"]), ("aktywna", "resumed"))
        self.assertEqual(get_company(a)["status"], "Aktywne")

    def test_vat_model_columns_and_tooltip(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from app.monthly_view.monthly_view_model import MonthlyViewModel
        m = MonthlyViewModel([{
            "id": 1, "nazwa": "A", "vat_status": "czynny",
            "vat_sprawdzono": "2026-02-19 12:23:00", "vat_sprawdzil": "Paweł S",
            "vat_ue_status": "zwolniony",
        }], "ksiegowosc")
        self.assertEqual(m.columnCount(), 12)
        self.assertEqual(m.data(m.index(0, 10), Qt.ItemDataRole.DisplayRole), "✔")
        self.assertEqual(m.data(m.index(0, 11), Qt.ItemDataRole.DisplayRole), "▲")
        tip = m.data(m.index(0, 10), Qt.ItemDataRole.ToolTipRole)
        self.assertIn("VAT czynny", tip)
        self.assertIn("19.02.2026 12:23, Paweł S", tip)

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
