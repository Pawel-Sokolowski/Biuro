from app.db.dao_companies import assign_employee_to_company
import unittest

from app.db.dao_monthly import get_month_status, save_month_status
from app.db.dao_tasks import delete_tasks, list_tasks, list_tasks_for_timer, save_task, set_tasks_status
from tests.base import DbTestCase


class SubstitutionTests(DbTestCase):
    def test_substitute_sees_company_and_original_owner_filter_does_not(self):
        from app.db.dao_monthly import clear_substitution, set_substitution
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic

        kasia = self.make_employee(email="kasia@example.com")
        pawel = self.make_employee(email="pawel@example.com")
        firma = self.make_company("Firma A", ksiegowa_id=kasia)
        logic = MonthlyViewLogic(pawel)
        self.assertEqual(logic.get_monthly_rows("2026-10", pawel, "Wszystkie"), [])
        set_substitution(firma, "ksiegowosc", pawel, kasia)
        rows = logic.get_monthly_rows("2026-10", pawel, "Wszystkie")
        self.assertEqual([r["id"] for r in rows], [firma])
        self.assertEqual(rows[0]["zast_ks_id"], pawel)
        with self.assertRaises(ValueError):
            set_substitution(firma, "ksiegowosc", kasia, kasia)
        set_substitution(firma, "ksiegowosc", pawel, kasia, "2000-01-01")
        self.assertEqual(logic.get_monthly_rows("2026-10", pawel, "Wszystkie"), [])
        clear_substitution(firma, "ksiegowosc")


