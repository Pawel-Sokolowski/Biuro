"""Synchronizacja kalendarza przez CalDAV (biblioteka `caldav`) – Google Calendar lub dowolny serwer CalDAV."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from icalendar import Calendar, Event

from app.db import dao_calendar
from app.db.dao_app_settings import get_setting, set_setting
from app.utils.crypto import decrypt_password, encrypt_password

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
    return cals[0]


# ---------- konwersja VEVENT <-> wydarzenia ----------
def _local_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value


def parse_ical(data: Any) -> List[Dict[str, Any]]:
    """Zamienia dane iCalendar na słowniki wierszy `wydarzenia` (z kluczem caldav_uid)."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    out: Dict[str, Dict[str, Any]] = {}
    for comp in Calendar.from_ical(data).walk("VEVENT"):
        uid = str(comp.get("UID") or "").strip()
        if not uid or uid in out or comp.get("DTSTART") is None:
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
        out[uid] = {
            "tytul": str(comp.get("SUMMARY") or "(bez tytułu)"),
            "opis": str(comp.get("DESCRIPTION") or ""),
            "lokalizacja": str(comp.get("LOCATION") or ""),
            "start": s, "koniec": e, "caly_dzien": all_day,
            "caldav_uid": uid, "zrodlo": "caldav",
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
        ev.add("dtstart", datetime.fromisoformat(event["start"]))
        ev.add("dtend", datetime.fromisoformat(event["koniec"]))
    cal.add_component(ev)
    return cal.to_ical().decode("utf-8")


def _find_remote(calendar, uid: str):
    try:
        return calendar.event_by_uid(uid)
    except Exception:
        return None


# ---------- operacje ----------
def push_event(pracownik_id: int, event: Dict[str, Any], calendar=None) -> None:
    """Tworzy lub aktualizuje wydarzenie na serwerze i zapisuje jego UID."""
    try:
        if calendar is None:
            cfg = get_config()
            calendar = _selected_calendar(_connect(cfg), cfg)
        uid = event.get("caldav_uid") or f"{uuid.uuid4()}@biuro"
        ical = build_ical(event, uid)
        remote = _find_remote(calendar, uid) if event.get("caldav_uid") else None
        if remote is not None:
            remote.data = ical
            remote.save()
        else:
            calendar.save_event(ical)
        if not event.get("caldav_uid"):
            dao_calendar.set_caldav_uid(event["id"], uid)
    except CaldavError:
        raise
    except Exception as exc:
        raise CaldavError(friendly_error(exc)) from exc


def delete_remote(pracownik_id: int, uid: str, calendar=None) -> None:
    try:
        if calendar is None:
            cfg = get_config()
            calendar = _selected_calendar(_connect(cfg), cfg)
        remote = _find_remote(calendar, uid)
        if remote is not None:
            remote.delete()
    except CaldavError:
        raise
    except Exception as exc:
        raise CaldavError(friendly_error(exc)) from exc


def pull_events(pracownik_id: int, calendar, days_back: int = 30, days_ahead: int = 365) -> Dict[str, int]:
    now = datetime.now()
    start, end = now - timedelta(days=days_back), now + timedelta(days=days_ahead)
    stats = {"pobrane": 0, "usuniete": 0}
    seen = set()
    for remote in calendar.date_search(start, end):
        for values in parse_ical(remote.data):
            seen.add(values["caldav_uid"])
            existing = dao_calendar.get_event_by_caldav_uid(pracownik_id, values["caldav_uid"])
            if existing:
                values["id"] = existing["id"]
                values["firma_id"] = existing.get("firma_id")
                values["google_event_id"] = existing.get("google_event_id")
            dao_calendar.save_event(pracownik_id, values)
            stats["pobrane"] += 1
    s_iso, e_iso = start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")
    for local in dao_calendar.list_caldav_events(pracownik_id):
        if local["caldav_uid"] not in seen and s_iso <= local["start"] <= e_iso:
            dao_calendar.delete_event(local["id"], pracownik_id)
            stats["usuniete"] += 1
    return stats


def sync(pracownik_id: int, days_back: int = 30, days_ahead: int = 365) -> Dict[str, int]:
    try:
        cfg = get_config()
        calendar = _selected_calendar(_connect(cfg), cfg)
        sent = 0
        for event in dao_calendar.list_events_without_caldav(pracownik_id):
            push_event(pracownik_id, event, calendar)
            sent += 1
        stats = pull_events(pracownik_id, calendar, days_back, days_ahead)
        stats["wyslane"] = sent
        return stats
    except CaldavError:
        raise
    except Exception as exc:
        raise CaldavError(friendly_error(exc)) from exc
