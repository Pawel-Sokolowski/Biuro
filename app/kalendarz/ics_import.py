"""Import wydarzeń z pliku iCalendar (.ics) do kalendarza pracownika."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from icalendar import Calendar

from app.db import dao_calendar
from app.kalendarz.caldav_sync import parse_ical

YEARS_BACK = 2
YEARS_AHEAD = 3


def _expand(cal: Calendar) -> List[Dict[str, Any]]:
    """Zwykłe wydarzenia + rozwinięte wystąpienia wydarzeń cyklicznych (RRULE)."""
    plain, recurring = Calendar(), Calendar()
    for comp in cal.walk("VEVENT"):
        (recurring if comp.get("RRULE") is not None else plain).add_component(comp)
    rows = parse_ical(plain.to_ical())

    if recurring.subcomponents:
        import recurring_ical_events

        today = date.today()
        start = today.replace(year=today.year - YEARS_BACK, month=1, day=1)
        end = today.replace(year=today.year + YEARS_AHEAD, month=12, day=31)
        for n, occ in enumerate(recurring_ical_events.of(recurring).between(start, end)):
            one = Calendar()
            one.add_component(occ)
            for row in parse_ical(one.to_ical()):
                row["caldav_uid"] = f"{row['caldav_uid']}#{n}"
                rows.append(row)
    return rows


def import_ics(pracownik_id: int, path: str) -> Dict[str, int]:
    """Importuje plik .ics. Zwraca {'zaimportowane': n, 'pominiete': m}.
    Wydarzenia już istniejące (ten sam tytuł i termin) są pomijane, więc plik można wczytać ponownie."""
    with open(path, "rb") as fh:
        cal = Calendar.from_ical(fh.read())

    stats = {"zaimportowane": 0, "pominiete": 0}
    existing = {
        (e["tytul"], e["start"], e["koniec"])
        for e in dao_calendar.list_events(pracownik_id, "0000-01-01", "9999-12-31")
    }
    for row in _expand(cal):
        key = (row["tytul"], row["start"], row["koniec"])
        if key in existing:
            stats["pominiete"] += 1
            continue
        row.pop("caldav_uid", None)
        row["zrodlo"] = "ics"
        dao_calendar.save_event(pracownik_id, row)
        existing.add(key)
        stats["zaimportowane"] += 1
    return stats
