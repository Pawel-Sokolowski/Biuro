"""Stable CalDAV resource/occurrence keys used by local rows and the outbox.

Normal UIDs retain their old representation. Occurrences need both the UID and
RECURRENCE-ID; the encoded key is never sent to a CalDAV server as a UID.
"""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
_PREFIX = "biuro-caldav-v1:"


def recurrence_key(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.replace(tzinfo=WARSAW) if value.tzinfo is None else value
        return value.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")
    return value.isoformat()


def remote_key(uid: str, recurrence: str = "") -> str:
    if not recurrence and not uid.startswith(_PREFIX):
        return uid
    return _PREFIX + json.dumps([uid, recurrence], separators=(",", ":"), ensure_ascii=False)


def split_remote_key(key: str) -> tuple[str, str]:
    if key.startswith(_PREFIX):
        try:
            values = json.loads(key[len(_PREFIX):])
            if isinstance(values, list) and len(values) == 2 and all(isinstance(v, str) for v in values):
                return values[0], values[1]
        except ValueError:
            pass
    return key, ""


def recurrence_value(value: str) -> date | datetime:
    return datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)
