from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn
from app.db import dao_calendar_sync as outbox

EVENT_FIELDS = (
    "tytul", "opis", "lokalizacja", "start", "koniec",
    "caly_dzien", "firma_id", "google_event_id", "google_calendar_id", "caldav_uid", "zrodlo", "caldav_calendar_id",
)


def list_events(pracownik_id: int, start: str, end: str) -> List[Dict[str, Any]]:
    """Wydarzenia nachodzące na przedział [start, end) (ISO 8601)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT w.*, COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa) AS firma_nazwa,
                   (w.pracownik_id != %s) AS udostepnione,
                   (SELECT imie || ' ' || nazwisko FROM pracownicy WHERE id = w.pracownik_id)
                       AS wlasciciel
            FROM wydarzenia w
            LEFT JOIN firmy f ON f.id = w.firma_id
            WHERE (w.pracownik_id = %s
                   OR EXISTS (SELECT 1 FROM wydarzenia_dostep d
                              WHERE d.wydarzenie_id = w.id AND d.pracownik_id = %s))
              AND w.start < %s AND w.koniec >= %s
            ORDER BY w.start
            """,
            (pracownik_id, pracownik_id, pracownik_id, end, start),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_event_access(event_id: int) -> List[int]:
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT pracownik_id FROM wydarzenia_dostep WHERE wydarzenie_id = %s", (event_id,)
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _event_task_marker(event_id: int) -> str:
    return f"[wydarzenie:{event_id}]"


def _sync_event_tasks(event_id: int, recipients: List[int]) -> None:
    """Pracownicy bez zakładki Kalendarz dostają wydarzenie jako zadanie (tylko gdy ich dotyczy)."""
    from app.security.authorization import allowed_tabs

    targets = [p for p in recipients if "Kalendarz" not in allowed_tabs(p)]
    marker = _event_task_marker(event_id)
    conn = get_conn()
    try:
        ev = conn.execute(
            "SELECT tytul, opis, start, firma_id FROM wydarzenia WHERE id = %s", (event_id,)
        ).fetchone()
        conn.execute("DELETE FROM zadania WHERE opis LIKE %s", (marker + "%",))
        if ev:
            for pid in targets:
                conn.execute(
                    "INSERT INTO zadania (tytul, opis, status, termin, firma_id, pracownik_id) "
                    "VALUES (%s, %s, 'Nowe', %s, %s, %s)",
                    (ev["tytul"], f"{marker} {ev['opis'] or ''}".strip(), str(ev["start"])[:10],
                     ev["firma_id"], pid),
                )
        conn.commit()
    finally:
        conn.close()


def set_event_access(event_id: int, pracownik_ids: List[int]) -> None:
    """Zastępuje listę pracowników z dostępem do wydarzenia (właściciel jest pomijany)."""
    conn = get_conn()
    try:
        owner = conn.execute(
            "SELECT pracownik_id FROM wydarzenia WHERE id = %s", (event_id,)
        ).fetchone()
        owner_id = owner[0] if owner else None
        recipients = [int(p) for p in set(pracownik_ids) if int(p) != owner_id]
        conn.execute("DELETE FROM wydarzenia_dostep WHERE wydarzenie_id = %s", (event_id,))
        conn.cursor().executemany(
            "INSERT INTO wydarzenia_dostep (wydarzenie_id, pracownik_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(event_id, p) for p in recipients],
        )
        conn.commit()
    finally:
        conn.close()
    _sync_event_tasks(event_id, recipients)


