"""Synchronizacja kalendarza przez CalDAV (biblioteka `caldav`) – Google Calendar lub dowolny serwer CalDAV."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from icalendar import Calendar, Event
from caldav.lib.error import NotFoundError

from app.db import dao_calendar, dao_calendar_sync as outbox
from app.db.dao_app_settings import get_setting, set_setting
from app.utils.crypto import decrypt_password, encrypt_password
from app.kalendarz.caldav_identity import WARSAW, recurrence_key, recurrence_value, remote_key, split_remote_key

KEY_URL = "caldav_url"
KEY_USER = "caldav_login"
KEY_PASSWORD = "caldav_haslo_enc"
KEY_CALENDAR = "caldav_kalendarz"
GOOGLE_HINT = "https://apidata.googleusercontent.com/caldav/v2/{calendar_id}/events"


class CaldavError(Exception):
    """Błąd z komunikatem gotowym do pokazania użytkownikowi."""


# ---------- konfiguracja ----------
def get_config() -> Dict[str, str]:
    enc = get_setting(KEY_PASSWORD) or ""
    try:
        password = decrypt_password(enc) if enc else ""
    except Exception:
        password = ""
    return {
        "url": (get_setting(KEY_URL) or "").strip(),
        "login": (get_setting(KEY_USER) or "").strip(),
        "haslo": password,
        "kalendarz": (get_setting(KEY_CALENDAR) or "").strip(),
    }


def save_config(url: str, login: str, password: str = "", calendar_url: Optional[str] = None) -> None:
    """Puste hasło oznacza pozostawienie zapisanego."""
    set_setting(KEY_URL, url.strip())
    set_setting(KEY_USER, login.strip())
    if password:
        set_setting(KEY_PASSWORD, encrypt_password(password))
    if calendar_url is not None:
        set_setting(KEY_CALENDAR, calendar_url.strip())


def is_configured() -> bool:
    cfg = get_config()
    return bool(cfg["url"] and cfg["login"] and cfg["haslo"])


# ---------- klient ----------
def _make_client(url: str, login: str, password: str):
    import caldav
    return caldav.DAVClient(url=url, username=login, password=password)


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, CaldavError):
        return str(exc)
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "authorization" in name or "401" in text or "403" in text or "unauthorized" in text:
        return "Serwer odrzucił login lub hasło. Sprawdź dane (dla Google użyj hasła aplikacji/OAuth)."
    if "notfound" in name or "404" in text:
        return "Nie znaleziono kalendarza pod podanym adresem URL."
    if any(w in name + text for w in ("connection", "timeout", "resolve", "getaddrinfo", "network", "ssl")):
        return "Brak połączenia z serwerem kalendarza. Sprawdź internet i adres URL."
    return f"Błąd synchronizacji kalendarza: {exc}"


def _connect(cfg: Optional[Dict[str, str]] = None):
    cfg = cfg or get_config()
    if not (cfg["url"] and cfg["login"] and cfg["haslo"]):
        raise CaldavError("Połączenie CalDAV nie jest skonfigurowane (Ustawienia → Kalendarz).")
    try:
        return _make_client(cfg["url"], cfg["login"], cfg["haslo"])
    except Exception as exc:
        raise CaldavError(friendly_error(exc)) from exc


def _calendars(client, cfg: Dict[str, str]) -> list:
    try:
        cals = list(client.principal().calendars())
    except Exception:
        cals = []
    if not cals:
        cals = [client.calendar(url=cfg["url"])]
    return cals


def _cal_url(cal) -> str:
    return str(getattr(cal, "url", "") or "")


def _cal_name(cal) -> str:
    return str(getattr(cal, "name", None) or _cal_url(cal))


def test_connection(url: str, login: str, password: str) -> str:
    """Zwraca komunikat o powodzeniu albo rzuca CaldavError."""
    try:
        cals = list_calendars({"url": url.strip(), "login": login.strip(), "haslo": password, "kalendarz": ""})
    except CaldavError:
        raise
    except Exception as exc:
        raise CaldavError(friendly_error(exc)) from exc
    return f"Połączono. Liczba znalezionych kalendarzy: {len(cals)}."


def list_calendars(cfg: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    cfg = cfg or get_config()
    try:
        client = _connect(cfg)
        return [{"nazwa": _cal_name(c), "url": _cal_url(c)} for c in _calendars(client, cfg)]
    except CaldavError:
        raise
    except Exception as exc:
        raise CaldavError(friendly_error(exc)) from exc


def _selected_calendar(client, cfg):
    cals = _calendars(client, cfg)
    if cfg.get("kalendarz"):
        for c in cals:
            if _cal_url(c) == cfg["kalendarz"]:
                return c
        raise CaldavError("Wybrany kalendarz nie jest dostępny. Sprawdź ustawienia kalendarza.")
    if not cals:
        raise CaldavError("Brak dostępnych kalendarzy.")
    return cals[0]


# ---------- konwersja VEVENT <-> wydarzenia ----------
def _local_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(WARSAW).replace(tzinfo=None)
    return value


def parse_ical(data: Any) -> List[Dict[str, Any]]:
    """Zamienia dane iCalendar na słowniki wierszy `wydarzenia` (z kluczem caldav_uid)."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    out: Dict[str, Dict[str, Any]] = {}
    for comp in Calendar.from_ical(data).walk("VEVENT"):
        uid = str(comp.get("UID") or "").strip()
        rid = recurrence_key(comp["RECURRENCE-ID"].dt) if comp.get("RECURRENCE-ID") else ""
        key = remote_key(uid, rid)
        if not uid or key in out or comp.get("DTSTART") is None or str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        start = comp.get("DTSTART").dt
        end_prop = comp.get("DTEND")
        end = end_prop.dt if end_prop is not None else None
        if end is None and comp.get("DURATION") is not None:
            end = start + comp.get("DURATION").dt
        if isinstance(start, datetime):
            start = _local_naive(start)
            end = _local_naive(end) if isinstance(end, datetime) else start + timedelta(hours=1)
            all_day, s, e = 0, start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")
        else:
            last = (end - timedelta(days=1)) if isinstance(end, date) and not isinstance(end, datetime) else start
            all_day, s, e = 1, start.isoformat(), max(last, start).isoformat()
        out[key] = {
            "tytul": str(comp.get("SUMMARY") or "(bez tytułu)"),
            "opis": str(comp.get("DESCRIPTION") or ""),
            "lokalizacja": str(comp.get("LOCATION") or ""),
            "start": s, "koniec": e, "caly_dzien": all_day,
            "caldav_uid": key, "zrodlo": "caldav",
        }
    return list(out.values())


