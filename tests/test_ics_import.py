import os, tempfile, unittest
from datetime import date
from app.kalendarz.ics_import import import_ics
from app.db import dao_calendar
from tests.base import DbTestCase

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//t//EN
BEGIN:VEVENT
UID:a1
DTSTART:20260310T090000
DTEND:20260310T100000
SUMMARY:Spotkanie
END:VEVENT
BEGIN:VEVENT
UID:a2
DTSTART;VALUE=DATE:20260312
DTEND;VALUE=DATE:20260314
SUMMARY:Urlop
END:VEVENT
BEGIN:VEVENT
UID:a3
DTSTART:20260401T080000
DTEND:20260401T083000
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Cotygodniowe
END:VEVENT
END:VCALENDAR
"""


class IcsImportTests(DbTestCase):
    def test_import_and_reimport(self):
        emp = self.make_employee()
        fd, path = tempfile.mkstemp(suffix=".ics")
        os.write(fd, ICS.encode()); os.close(fd)
        try:
            s = import_ics(emp, path)
            self.assertEqual(s["zaimportowane"], 5)
            ev = dao_calendar.list_events(emp, "2026-03-01", "2026-05-01")
            self.assertEqual(sorted(e["tytul"] for e in ev), ["Cotygodniowe"] * 3 + ["Spotkanie", "Urlop"])
            urlop = [e for e in ev if e["tytul"] == "Urlop"][0]
            self.assertEqual((urlop["caly_dzien"], urlop["koniec"]), (1, "2026-03-13"))
            s2 = import_ics(emp, path)
            self.assertEqual((s2["zaimportowane"], s2["pominiete"]), (0, 5))
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
