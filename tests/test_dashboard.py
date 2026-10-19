import unittest
from datetime import date

from app.db.dao_calendar import save_event
from app.db.dao_dashboard import format_duration, get_dashboard_data
from app.db.dao_monthly import save_month_status
from app.db.dao_conn import get_conn
from app.db.dao_tasks import save_task
from tests.base import DbTestCase

TODAY = date(2026, 3, 10)


class DashboardDataTests(DbTestCase):
    def test_counts_reflect_database(self):
        emp = self.make_employee()
        other = self.make_employee("o@x.pl", "Olga", "Inna")
        firma = self.make_company("Alfa")
        from app.db.dao_companies import assign_employee_to_company
        assign_employee_to_company(emp, firma)
        self.make_company("Beta")

        save_task("Moje 1", firma_id=firma, pracownik_id=emp, created_by=emp, actor_id=emp, termin="2026-03-01")
        save_task("Moje 2", firma_id=firma, pracownik_id=emp, created_by=emp, actor_id=emp, termin="2026-03-20")
        save_task("Cudze", pracownik_id=other, created_by=1, actor_id=1)
        save_task("Skończone", status="Zakończone", pracownik_id=emp, created_by=emp, actor_id=emp)

        save_month_status(firma, "2026-03", {"etap": "Zatwierdzone", "dokumenty_dostarczone": 1})

        data = get_dashboard_data(emp, today=TODAY)
        self.assertGreaterEqual(data["firmy_aktywne"], 2)
        self.assertEqual(data["zadania_otwarte"], 2)
        self.assertEqual(data["zadania_zalegle"], 1)
        self.assertEqual(data["dokumenty_dostarczone"], 1)
        self.assertEqual(data["dokumenty_brak"], data["firmy_aktywne"] - 1)
        self.assertEqual(data["etapy"]["Zatwierdzone"], 1)
        self.assertEqual(sum(data["etapy"].values()), data["firmy_aktywne"])
        self.assertEqual([t["tytul"] for t in data["zadania"]], ["Moje 1", "Moje 2"])
        self.assertNotIn("Alfa", [c["nazwa"] for c in data["brak_dokumentow"]])
        self.assertIn("Beta", [c["nazwa"] for c in data["brak_dokumentow"]])

    def test_events_in_next_seven_days(self):
        emp = self.make_employee()
        save_event(emp, {"tytul": "Za 2 dni", "start": "2026-03-12T10:00", "koniec": "2026-03-12T11:00"})
        save_event(emp, {"tytul": "Za miesiąc", "start": "2026-04-12T10:00", "koniec": "2026-04-12T11:00"})
        data = get_dashboard_data(emp, today=TODAY)
        self.assertEqual([e["tytul"] for e in data["wydarzenia"]], ["Za 2 dni"])

    def test_timer_seconds_and_sessions_today(self):
        emp = self.make_employee()
        firma = self.make_company()
        conn = get_conn()
        conn.execute(
            "INSERT INTO czasomierze (firma_id, pracownik_id, miesiac, typ, rozpoczecie, zakonczenie, sekundy) "
            "VALUES (%s, %s, '2026-03', 'ksiegowosc', '2026-03-01 08:00', '2026-03-01 09:00', 3600)",
            (firma, emp),
        )
        conn.execute(
            "INSERT INTO czasomierze (firma_id, pracownik_id, miesiac, typ, rozpoczecie, zakonczenie, sekundy) "
            "VALUES (%s, %s, '2026-03', 'ksiegowosc', '2026-03-10 08:00', '2026-03-10 08:30', 1800)",
            (firma, emp),
        )
        conn.commit()
        conn.close()
        data = get_dashboard_data(emp, today=TODAY)
        self.assertEqual(data["sekundy_miesiac"], 5400)
        self.assertEqual(data["sesje_dzisiaj"], 1)

    def test_format_duration(self):
        self.assertEqual(format_duration(3660), "1 h 01 min")
        self.assertEqual(format_duration(0), "0 h 00 min")


if __name__ == "__main__":
    unittest.main()