def build_ical(event: Dict[str, Any], uid: str) -> str:
    cal = Calendar()
    cal.add("prodid", "-//Biuro//Kalendarz//PL")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("uid", uid)
    ev.add("dtstamp", datetime.utcnow())
    ev.add("summary", event["tytul"])
    if event.get("opis"):
        ev.add("description", event["opis"])
    if event.get("lokalizacja"):
        ev.add("location", event["lokalizacja"])
    if event.get("caly_dzien"):
        ev.add("dtstart", date.fromisoformat(event["start"][:10]))
        ev.add("dtend", date.fromisoformat(event["koniec"][:10]) + timedelta(days=1))
    else:
        ev.add("dtstart", _warsaw_time(event["start"]))
        ev.add("dtend", _warsaw_time(event["koniec"]))
    cal.add_component(ev)
    return cal.to_ical().decode("utf-8")


def _warsaw_time(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=WARSAW) if parsed.tzinfo is None else parsed.astimezone(WARSAW)


def _edit_resource(data, uid, recurrence, values=None):
    """Preserve the resource; an occurrence edit creates/updates one exception.

    A row without RECURRENCE-ID edits the master (the entire series). Expanded
    rows always address exactly one occurrence, including when it is moved.
    Deleting an occurrence uses a cancelled exception and never deletes a series.
    """
    resource = Calendar.from_ical(data)
    components = [c for c in resource.walk("VEVENT") if str(c.get("UID") or "") == uid]
    master = next((c for c in components if c.get("RECURRENCE-ID") is None), None)
    component = next((c for c in components if
                      (recurrence_key(c["RECURRENCE-ID"].dt) if c.get("RECURRENCE-ID") else "") == recurrence), None)
    if component is None:
        if not recurrence or master is None:
            raise CaldavError("Nie znaleziono wydarzenia lub jego serii. Odśwież kalendarz.")
        component = deepcopy(master)
        for prop in ("RRULE", "RDATE", "EXRULE", "EXDATE"):
            component.pop(prop, None)
        original_start = recurrence_value(recurrence)
        series_start = master["DTSTART"].dt
        if isinstance(original_start, datetime):
            # RECURRENCE-ID must use the series' time representation, including
            # its TZID/floating convention, rather than the normalized DB key.
            original_start = original_start.astimezone(series_start.tzinfo or WARSAW)
            if series_start.tzinfo is None:
                original_start = original_start.replace(tzinfo=None)
        component.add("recurrence-id", original_start)
        # A cancelled exception still needs the original occurrence's DTSTART.
        duration = master["DTEND"].dt - master["DTSTART"].dt if master.get("DTEND") else master.get("DURATION")
        duration = duration.dt if hasattr(duration, "dt") else duration
        component["DTSTART"] = component["RECURRENCE-ID"]
        component.pop("DTEND", None)
        component.pop("DURATION", None)
        if duration:
            component.add("dtend", original_start + duration)
        resource.add_component(component)
    if values is None:
        component.pop("STATUS", None)
        component.add("status", "CANCELLED")
    else:
        replacement = Calendar.from_ical(build_ical(values, uid)).walk("VEVENT")[0]
        for prop in ("SUMMARY", "DESCRIPTION", "LOCATION", "DTSTART", "DTEND", "DURATION"):
            component.pop(prop, None)
            if prop in replacement:
                component[prop] = replacement[prop]
    component.pop("DTSTAMP", None)
    component.add("dtstamp", datetime.now(WARSAW))
    component["SEQUENCE"] = int(component.get("SEQUENCE", 0)) + 1
    return resource.to_ical().decode("utf-8")


