"""Dwukierunkowa synchronizacja kalendarza z Google Calendar (REST + OAuth2, bez dodatkowych bibliotek)."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests

from app.db import dao_calendar
from app.db.dao_app_settings import delete_setting, get_setting, set_setting
from app.utils.crypto import decrypt_password, encrypt_password

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/calendar/v3"
SCOPE = (
    "https://www.googleapis.com/auth/calendar.events "
    "https://www.googleapis.com/auth/calendar.readonly"
)
TIMEZONE = "Europe/Warsaw"
TIMEOUT = 30
TOKEN_KEY = "google_shared_refresh_token"
ACCESS_KEY = "google_calendar_access"

_token_cache: Dict[str, Any] = {"value": None, "expires": 0.0}


class GoogleCalendarError(Exception):
    pass


# ---------- dostęp pracowników do kalendarzy ----------
def get_access() -> Dict[str, Dict[str, Any]]:
    """{calendar_id: {"nazwa": str, "pracownicy": [id, ...]}} – kto widzi który kalendarz Google."""
    raw = get_setting(ACCESS_KEY) or "{}"
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for cal_id, info in data.items():
        if isinstance(info, dict):
            out[str(cal_id)] = {
                "nazwa": str(info.get("nazwa") or cal_id),
                "pracownicy": [int(p) for p in info.get("pracownicy", [])],
            }
    return out


def save_access(access: Dict[str, Dict[str, Any]]) -> None:
    set_setting(ACCESS_KEY, json.dumps(access))


def calendars_for(pracownik_id: int) -> List[Dict[str, str]]:
    """Kalendarze Google widoczne dla pracownika: przypisane lub wszystkie, jeśli ma zakładkę Kalendarz."""
    if not is_account_connected():
        return []
    from app.security.authorization import allowed_tabs

    sees_tab = "Kalendarz" in allowed_tabs(pracownik_id)
    result = [
        (0 if pracownik_id in info["pracownicy"] else 1 if info["pracownicy"] else 2,
         {"id": cal_id, "nazwa": info["nazwa"]})
        for cal_id, info in get_access().items()
        if sees_tab or pracownik_id in info["pracownicy"]
    ]
    # pierwszy na liście jest domyślny: własny, potem przypisany komukolwiek, na końcu reszta
    return [cal for _, cal in sorted(result, key=lambda r: r[0])]


def has_calendar_access(pracownik_id: int, calendar_id: Optional[str]) -> bool:
    return bool(calendar_id) and any(c["id"] == calendar_id for c in calendars_for(pracownik_id))


# ---------- kolejka zmian wykonanych offline ----------
def _pending_key(pracownik_id: int) -> str:
    return f"google_pending_changes_{pracownik_id}"


def _pending_changes(pracownik_id: int) -> Dict[str, Dict[str, str]]:
    raw = get_setting(_pending_key(pracownik_id)) or "{}"
    try:
        pending = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise GoogleCalendarError("Nie można odczytać kolejki zmian Google Calendar.") from exc
    if not isinstance(pending, dict):
        raise GoogleCalendarError("Nieprawidłowa kolejka zmian Google Calendar.")
    return {
        str(key): value for key, value in pending.items()
        if isinstance(value, dict) and value.get("kalendarz") and value.get("akcja")
    }


def _save_pending_changes(pracownik_id: int, pending: Dict[str, Dict[str, str]]) -> None:
    key = _pending_key(pracownik_id)
    if pending:
        set_setting(key, json.dumps(pending))
    else:
        delete_setting(key)


def mark_event_pending(
    pracownik_id: int, calendar_id: str, google_event_id: str, action: str = "upsert"
) -> None:
    if action not in {"upsert", "delete"}:
        raise ValueError("Nieobsługiwana operacja Google Calendar.")
    pending = _pending_changes(pracownik_id)
    pending[google_event_id] = {"kalendarz": calendar_id, "akcja": action}
    _save_pending_changes(pracownik_id, pending)


def _clear_pending(pracownik_id: int, google_event_id: str) -> None:
    pending = _pending_changes(pracownik_id)
    if google_event_id in pending:
        del pending[google_event_id]
        _save_pending_changes(pracownik_id, pending)


def _events_url(calendar_id: str) -> str:
    return f"{API_URL}/calendars/{quote(calendar_id, safe='')}/events"


def get_client_credentials() -> tuple[str, str]:
    client_id = (get_setting("google_client_id") or "").strip()
    secret_enc = get_setting("google_client_secret") or ""
    secret = decrypt_password(secret_enc) if secret_enc else ""
    return client_id, secret


def save_client_credentials(client_id: str, client_secret: str) -> None:
    set_setting("google_client_id", client_id.strip())
    if client_secret:
        set_setting("google_client_secret", encrypt_password(client_secret.strip()))


def is_account_connected() -> bool:
    return bool(get_setting(TOKEN_KEY))


def is_connected(pracownik_id: int) -> bool:
    """Prawda, gdy konto firmowe jest połączone i pracownik ma przypisany choć jeden kalendarz."""
    return bool(calendars_for(pracownik_id))


def disconnect() -> None:
    delete_setting(TOKEN_KEY)
    _token_cache.update(value=None, expires=0.0)


def connect(wait_seconds: int = 180) -> None:
    """Loguje firmowe konto Google (OAuth2: przeglądarka + lokalny callback) i zapisuje refresh token."""
    client_id, client_secret = get_client_credentials()
    if not client_id or not client_secret:
        raise GoogleCalendarError(
            "Brak danych klienta Google. Administrator musi je podać w Ustawieniach → Kalendarz."
        )

    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    result: Dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            if params.get("state", [""])[0] == state:
                result["code"] = params.get("code", [""])[0]
                result["error"] = params.get("error", [""])[0]
                done.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<html><body>Połączono. Możesz zamknąć to okno.</body></html>".encode("utf-8")
            )

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        webbrowser.open(
            AUTH_URL + "?" + urlencode({
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            })
        )
        if not done.wait(wait_seconds):
            raise GoogleCalendarError("Przekroczono czas oczekiwania na logowanie Google.")
    finally:
        server.shutdown()
        server.server_close()

    if result.get("error") or not result.get("code"):
        raise GoogleCalendarError(f"Google odrzucił logowanie: {result.get('error') or 'brak kodu'}")

    resp = requests.post(
        TOKEN_URL,
        data={
            "code": result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=TIMEOUT,
    )
    data = resp.json()
    if resp.status_code != 200 or not data.get("refresh_token"):
        raise GoogleCalendarError(data.get("error_description") or "Nie otrzymano tokenu od Google.")
    set_setting(TOKEN_KEY, encrypt_password(data["refresh_token"]))
    _token_cache.update(value=None, expires=0.0)


def _access_token() -> str:
    if _token_cache["value"] and _token_cache["expires"] > time.time() + 60:
        return _token_cache["value"]
    stored = get_setting(TOKEN_KEY)
    if not stored:
        raise GoogleCalendarError("Konto Google nie jest połączone.")
    client_id, client_secret = get_client_credentials()
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": decrypt_password(stored),
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    data = resp.json()
    if resp.status_code != 200 or "access_token" not in data:
        raise GoogleCalendarError(data.get("error_description") or "Nie udało się odświeżyć tokenu Google.")
    _token_cache.update(
        value=data["access_token"], expires=time.time() + int(data.get("expires_in", 3000))
    )
    return data["access_token"]


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_access_token()}"}


def _to_google_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "summary": event["tytul"],
        "description": event.get("opis") or "",
        "location": event.get("lokalizacja") or "",
    }
    if event.get("caly_dzien"):
        end = date.fromisoformat(event["koniec"][:10]) + timedelta(days=1)
        body["start"] = {"date": event["start"][:10]}
        body["end"] = {"date": end.isoformat()}
    else:
        body["start"] = {"dateTime": _rfc3339(event["start"]), "timeZone": TIMEZONE}
        body["end"] = {"dateTime": _rfc3339(event["koniec"]), "timeZone": TIMEZONE}
    return body


def _rfc3339(value: str) -> str:
    """Google wymaga sekund w dateTime (np. 2026-10-22T09:00:00)."""
    value = value.replace(" ", "T")
    return value + ":00" if len(value) == 16 else value


def _from_google(item: Dict[str, Any]) -> Dict[str, Any]:
    start, end = item.get("start", {}), item.get("end", {})
    if "date" in start:
        last_day = date.fromisoformat(end["date"]) - timedelta(days=1)
        start_s, end_s, all_day = start["date"], max(last_day.isoformat(), start["date"]), 1
    else:
        def local(value: str) -> str:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(
                tzinfo=None
            ).isoformat(timespec="minutes")

        start_s, end_s, all_day = local(start["dateTime"]), local(end["dateTime"]), 0
    return {
        "tytul": item.get("summary") or "(bez tytułu)",
        "opis": item.get("description") or "",
        "lokalizacja": item.get("location") or "",
        "start": start_s,
        "koniec": end_s,
        "caly_dzien": all_day,
        "google_event_id": item["id"],
        "zrodlo": "google",
    }


def _check(resp: requests.Response) -> None:
    if resp.status_code >= 400 and resp.status_code != 404:
        raise GoogleCalendarError(f"Google Calendar: {resp.status_code} {resp.text[:200]}")


def _require_access(pracownik_id: int, calendar_id: Optional[str]) -> str:
    if not calendar_id or not has_calendar_access(pracownik_id, calendar_id):
        raise GoogleCalendarError("Brak dostępu pracownika do wybranego kalendarza Google.")
    return calendar_id


def push_event(pracownik_id: int, event: Dict[str, Any]) -> None:
    """Wysyła nowe lub zmienione wydarzenie do jego kalendarza Google i zapisuje identyfikator."""
    calendar_id = _require_access(pracownik_id, event.get("google_calendar_id"))
    url = _events_url(calendar_id)
    headers = _headers()
    body = _to_google_body(event)
    gid = event.get("google_event_id")
    if gid:
        resp = requests.patch(f"{url}/{gid}", json=body, headers=headers, timeout=TIMEOUT)
        if resp.status_code != 404:
            _check(resp)
            dao_calendar.set_google_event_id(event["id"], gid, calendar_id)
            _clear_pending(pracownik_id, gid)
            return
    resp = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    _check(resp)
    dao_calendar.set_google_event_id(event["id"], resp.json()["id"], calendar_id)
    if gid:
        _clear_pending(pracownik_id, gid)


def delete_remote(pracownik_id: int, calendar_id: str, google_event_id: str) -> None:
    _require_access(pracownik_id, calendar_id)
    resp = requests.delete(
        f"{_events_url(calendar_id)}/{google_event_id}", headers=_headers(), timeout=TIMEOUT
    )
    if resp.status_code not in (200, 204, 404, 410):
        _check(resp)
    _clear_pending(pracownik_id, google_event_id)


# ---------- test połączenia i lista kalendarzy ----------
def list_account_calendars() -> List[Dict[str, str]]:
    """Kalendarze konta firmowego, do których można zapisywać."""
    calendars: List[Dict[str, str]] = []
    params: Dict[str, Any] = {"minAccessRole": "writer", "maxResults": 250}
    while True:
        resp = requests.get(
            f"{API_URL}/users/me/calendarList", params=params, headers=_headers(), timeout=TIMEOUT
        )
        if resp.status_code >= 400:
            raise GoogleCalendarError(f"Google Calendar: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        for item in data.get("items", []):
            calendars.append({
                "id": item["id"],
                "nazwa": item.get("summaryOverride") or item.get("summary") or item["id"],
            })
        token = data.get("nextPageToken")
        if not token:
            return calendars
        params["pageToken"] = token


def test_connection() -> str:
    """Zwraca komunikat o powodzeniu albo rzuca GoogleCalendarError."""
    client_id, client_secret = get_client_credentials()
    if not client_id or not client_secret:
        raise GoogleCalendarError("Podaj Client ID i Client Secret klienta OAuth Google.")
    if not is_account_connected():
        raise GoogleCalendarError("Konto Google nie jest połączone. Kliknij „Połącz konto Google”.")
    try:
        calendars = list_account_calendars()
    except requests.RequestException as exc:
        raise GoogleCalendarError(f"Brak połączenia z Google: {exc}") from exc
    return f"Połączono z Google. Dostępne kalendarze do zapisu: {len(calendars)}."


def sync(pracownik_id: int, days_back: int = 30, days_ahead: int = 365) -> Dict[str, int]:
    """Wysyła lokalne zmiany, a następnie pobiera zmiany z kalendarzy Google przypisanych pracownikowi."""
    stats = {"wyslane": 0, "pobrane": 0, "usuniete": 0}
    allowed = {c["id"] for c in calendars_for(pracownik_id)}
    if not allowed:
        raise GoogleCalendarError("Nie przypisano Ci żadnego kalendarza Google.")

    for google_event_id, change in list(_pending_changes(pracownik_id).items()):
        calendar_id = change["kalendarz"]
        if calendar_id not in allowed:
            _clear_pending(pracownik_id, google_event_id)
            continue
        if change["akcja"] == "delete":
            delete_remote(pracownik_id, calendar_id, google_event_id)
        else:
            event = dao_calendar.get_event_by_google_id(pracownik_id, google_event_id)
            if event:
                push_event(pracownik_id, event)
            else:
                delete_remote(pracownik_id, calendar_id, google_event_id)
        stats["wyslane"] += 1

    for event in dao_calendar.list_unsynced_events(pracownik_id):
        if event.get("google_calendar_id") in allowed:
            push_event(pracownik_id, event)
            stats["wyslane"] += 1

    headers = _headers()
    now = datetime.utcnow()
    base_params: Dict[str, Any] = {
        "timeMin": (now - timedelta(days=days_back)).isoformat() + "Z",
        "timeMax": (now + timedelta(days=days_ahead)).isoformat() + "Z",
        "singleEvents": "true",
        "showDeleted": "true",
        "maxResults": 250,
    }
    for calendar_id in allowed:
        _pull_calendar(pracownik_id, calendar_id, headers, dict(base_params), stats)
    stats["usuniete"] += dao_calendar.remove_local_duplicates(pracownik_id)
    return stats


def _pull_calendar(
    pracownik_id: int, calendar_id: str, headers: Dict[str, str],
    params: Dict[str, Any], stats: Dict[str, int],
) -> None:
    url = _events_url(calendar_id)
    while True:
        resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        _check(resp)
        data = resp.json()
        for item in data.get("items", []):
            existing = dao_calendar.get_event_by_google_id(pracownik_id, item["id"])
            if item.get("status") == "cancelled":
                if existing:
                    dao_calendar.delete_event(existing["id"], pracownik_id)
                    stats["usuniete"] += 1
                continue
            if "start" not in item or "end" not in item:
                continue
            values = _from_google(item)
            values["google_calendar_id"] = calendar_id
            if existing:
                values["id"] = existing["id"]
                values["firma_id"] = existing.get("firma_id")
            dao_calendar.save_event(pracownik_id, values)
            stats["pobrane"] += 1
        token = data.get("nextPageToken")
        if not token:
            return
        params["pageToken"] = token
