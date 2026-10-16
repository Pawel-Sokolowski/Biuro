import unittest
from datetime import date
from unittest import mock

from PyQt6.QtWidgets import QApplication

from app.db import dao_calendar
from app.kalendarz import caldav_sync
from tests.base import DbTestCase

ICAL = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//x//y//EN
BEGIN:VEVENT
UID:abc-1@x
SUMMARY:Spotkanie z klientem
DTSTART:{start}
DTEND:{end}
LOCATION:Biuro
END:VEVENT
END:VCALENDAR
"""


class FakeRemote:
    def __init__(self, calendar, data):
        self.calendar, self.data = calendar, data

    def save(self):
        pass

    def delete(self):
        self.calendar.events.remove(self)


class FakeCalendar:
    name = "Moj"
    url = "https://fake/cal/"

    def __init__(self, events=()):
        self.events = [FakeRemote(self, d) for d in events]

    def date_search(self, start, end):
        return list(self.events)

    def save_event(self, ical):
        r = FakeRemote(self, ical)
        self.events.append(r)
        return r

    def event_by_uid(self, uid):
        for e in self.events:
            if f"UID:{uid}" in e.data:
                return e
        raise KeyError(uid)


class FakeClient:
    def __init__(self, calendars):
        self.cals = calendars

    def principal(self):
        return self

    def calendars(self):
        return self.cals


def _today_ical():
    d = date.today().strftime("%Y%m%d")
    return ICAL.format(start=f"{d}T090000", end=f"{d}T100000")


class CaldavTests(DbTestCase):
    def setUp(self):
        super().setUp()
        caldav_sync.save_config("https://fake/", "u", "tajne")

    def _patch(self, cal):
        return mock.patch.object(caldav_sync, "_make_client", lambda *a: FakeClient([cal]))

    def test_password_is_encrypted(self):
        from app.db.dao_app_settings import get_setting
        self.assertNotEqual(get_setting(caldav_sync.KEY_PASSWORD), "tajne")
        self.assertEqual(caldav_sync.get_config()["haslo"], "tajne")
        self.assertTrue(caldav_sync.is_configured())

    def test_parse_timed_and_all_day(self):
        ev = caldav_sync.parse_ical(ICAL.format(start="20260305T090000", end="20260305T100000"))[0]
        self.assertEqual((ev["start"], ev["koniec"], ev["caly_dzien"]), ("2026-03-05T09:00", "2026-03-05T10:00", 0))
        ev = caldav_sync.parse_ical(ICAL.format(start="20260305", end="20260307").replace("DTSTART:", "DTSTART;VALUE=DATE:").replace("DTEND:", "DTEND;VALUE=DATE:"))[0]
        self.assertEqual((ev["start"], ev["koniec"], ev["caly_dzien"]), ("2026-03-05", "2026-03-06", 1))

    def test_pull_creates_once_no_duplicates(self):
        emp = self.make_employee()
        cal = FakeCalendar([_today_ical()])
        with self._patch(cal):
            caldav_sync.sync(emp)
            stats = caldav_sync.sync(emp)
        rows = dao_calendar.list_caldav_events(emp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["zrodlo"], "caldav")
        self.assertEqual(stats["pobrane"], 1)

    def test_pull_removes_events_deleted_remotely(self):
        emp = self.make_employee()
        cal = FakeCalendar([_today_ical()])
        with self._patch(cal):
            caldav_sync.sync(emp)
            cal.events.clear()
            stats = caldav_sync.sync(emp)
        self.assertEqual(stats["usuniete"], 1)
        self.assertEqual(dao_calendar.list_caldav_events(emp), [])

    def test_push_new_update_delete(self):
        emp = self.make_employee()
        eid = dao_calendar.save_event(emp, {"tytul": "Lokalne", "start": "2026-03-05T09:00", "koniec": "2026-03-05T10:00"})
        cal = FakeCalendar()
        with self._patch(cal):
            stats = caldav_sync.sync(emp)
            self.assertEqual(stats["wyslane"], 1)
            ev = dao_calendar.get_event(eid)
            self.assertTrue(ev["caldav_uid"])
            self.assertEqual(len(cal.events), 1)
            self.assertIn("SUMMARY:Lokalne", cal.events[0].data)
            ev["tytul"] = "Zmienione"
            dao_calendar.save_event(emp, ev)
            caldav_sync.push_event(emp, dao_calendar.get_event(eid), cal)
            self.assertEqual(len(cal.events), 1)
            self.assertIn("SUMMARY:Zmienione", cal.events[0].data)
            caldav_sync.delete_remote(emp, ev["caldav_uid"], cal)
            self.assertEqual(cal.events, [])

    def test_connection_and_list_calendars(self):
        with self._patch(FakeCalendar()):
            self.assertEqual(caldav_sync.list_calendars()[0]["nazwa"], "Moj")
            self.assertIn("1", caldav_sync.test_connection("https://fake/", "u", "p"))

    def test_network_errors_are_friendly(self):
        def boom(*a):
            raise ConnectionError("Connection refused")
        with mock.patch.object(caldav_sync, "_make_client", boom):
            with self.assertRaises(caldav_sync.CaldavError) as ctx:
                caldav_sync.test_connection("https://x/", "u", "p")
            self.assertIn("Brak połączenia", str(ctx.exception))
            with self.assertRaises(caldav_sync.CaldavError):
                caldav_sync.sync(self.make_employee())

    def test_not_configured(self):
        caldav_sync.save_config("", "", "")
        from app.db.dao_app_settings import delete_setting
        delete_setting(caldav_sync.KEY_PASSWORD)
        with self.assertRaises(caldav_sync.CaldavError):
            caldav_sync.sync(self.make_employee())


class CalendarPageTests(DbTestCase):
    def test_page_builds_and_switches_views(self):
        app = QApplication.instance() or QApplication([])
        from app.kalendarz.calendar_ui import CalendarPage
        emp = self.make_employee()
        today = date.today().isoformat()
        dao_calendar.save_event(emp, {"tytul": "Test", "start": today + "T09:00", "koniec": today + "T10:00"})
        page = CalendarPage(emp)
        self.assertEqual(page.tree.topLevelItemCount(), 1)
        for i in range(4):
            page._set_view(i)
            page._shift(1)
            page._shift(-1)
        page._set_view(3)
        page.search.setText("brak takiego")
        self.assertEqual(page.tree.topLevelItemCount(), 0)
        page.search.setText("tes")
        self.assertEqual(page.tree.topLevelItemCount(), 1)
        page.calendars.item(0).setCheckState(page.calendars.item(0).checkState().__class__.Unchecked)
        self.assertEqual(page.tree.topLevelItemCount(), 0)


if __name__ == "__main__":
    unittest.main()