class MonthlyStageFilterAndSubstitutionDisplayTests(unittest.TestCase):
    def _page(self, choice, employee_id=None, delivery='Wszystkie'):
        from types import SimpleNamespace
        from app.monthly_view.monthly_view_ui import MonthlyViewPage
        stub = SimpleNamespace(
            stage_combo=SimpleNamespace(currentText=lambda: choice),
            delivery_combo=SimpleNamespace(currentText=lambda: delivery),
            _current_employee_id=lambda: employee_id,
            _SUBSTITUTION_FIELDS=MonthlyViewPage._SUBSTITUTION_FIELDS,
        )
        return stub, MonthlyViewPage

    def test_stage_filter_options(self):
        rows = {
            "ok": {"etap": "Zatwierdzone", "dokumenty_dostarczone": 1},
            "none": {"etap": None, "dokumenty_dostarczone": 0},
            "booked": {"etap": "Zaksięgowane", "dokumenty_dostarczone": 0},
        }
        expected = {
            "Wszystkie": {"ok", "none", "booked"},
            "Brak": {"none"},
            "Zatwierdzone": {"ok"},
            "Zaksięgowane": {"booked"},
            "Wysłane wyniki": set(),
        }
        for choice, names in expected.items():
            stub, cls = self._page(choice)
            got = {k for k, r in rows.items() if cls._stage_matches(stub, r, "etap")}
            self.assertEqual(got, names, choice)

    def test_stage_filter_uses_stage_field_of_the_given_tab(self):
        stub, cls = self._page("Wysłane wyniki")
        row = {"etap": "Brak", "etap_kadry": "Wysłane wyniki"}
        self.assertFalse(cls._stage_matches(stub, row, "etap"))
        self.assertTrue(cls._stage_matches(stub, row, "etap_kadry"))

    def test_row_in_tab_prefers_substitute_over_owner(self):
        row = {"ksiegowa_id": 1, "zast_ks_id": 2}
        owner, cls = self._page("Wszystkie", 1)
        substitute, _ = self._page("Wszystkie", 2)
        everyone, _ = self._page("Wszystkie", None)
        self.assertFalse(cls._row_in_tab(owner, row, "ksiegowa_id"))
        self.assertTrue(cls._row_in_tab(substitute, row, "ksiegowa_id"))
        self.assertTrue(cls._row_in_tab(everyone, row, "ksiegowa_id"))
        self.assertTrue(cls._row_in_tab(owner, {"ksiegowa_id": 1}, "ksiegowa_id"))

    def test_mark_substitution_is_per_role(self):
        _stub, cls = self._page("Wszystkie")
        row = {"id": 1, "zast_ks_id": 2, "zast_ks_za": "Kasia Nowak"}
        marked = cls._mark_substitution(None, row, "ksiegowosc")
        self.assertTrue(marked["zastepstwo"])
        self.assertEqual(marked["zastepstwo_za"], "Kasia Nowak")
        self.assertNotIn("zastepstwo", row)
        self.assertIs(cls._mark_substitution(None, row, "kadry"), row)

    def test_model_marks_substituted_company_name(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QColor, QFont
        from app.monthly_view.monthly_view_model import MonthlyViewModel
        model = MonthlyViewModel([
            {"id": 1, "nazwa": "Firma A", "zastepstwo": True, "zastepstwo_za": "Kasia Nowak"},
            {"id": 2, "nazwa": "Firma B"},
        ], "ksiegowosc")
        first, second = model.index(0, 0), model.index(1, 0)
        self.assertEqual(model.data(first), "↪ Firma A")
        self.assertEqual(model.data(first, Qt.ItemDataRole.ToolTipRole), "Zastępstwo za: Kasia Nowak")
        self.assertTrue(model.data(first, Qt.ItemDataRole.FontRole).italic())
        self.assertIsInstance(model.data(first, Qt.ItemDataRole.ForegroundRole), QColor)
        self.assertEqual(model.data(second), "Firma B")
        self.assertNotIsInstance(model.data(second, Qt.ItemDataRole.FontRole), QFont)


class MonthlyStatusTests(DbTestCase):
    def test_status_and_delivered_flag_roundtrip(self):
        firma = self.make_company()
        save_month_status(firma, "2026-01", {"etap": "Zaksięgowane", "dokumenty_dostarczone": 1})
        row = get_month_status(firma, "2026-01")
        self.assertEqual(row["etap"], "Zaksięgowane")
        self.assertEqual(row["dokumenty_dostarczone"], 1)

    def test_all_stages_accepted(self):
        firma = self.make_company()
        for i, etap in enumerate(("Brak", "Zatwierdzone", "Zaksięgowane", "Wysłane wyniki"), start=1):
            save_month_status(firma, f"2026-0{i}", {"etap": etap})
            self.assertEqual(get_month_status(firma, f"2026-0{i}")["etap"], etap)

    def test_unknown_stage_rejected(self):
        firma = self.make_company()
        with self.assertRaises(Exception):
            save_month_status(firma, "2026-01", {"etap": "W trakcie"})

    def test_update_overwrites_existing_month(self):
        firma = self.make_company()
        save_month_status(firma, "2026-01", {"etap": "Zatwierdzone", "dokumenty_dostarczone": 1})
        save_month_status(firma, "2026-01", {"etap": "Wysłane wyniki", "dokumenty_dostarczone": 0})
        row = get_month_status(firma, "2026-01")
        self.assertEqual((row["etap"], row["dokumenty_dostarczone"]), ("Wysłane wyniki", 1))


class MonthlyContractDefaultsTests(DbTestCase):
    def test_newest_month_updates_company_defaults_without_changing_history(self):
        from app.db.dao_companies import get_company
        from app.db.dao_monthly import get_month_contracts
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic

        firma = self.make_company(uop_ilosc=10, uz_ilosc=2, uod_ilosc=1)
        # Pomijamy uruchamianie sprawdzania CEIDG w tle w konstruktorze.
        logic = MonthlyViewLogic.__new__(MonthlyViewLogic)

        logic.set_month("2026-09")
        logic.save_status_for_company(firma, uop=20)
        self.assertEqual(get_company(firma)["uop_ilosc"], 20)

        logic.set_month("2026-10")
        logic.save_status_for_company(firma, uop=22, uz=3, uod=2)
        company = get_company(firma)
        self.assertEqual(
            (company["uop_ilosc"], company["uz_ilosc"], company["uod_ilosc"]),
            (22, 3, 2),
        )
        self.assertEqual(
            get_month_contracts(firma, "2026-09"),
            {"uop_ilosc": 20, "uz_ilosc": 2, "uod_ilosc": 1},
        )
        self.assertEqual(
            get_month_contracts(firma, "2026-10"),
            {"uop_ilosc": 22, "uz_ilosc": 3, "uod_ilosc": 2},
        )

        # Korekta starszego miesiąca nie cofa domyślnych wartości klienta.
        logic.set_month("2026-09")
        logic.save_status_for_company(firma, uop=19)
        self.assertEqual(get_month_contracts(firma, "2026-09")["uop_ilosc"], 19)
        self.assertEqual(get_company(firma)["uop_ilosc"], 22)

        logic.set_month("2026-10")
        logic.save_status_for_company(firma, uop=23)
        self.assertEqual(get_company(firma)["uop_ilosc"], 23)


class TimerTaskFilterTests(DbTestCase):
    def test_only_tasks_of_selected_company_are_listed(self):
        emp = self.make_employee()
        firma_a = self.make_company("A")
        firma_b = self.make_company("B")
        for f in (firma_a, firma_b):
            assign_employee_to_company(emp, f)
        save_task("Zadanie A", firma_id=firma_a, pracownik_id=emp, created_by=emp, actor_id=emp)
        save_task("Zadanie B", firma_id=firma_b, pracownik_id=emp, created_by=emp, actor_id=emp)
        titles = [t["tytul"] for t in list_tasks_for_timer(emp, firma_a)]
        self.assertEqual(titles, ["Zadanie A"])

    def test_closed_tasks_are_not_offered(self):
        emp = self.make_employee()
        firma = self.make_company()
        assign_employee_to_company(emp, firma)
        save_task("Zamknięte", status="Zakończone", firma_id=firma, pracownik_id=emp, created_by=emp, actor_id=emp)
        save_task("Otwarte", firma_id=firma, pracownik_id=emp, created_by=emp, actor_id=emp)
        titles = [t["tytul"] for t in list_tasks_for_timer(emp, firma)]
        self.assertEqual(titles, ["Otwarte"])


class BulkTaskTests(DbTestCase):
    def test_bulk_status_and_delete(self):
        emp = self.make_employee()
        other = self.make_employee(email="inny@example.com")
        mine = [save_task(f"T{i}", pracownik_id=emp, created_by=emp, actor_id=emp) for i in range(3)]
        foreign = save_task("Obce", pracownik_id=other, created_by=other, actor_id=other)
        self.assertEqual(set_tasks_status(mine + [foreign], "Zakończone", actor_id=emp), 3)
        by_id = {t["id"]: t["status"] for t in list_tasks(viewer_id=emp)}
        self.assertTrue(all(by_id[i] == "Zakończone" for i in mine))
        self.assertEqual(delete_tasks(mine + [foreign], actor_id=emp), 3)
        self.assertFalse(any(t["id"] in mine for t in list_tasks(viewer_id=emp)))


if __name__ == "__main__":
    unittest.main()
