"""Dwukierunkowa synchronizacja kalendarza z Google Calendar (REST + OAuth2, bez dodatkowych bibliotek)."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from app.db import dao_calendar
from app.db.dao_app_settings import delete_setting, get_setting, set_setting
from app.utils.crypto import decrypt_password, encrypt_password

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
TIMEZONE = "Europe/Warsaw"
TIMEOUT = 30


class GoogleCalendarError(Exception):
    pass


def _token_key(pracownik_id: int) -> str:
    return f"google_refresh_token_{pracownik_id}"


def get_client_credentials() -> tuple[str, str]:
    client_id = (get_setting("google_client_id") or "").strip()
    secret_enc = get_setting("google_client_secret") or ""
    secret = decrypt_password(secret_enc) if secret_enc else ""
    return client_id, secret


def save_client_credentials(client_id: str, client_secret: str) -> None:
    set_setting("google_client_id", client_id.strip())
    if client_secret:
        set_setting("google_client_secret", encrypt_password(client_secret.strip()))


def is_connected(pracownik_id: int) -> bool:
    return bool(get_setting(_token_key(pracownik_id)))


def disconnect(pracownik_id: int) -> None:
    delete_setting(_token_key(pracownik_id))


def connect(pracownik_id: int, wait_seconds: int = 180) -> None:
    """Przeprowadza logowanie OAuth2 (przeglądarka + lokalny callback) i zapisuje refresh token."""
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
    set_setting(_token_key(pracownik_id), encrypt_password(data["refresh_token"]))


def _access_token(pracownik_id: int) -> str:
    stored = get_setting(_token_key(pracownik_id))
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
    return data["access_token"]


def _headers(pracownik_id: int) -> Dict[str, str]:
    return {"Authorization": f"Bearer {_access_token(pracownik_id)}"}


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
        body["start"] = {"dateTime": event["start"], "timeZone": TIMEZONE}
        body["end"] = {"dateTime": event["koniec"], "timeZone": TIMEZONE}
    return body


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
    }


def _check(resp: requests.Response) -> None:
    if resp.status_code >= 400 and resp.status_code != 404:
        raise GoogleCalendarError(f"Google Calendar: {resp.status_code} {resp.text[:200]}")


def push_event(pracownik_id: int, event: Dict[str, Any]) -> None:
    """Wysyła nowe lub zmienione wydarzenie do Google i zapisuje jego identyfikator."""
    headers = _headers(pracownik_id)
    body = _to_google_body(event)
    gid = event.get("google_event_id")
    if gid:
        resp = requests.patch(f"{EVENTS_URL}/{gid}", json=body, headers=headers, timeout=TIMEOUT)
        if resp.status_code != 404:
            _check(resp)
            return
    resp = requests.post(EVENTS_URL, json=body, headers=headers, timeout=TIMEOUT)
    _check(resp)
    dao_calendar.set_google_event_id(event["id"], resp.json()["id"])


def delete_remote(pracownik_id: int, google_event_id: str) -> None:
    resp = requests.delete(
        f"{EVENTS_URL}/{google_event_id}", headers=_headers(pracownik_id), timeout=TIMEOUT
    )
    if resp.status_code not in (200, 204, 404, 410):
        _check(resp)


def sync(pracownik_id: int, days_back: int = 30, days_ahead: int = 365) -> Dict[str, int]:
    """Wysyła lokalne wydarzenia bez odpowiednika w Google, a następnie pobiera zmiany z Google."""
    stats = {"wyslane": 0, "pobrane": 0, "usuniete": 0}
    for event in dao_calendar.list_unsynced_events(pracownik_id):
        push_event(pracownik_id, event)
        stats["wyslane"] += 1

    headers = _headers(pracownik_id)
    now = datetime.utcnow()
    params: Dict[str, Any] = {
        "timeMin": (now - timedelta(days=days_back)).isoformat() + "Z",
        "timeMax": (now + timedelta(days=days_ahead)).isoformat() + "Z",
        "singleEvents": "true",
        "showDeleted": "true",
        "maxResults": 250,
    }
    while True:
        resp = requests.get(EVENTS_URL, params=params, headers=headers, timeout=TIMEOUT)
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
            if existing:
                values["id"] = existing["id"]
                values["firma_id"] = existing.get("firma_id")
            dao_calendar.save_event(pracownik_id, values)
            stats["pobrane"] += 1
        token = data.get("nextPageToken")
        if not token:
            break
        params["pageToken"] = token
    return stats