def _load_remote(remote):
    # CalDAV's load reads content and ETag in the same response. Preserve it for
    # an If-Match write so another calendar client's newer resource wins.
    if callable(getattr(remote, "load", None)):
        remote.load()


def _save_remote(remote, data):
    from caldav.elements.dav import GetEtag

    props = getattr(remote, "props", None)
    etag = props.get(GetEtag.tag) if isinstance(props, dict) else None
    if etag:
        result = remote.client.put(remote.url, data, {"Content-Type": "text/calendar; charset=utf-8", "If-Match": etag})
        if result.status == 412:
            raise CaldavError("Wydarzenie zmieniono w innym programie. Odśwież kalendarz i ponów zmianę.")
        if result.status not in (201, 204):
            raise CaldavError(f"Serwer odrzucił zapis wydarzenia (HTTP {result.status}).")
        remote.data = data
    else:
        remote.data = data
        remote.save()


def _find_remote(calendar, uid: str):
    try:
        return calendar.event_by_uid(uid)
    except NotFoundError:
        return None


# ---------- operacje ----------
def _send_operation(op, calendar):
    key = op["remote_id"]
    uid, recurrence = split_remote_key(key)
    remote = _find_remote(calendar, uid)
    if op["action"] == "delete":
        if remote is not None:
            if recurrence:
                _load_remote(remote)
                _save_remote(remote, _edit_resource(remote.data, uid, recurrence))
            else:
                remote.delete()
    else:
        if remote is not None:
            _load_remote(remote)
            _save_remote(remote, _edit_resource(remote.data, uid, recurrence, op["payload"]))
        else:
            if recurrence:
                raise CaldavError("Seria wydarzenia została usunięta. Odśwież kalendarz.")
            calendar.save_event(build_ical(op["payload"], uid))
        dao_calendar.set_caldav_uid(op["event_id"], key, calendar_id=op["calendar_id"])
    outbox.acknowledge(op)


