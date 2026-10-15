import unittest

from app.db.dao_calendar import (
    delete_event, get_event_access, list_events, save_event, set_event_access,
)
from app.kalendarz.google_sync import _from_google
from tests.base import DbTestCase


def event(**kw):
    base = {"tytul": "Spotkanie", "start": "2026-03-05T09:00", "koniec": "2026-03-05T10:00"}
    base.update(kw)
    return base


class CalendarTests(DbTestCase):
    def test_save_and_list_in_range(self):
        emp = self.make_employee()
        save_event(emp, event())
        self.assertEqual(len(list_events(emp, "2026-03-05", "2026-03-06")), 1)
        self.assertEqual(list_events(emp, "2026-03-06", "2026-03-07"), [])

    def test_required_fields(self):
        emp = self.make_employee()
        with self.assertRaises(ValueError):
            save_event(emp, event(tytul=""))

    def test_other_employee_does_not_see_event_without_access(self):
        owner = self.make_employee("o@x.pl", "Olga", "Owner")
        other = self.make_employee("p@x.pl", "Piotr", "Other")
        save_event(owner, event())
        self.assertEqual(list_events(other, "2026-03-05", "2026-03-06"), [])

    def test_shared_event_visible_to_granted_employee_only(self):
        owner = self.make_employee("o@x.pl", "Olga", "Owner")
        granted = self.make_employee("g@x.pl", "Grzegorz", "Granted")
        outsider = self.make_employee("x@x.pl", "Xawery", "Outsider")
        event_id = save_event(owner, event())
        set_event_access(event_id, [granted])

        seen = list_events(granted, "2026-03-05", "2026-03-06")
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0]["udostepnione"])
        self.assertEqual(seen[0]["wlasciciel"], "Olga Owner")
        self.assertEqual(list_events(outsider, "2026-03-05", "2026-03-06"), [])
        self.assertFalse(list_events(owner, "2026-03-05", "2026-03-06")[0]["udostepnione"])

    def test_access_is_replaced_and_owner_ignored(self):
        owner = self.make_employee("o@x.pl", "Olga", "Owner")
        a = self.make_employee("a@x.pl", "Anna", "A")
        b = self.make_employee("b@x.pl", "Bartek", "B")
        event_id = save_event(owner, event())
        set_event_access(event_id, [a, b, owner])
        self.assertEqual(sorted(get_event_access(event_id)), sorted([a, b]))
        set_event_access(event_id, [b])
        self.assertEqual(get_event_access(event_id), [b])

    def test_shared_user_cannot_modify_or_delete(self):
        owner = self.make_employee("o@x.pl", "Olga", "Owner")
        granted = self.make_employee("g@x.pl", "Grzegorz", "Granted")
        event_id = save_event(owner, event())
        set_event_access(event_id, [granted])

        save_event(granted, event(id=event_id, tytul="Zmiana"))
        delete_event(event_id, granted)
        events = list_events(owner, "2026-03-05", "2026-03-06")
        self.assertEqual([e["tytul"] for e in events], ["Spotkanie"])

    def test_deleting_event_removes_access_rows(self):
        owner = self.make_employee("o@x.pl", "Olga", "Owner")
        granted = self.make_employee("g@x.pl", "Grzegorz", "Granted")
        event_id = save_event(owner, event())
        set_event_access(event_id, [granted])
        delete_event(event_id, owner)
        self.assertEqual(get_event_access(event_id), [])

    def test_google_all_day_end_is_converted_to_inclusive(self):
        parsed = _from_google({
            "id": "g1", "summary": "Urlop",
            "start": {"date": "2026-03-05"}, "end": {"date": "2026-03-07"},
        })
        self.assertEqual(parsed["caly_dzien"], 1)
        self.assertEqual(parsed["google_event_id"], "g1")


if __name__ == "__main__":
    unittest.main()
