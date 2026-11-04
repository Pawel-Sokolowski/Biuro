"""Odczyt powiadomień push Saldeo (toasty Windows z Chrome) i oznaczanie "Dokumenty dostarczone".

Treść powiadomienia: "Klient (Rajch) informuje o dostarczeniu kompletu dokumentów za 10/2026",
gdzie "Rajch" to nazwa skrócona z Saldeo (pole firmy `nazwa_saldeo`).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.db.dao_conn import get_conn

logger = logging.getLogger(__name__)

COMPLETE_RE = re.compile(
    r"Klient\s*\((?P<name>.+?)\)\s+informuje\s+o\s+dostarczeniu\s+kompletu\s+dokument\w*\s+"
    r"za\s+(?P<month>\d{1,2})\s*/\s*(?P<year>\d{4})",
    re.IGNORECASE | re.DOTALL,
)
BROWSER_HINTS = ("chrome", "edge", "firefox", "opera", "brave")


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def parse_notification(text: str) -> Optional[Tuple[str, str]]:
    """Zwraca (nazwa z Saldeo, 'YYYY-MM') albo None."""
    match = COMPLETE_RE.search(text or "")
    if not match:
        return None
    month = int(match.group("month"))
    if not 1 <= month <= 12:
        return None
    return match.group("name").strip(), f"{int(match.group('year')):04d}-{month:02d}"


def mark_documents_complete(name: str, month: str, today: Optional[datetime.date] = None) -> str:
    """'ok' - ustawiono, 'juz' - już było, 'brak_firmy' - nie znaleziono firmy o tej nazwie w Saldeo."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, nazwa_saldeo_firmy AS nazwa_saldeo FROM firmy WHERE COALESCE(TRIM(nazwa_saldeo_firmy), '') <> ''"
        )
        firma_id = next((r["id"] for r in cur.fetchall() if _fold(r["nazwa_saldeo"]) == _fold(name)), None)
        if firma_id is None:
            return "brak_firmy"
        cur.execute(
            """
            INSERT INTO statusy_miesieczne (firma_id, miesiac, dokumenty_dostarczone, data_dostarczenia)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (firma_id, miesiac) DO UPDATE SET
                dokumenty_dostarczone = 1, data_dostarczenia = excluded.data_dostarczenia
            WHERE COALESCE(statusy_miesieczne.dokumenty_dostarczone, 0) = 0
            """,
            (firma_id, month, (today or datetime.date.today()).isoformat()),
        )
        changed = cur.rowcount or 0
        conn.commit()
        return "ok" if changed else "juz"
    finally:
        conn.close()


async def _read_toasts_async() -> List[Dict[str, str]]:
    from winrt.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
    from winrt.windows.ui.notifications.management import (
        UserNotificationListener, UserNotificationListenerAccessStatus,
    )

    listener = UserNotificationListener.current
    status = await listener.request_access_async()
    if status != UserNotificationListenerAccessStatus.ALLOWED:
        raise PermissionError("Brak zgody Windows na odczyt powiadomień")
    items = []
    for notification in await listener.get_notifications_async(NotificationKinds.TOAST):
        try:
            app_name = notification.app_info.display_info.display_name or ""
            binding = notification.notification.visual.get_binding(KnownNotificationBindings.toast_generic)
            texts = [element.text for element in binding.get_text_elements()] if binding else []
        except Exception:
            continue
        items.append({"id": str(notification.id), "app": app_name, "text": "\n".join(texts)})
    return items


def read_windows_toasts() -> List[Dict[str, str]]:
    return asyncio.run(_read_toasts_async())


class SaldeoPushWatcher:
    """Odpytywany cyklicznie (poza wątkiem GUI); każdy toast jest obsługiwany raz."""

    def __init__(self):
        self._seen: set = set()
        self.available = True
        self.last_error = ""

    def poll(self) -> List[Tuple[str, str, str]]:
        """Zwraca [(nazwa, miesiąc, wynik)] dla nowych powiadomień o komplecie dokumentów."""
        if not self.available:
            return []
        try:
            toasts = read_windows_toasts()
        except ImportError:
            self.available, self.last_error = False, "Brak pakietu winrt (pip install winrt-Windows.UI.Notifications.Management)"
            logger.info(self.last_error)
            return []
        except PermissionError as exc:
            self.available, self.last_error = False, str(exc)
            logger.warning("Powiadomienia Saldeo: %s", exc)
            return []
        except Exception:
            logger.debug("Powiadomienia Saldeo: błąd odczytu", exc_info=True)
            return []

        results = []
        for toast in toasts:
            if toast["id"] in self._seen:
                continue
            self._seen.add(toast["id"])
            parsed = parse_notification(toast["text"])
            if not parsed:
                continue
            try:
                outcome = mark_documents_complete(*parsed)
            except Exception:
                logger.exception("Powiadomienia Saldeo: błąd zapisu statusu")
                continue
            results.append((parsed[0], parsed[1], outcome))
        return results
