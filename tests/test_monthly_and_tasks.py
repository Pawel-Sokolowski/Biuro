from app.db.dao_companies import assign_employee_to_company
import unittest

from app.db.dao_monthly import get_month_status, save_month_status
from app.db.dao_tasks import list_tasks_for_timer, save_task
from tests.base import DbTestCase


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
        self.assertEqual((row["etap"], row["dokumenty_dostarczone"]), ("Wysłane wyniki", 0))


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


if __name__ == "__main__":
    unittest.main()
