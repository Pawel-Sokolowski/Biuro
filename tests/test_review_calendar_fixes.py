"""External-format preservation and occurrence identities, without remote I/O."""
import os
import time
import unittest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from icalendar import Calendar

from app.db import dao_calendar, dao_calendar_sync as outbox
from app.kalendarz import caldav_sync, google_sync
from app.kalendarz.caldav_identity import recurrence_key, remote_key, split_remote_key
from tests.base import DbTestCase
from tests.test_calendar_caldav import FakeCalendar

SERIES = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Biuro regression//EN
X-WR-CALNAME:Office
BEGIN:VEVENT
UID:series-1
SUMMARY:Original title
DTSTART;TZID=Europe/Warsaw:20261109T090000
DTEND;TZID=Europe/Warsaw:20261109T100000
RRULE:FREQ=WEEKLY;COUNT=3
ATTENDEE;CN=Client:mailto:client@example.com
ORGANIZER:mailto:office@example.com
X-EXTERNAL-FIELD:keep me
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
DESCRIPTION:Reminder
END:VALARM
END:VEVENT
END:VCALENDAR
"""

EXPANDED = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:series-1
RECURRENCE-ID;TZID=Europe/Warsaw:20261109T090000
DTSTART;TZID=Europe/Warsaw:20261109T090000
DTEND;TZID=Europe/Warsaw:20261109T100000
SUMMARY:First occurrence
END:VEVENT
BEGIN:VEVENT
UID:series-1
RECURRENCE-ID;TZID=Europe/Warsaw:20261116T090000
DTSTART;TZID=Europe/Warsaw:20261116T090000
DTEND;TZID=Europe/Warsaw:20261116T100000
SUMMARY:Second occurrence
END:VEVENT
END:VCALENDAR
"""


@contextmanager
def utc_workstation():
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        if hasattr(time, "tzset"):
            time.tzset()


class CalendarResourceTests(unittest.TestCase):
    def test_title_edit_preserves_unmodeled_resource_components(self):
        values = caldav_sync.parse_ical(SERIES)[0]
        values["tytul"] = "Changed title"
        rendered = caldav_sync._edit_resource(SERIES, "series-1", "", values)
        cal = Calendar.from_ical(rendered)
        event = cal.walk("VEVENT")[0]
        self.assertEqual(str(event["SUMMARY"]), "Changed title")
        self.assertEqual(str(cal["X-WR-CALNAME"]), "Office")
        self.assertEqual(str(event["ATTENDEE"]), "mailto:client@example.com")
        self.assertEqual(str(event["ORGANIZER"]), "mailto:office@example.com")
        self.assertEqual(str(event["X-EXTERNAL-FIELD"]), "keep me")
        self.assertEqual(event["RRULE"]["COUNT"], [3])
        self.assertEqual(len(event.walk("VALARM")), 1)

    def test_expanded_occurrences_keep_distinct_stable_identity(self):
        rows = caldav_sync.parse_ical(EXPANDED)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["caldav_uid"], rows[1]["caldav_uid"])
        self.assertEqual([split_remote_key(r["caldav_uid"])[0] for r in rows], ["series-1"] * 2)
        self.assertEqual([r["caldav_uid"] for r in caldav_sync.parse_ical(EXPANDED)],
                         [r["caldav_uid"] for r in rows])
        # Equivalent UTC and local TZID occurrence identifiers must match.
        self.assertEqual(recurrence_key(datetime.fromisoformat("2026-11-09T09:00:00+01:00")),
                         recurrence_key(datetime.fromisoformat("2026-11-09T08:00:00+00:00")))

    def test_occurrence_move_creates_exception_and_preserves_series(self):
        values = caldav_sync.parse_ical(EXPANDED)[1]
        uid, occurrence = split_remote_key(values["caldav_uid"])
        values.update(tytul="Moved only this occurrence", start="2026-11-17T11:00", koniec="2026-11-17T12:00")
        rendered = caldav_sync._edit_resource(SERIES, uid, occurrence, values)
        components = Calendar.from_ical(rendered).walk("VEVENT")
        self.assertEqual(len(components), 2)
        master, exception = components
        self.assertEqual(str(master["SUMMARY"]), "Original title")
        self.assertIn("RRULE", master)
        self.assertNotIn("RRULE", exception)
        self.assertEqual(recurrence_key(exception["RECURRENCE-ID"].dt), occurrence)
        self.assertEqual(exception["RECURRENCE-ID"].params["TZID"], "Europe/Warsaw")
        self.assertEqual(exception["DTSTART"].dt.day, 17)
        self.assertIn("ATTENDEE", exception)
        self.assertEqual(len(exception.walk("VALARM")), 1)
        # Retrying the same edit updates the exception instead of duplicating it.
        self.assertEqual(len(Calendar.from_ical(caldav_sync._edit_resource(rendered, uid, occurrence, values)).walk("VEVENT")), 2)

    def test_delete_occurrence_cancels_only_one_and_retry_is_idempotent(self):
        uid, occurrence = split_remote_key(caldav_sync.parse_ical(EXPANDED)[0]["caldav_uid"])
        rendered = caldav_sync._edit_resource(SERIES, uid, occurrence)
        rendered = caldav_sync._edit_resource(rendered, uid, occurrence)
        master, cancelled = Calendar.from_ical(rendered).walk("VEVENT")
        self.assertIn("RRULE", master)
        self.assertEqual(str(cancelled["STATUS"]), "CANCELLED")
        self.assertEqual(recurrence_key(cancelled["RECURRENCE-ID"].dt), occurrence)

    def test_conditional_remote_write_keeps_pending_edit_on_conflict(self):
        from caldav.elements.dav import GetEtag
        remote = SimpleNamespace(data=SERIES, props={GetEtag.tag: '"revision-1"'}, url="https://fake/event",
                                 client=mock.Mock())
        remote.client.put.return_value.status = 412
        with self.assertRaises(caldav_sync.CaldavError):
            caldav_sync._save_remote(remote, SERIES)
        self.assertEqual(remote.client.put.call_args.args[2]["If-Match"], '"revision-1"')

    def test_google_and_caldav_use_warsaw_on_utc_workstation(self):
        with utc_workstation():
            for when in ("2026-01-15T09:00:00+01:00", "2026-07-15T09:00:00+02:00"):
                incoming = {"id": "g1", "summary": "Office time", "start": {"dateTime": when}, "end": {"dateTime": when}}
                body = google_sync._to_google_body(google_sync._from_google(incoming))
                self.assertEqual(body["start"]["dateTime"][11:16], "09:00")
                self.assertEqual(body["start"]["timeZone"], "Europe/Warsaw")
            self.assertEqual(caldav_sync.parse_ical(EXPANDED)[0]["start"], "2026-11-09T09:00")

    def test_uid_prefix_is_escaped_without_confusing_resource_and_occurrence(self):
        uid = 'biuro-caldav-v1:["raw","uid"]'
        self.assertEqual(split_remote_key(remote_key(uid)), (uid, ""))