def get_event(event_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM wydarzenia WHERE id = %s", (event_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_event_by_google_id(pracownik_id: int, google_event_id: str, calendar_id: str | None = None) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM wydarzenia WHERE pracownik_id = %s AND google_event_id = %s "
            "AND (%s::text IS NULL OR google_calendar_id=%s)",
            (pracownik_id, google_event_id, calendar_id, calendar_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_event_by_caldav_uid(pracownik_id: int, uid: str, calendar_id: str | None = None) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM wydarzenia WHERE pracownik_id = %s AND caldav_uid = %s "
            "AND (%s::text IS NULL OR caldav_calendar_id=%s OR caldav_calendar_id IS NULL)",
            (pracownik_id, uid, calendar_id, calendar_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_caldav_events(pracownik_id: int, calendar_id: str | None = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM wydarzenia WHERE pracownik_id = %s AND caldav_uid IS NOT NULL "
            "AND (%s::text IS NULL OR caldav_calendar_id=%s OR caldav_calendar_id IS NULL)",
            (pracownik_id, calendar_id, calendar_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_events_without_caldav(pracownik_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM wydarzenia WHERE pracownik_id = %s AND caldav_uid IS NULL "
            "AND google_event_id IS NULL",
            (pracownik_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_caldav_uid(event_id: int, uid: Optional[str], zrodlo: str = "caldav", *, calendar_id=None) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE wydarzenia SET caldav_uid = %s, zrodlo = %s, caldav_calendar_id=COALESCE(%s,caldav_calendar_id) WHERE id = %s "
            "AND (%s::text IS NULL OR (google_calendar_id IS NULL AND "
            "(caldav_calendar_id=%s OR caldav_calendar_id IS NULL)))",
            (uid, zrodlo if uid else "lokalne", calendar_id, event_id, calendar_id, calendar_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_unsynced_events(pracownik_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM wydarzenia WHERE pracownik_id = %s "
            "AND google_event_id IS NULL AND google_calendar_id IS NOT NULL",
            (pracownik_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def save_event(pracownik_id: int, data: Dict[str, Any], *, from_remote=False) -> int:
    values = {k: data.get(k) for k in EVENT_FIELDS}
    values["caly_dzien"] = 1 if values["caly_dzien"] else 0
    values["zrodlo"] = values["zrodlo"] or "lokalne"
    if not values["tytul"] or not values["start"] or not values["koniec"]:
        raise ValueError("Tytuł, początek i koniec są wymagane.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        event_id = data.get("id")
        if not from_remote and not values.get("google_calendar_id") and not values.get("caldav_calendar_id"):
            values["caldav_calendar_id"] = outbox.caldav_scope(conn)
        if event_id:
            existing = conn.execute("SELECT * FROM wydarzenia WHERE id=%s AND pracownik_id=%s FOR UPDATE",
                                    (event_id, pracownik_id)).fetchone()
            if existing is None:
                return int(event_id)
            if from_remote and outbox.pending(conn, event_id):
                return int(event_id)
            if existing["google_calendar_id"] != values["google_calendar_id"]:
                values["google_event_id"] = None
            sets = ", ".join(f"{k} = %s" for k in EVENT_FIELDS)
            cur.execute(
                f"UPDATE wydarzenia SET {sets}, updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') "
                "WHERE id = %s AND pracownik_id = %s",
                (*values.values(), event_id, pracownik_id),
            )
        else:
            cols = ", ".join(EVENT_FIELDS)
            marks = ", ".join("%s" for _ in EVENT_FIELDS)
            cur.execute(
                f"INSERT INTO wydarzenia (pracownik_id, {cols}) VALUES (%s, {marks}) RETURNING id",
                (pracownik_id, *values.values()),
            )
            _new_id = cur.fetchone()[0]
            event_id = _new_id
        if not from_remote:
            event = dict(conn.execute("SELECT * FROM wydarzenia WHERE id=%s", (event_id,)).fetchone())
            outbox.queue_event(conn, pracownik_id, event)
        conn.commit()
        return int(event_id)
    finally:
        conn.close()


def set_google_event_id(
    event_id: int, google_event_id: Optional[str], google_calendar_id: Optional[str] = None, *, expected_calendar_id=None
) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE wydarzenia SET google_event_id = %s, "
            "google_calendar_id = COALESCE(%s, google_calendar_id), zrodlo = %s WHERE id = %s "
            "AND (%s::text IS NULL OR google_calendar_id=%s)",
            (google_event_id, google_calendar_id, "google" if google_event_id else "lokalne", event_id,
             expected_calendar_id, expected_calendar_id),
        )
        conn.commit()
    finally:
        conn.close()


def remove_local_duplicates(pracownik_id: int) -> int:
    """Usuwa lokalne wydarzenia (np. z importu .ics), które mają identyczny odpowiednik z Google."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            DELETE FROM wydarzenia l
            WHERE l.pracownik_id = %s
              AND l.google_event_id IS NULL AND l.caldav_uid IS NULL
              AND NOT EXISTS (SELECT 1 FROM calendar_outbox o WHERE o.event_id=l.id)
              AND EXISTS (
                SELECT 1 FROM wydarzenia g
                WHERE g.pracownik_id = l.pracownik_id AND g.google_event_id IS NOT NULL
                  AND lower(trim(g.tytul)) = lower(trim(l.tytul))
                  AND substr(g.start, 1, 16) = substr(l.start, 1, 16)
                  AND substr(g.koniec, 1, 16) = substr(l.koniec, 1, 16))
            """,
            (pracownik_id,),
        )
        removed = cur.rowcount
        conn.commit()
        return removed
    finally:
        conn.close()


def delete_event(event_id: int, pracownik_id: int, *, from_remote=False) -> None:
    conn = get_conn()
    try:
        event = conn.execute("SELECT * FROM wydarzenia WHERE id=%s AND pracownik_id=%s FOR UPDATE",
                             (event_id, pracownik_id)).fetchone()
        if event is None or (from_remote and outbox.pending(conn, event_id)):
            return
        if not from_remote:
            outbox.queue_deletion(conn, pracownik_id, dict(event))
        conn.execute("DELETE FROM zadania WHERE opis LIKE %s", (_event_task_marker(event_id) + "%",))
        conn.execute(
            "DELETE FROM wydarzenia WHERE id = %s AND pracownik_id = %s",
            (event_id, pracownik_id),
        )
        conn.commit()
    finally:
        conn.close()