def push_event(pracownik_id: int, event: Dict[str, Any], calendar=None) -> None:
    try:
        cfg = get_config()
        scope = outbox.caldav_scope_values(cfg["url"], cfg["login"], cfg["kalendarz"])
        if not scope:
            raise CaldavError("Połączenie CalDAV nie jest skonfigurowane.")
        with outbox.calendar_lock(pracownik_id, "caldav", scope):
            if calendar is None:
                calendar = _selected_calendar(_connect(cfg), cfg)
            op = outbox.stage(pracownik_id, "caldav", scope, event["id"])
            if op:
                _send_operation(op, calendar)
    except CaldavError:
        raise
    except Exception as exc:
        raise CaldavError(friendly_error(exc)) from exc


def delete_remote(pracownik_id: int, uid: str, calendar=None, *, calendar_id=None) -> None:
    try:
        cfg = get_config()
        scope = outbox.caldav_scope_values(cfg["url"], cfg["login"], cfg["kalendarz"])
        if calendar_id is not None and calendar_id != scope:
            raise CaldavError("Zdarzenie należy do innego kalendarza. Usunięcie pozostaje w kolejce.")
        with outbox.calendar_lock(pracownik_id, "caldav", scope):
            if calendar is None:
                calendar = _selected_calendar(_connect(cfg), cfg)
            op = next((o for o in outbox.operations(pracownik_id, "caldav", scope)
                       if o["remote_id"] == uid and o["action"] == "delete"), None)
            if op:
                _send_operation(op, calendar)
            else:
                resource_uid, recurrence = split_remote_key(uid)
                remote = _find_remote(calendar, resource_uid)
                if remote is not None:
                    if recurrence:
                        _load_remote(remote)
                        _save_remote(remote, _edit_resource(remote.data, resource_uid, recurrence))
                    else:
                        remote.delete()
    except CaldavError:
        raise
    except Exception as exc:
        raise CaldavError(friendly_error(exc)) from exc


def pull_events(pracownik_id: int, calendar, days_back: int = 30, days_ahead: int = 365, *, scope=None) -> Dict[str, int]:
    now = datetime.now(WARSAW).replace(tzinfo=None)
    start, end = now - timedelta(days=days_back), now + timedelta(days=days_ahead)
    stats = {"pobrane": 0, "usuniete": 0}
    seen = set()
    scope = scope or outbox.caldav_scope()
    for remote in calendar.date_search(start, end):
        for values in parse_ical(remote.data):
            seen.add(values["caldav_uid"])
            if outbox.remote_pending(pracownik_id, "caldav", scope, values["caldav_uid"]):
                continue
            values["caldav_calendar_id"] = scope
            existing = dao_calendar.get_event_by_caldav_uid(pracownik_id, values["caldav_uid"], scope)
            if existing:
                values["id"] = existing["id"]
                values["firma_id"] = existing.get("firma_id")
                values["google_event_id"] = existing.get("google_event_id")
            dao_calendar.save_event(pracownik_id, values, from_remote=True)
            stats["pobrane"] += 1
    s_iso, e_iso = start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")
    for local in dao_calendar.list_caldav_events(pracownik_id, scope):
        if local["caldav_uid"] not in seen and s_iso <= local["start"] <= e_iso:
            dao_calendar.delete_event(local["id"], pracownik_id, from_remote=True)
            stats["usuniete"] += 1
    return stats


def sync(pracownik_id: int, days_back: int = 30, days_ahead: int = 365) -> Dict[str, int]:
    try:
        cfg = get_config()
        calendar = _selected_calendar(_connect(cfg), cfg)
        scope = outbox.caldav_scope_values(cfg["url"], cfg["login"], cfg["kalendarz"])
        with outbox.calendar_lock(pracownik_id, "caldav", scope):
            for event in dao_calendar.list_events_without_caldav(pracownik_id):
                outbox.stage(pracownik_id, "caldav", scope, event["id"])
            sent = 0
            for op in outbox.operations(pracownik_id, "caldav", scope):
                _send_operation(op, calendar)
                sent += 1
            stats = pull_events(pracownik_id, calendar, days_back, days_ahead, scope=scope)
            stats["wyslane"] = sent
            return stats
    except CaldavError:
        raise
    except Exception as exc:
        raise CaldavError(friendly_error(exc)) from exc