class CalendarOccurrenceDatabaseTests(DbTestCase):
    def setUp(self):
        super().setUp()
        caldav_sync.save_config("https://fake/", "u", "secret")
        self.scope = outbox.caldav_scope()

    def test_two_occurrences_survive_repeated_pull_with_distinct_ids(self):
        calendar = FakeCalendar([EXPANDED])
        caldav_sync.pull_events(1, calendar, days_back=1000, days_ahead=1000)
        before = dao_calendar.list_caldav_events(1, self.scope)
        caldav_sync.pull_events(1, calendar, days_back=1000, days_ahead=1000)
        after = dao_calendar.list_caldav_events(1, self.scope)
        self.assertEqual(len(before), 2)
        self.assertEqual({r["id"] for r in before}, {r["id"] for r in after})

    def test_occurrence_outboxes_are_distinct_and_transport_uses_resource_uid(self):
        rows = caldav_sync.parse_ical(EXPANDED)
        ids = []
        for row in rows:
            row["caldav_calendar_id"] = self.scope
            ids.append(dao_calendar.save_event(1, row, from_remote=True))
        for event_id in ids:
            values = dao_calendar.get_event(event_id)
            values["tytul"] += " edited"
            dao_calendar.save_event(1, values)
        ops = outbox.operations(1, "caldav", self.scope)
        self.assertEqual(len(ops), 2)
        calendar = FakeCalendar([SERIES])
        for op in ops:
            caldav_sync._send_operation(op, calendar)
        self.assertEqual(outbox.operations(1, "caldav", self.scope), [])
        master, *exceptions = Calendar.from_ical(calendar.events[0].data).walk("VEVENT")
        self.assertEqual(len(exceptions), 2)
        self.assertIn("RRULE", master)
        dao_calendar.delete_event(ids[0], 1)
        operation = outbox.operations(1, "caldav", self.scope)[0]
        self.assertEqual(operation["action"], "delete")
        caldav_sync._send_operation(operation, calendar)
        self.assertEqual(len(calendar.events), 1)
        events = Calendar.from_ical(calendar.events[0].data).walk("VEVENT")
        self.assertEqual(sum(str(e.get("STATUS", "")) == "CANCELLED" for e in events), 1)
