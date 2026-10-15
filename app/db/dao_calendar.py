from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn

EVENT_FIELDS = (
    "tytul", "opis", "lokalizacja", "start", "koniec",
    "caly_dzien", "firma_id", "google_event_id",
)


def list_events(pracownik_id: int, start: str, end: str) -> List[Dict[str, Any]]:
    """Wydarzenia nachodzące na przedział [start, end) (ISO 8601)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT w.*, COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa) AS firma_nazwa,
                   (w.pracownik_id != ?) AS udostepnione,
                   (SELECT imie || ' ' || nazwisko FROM pracownicy WHERE id = w.pracownik_id)
                       AS wlasciciel
            FROM wydarzenia w
            LEFT JOIN firmy f ON f.id = w.firma_id
            WHERE (w.pracownik_id = ?
                   OR EXISTS (SELECT 1 FROM wydarzenia_dostep d
                              WHERE d.wydarzenie_id = w.id AND d.pracownik_id = ?))
              AND w.start < ? AND w.koniec >= ?
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
            "SELECT pracownik_id FROM wydarzenia_dostep WHERE wydarzenie_id = ?", (event_id,)
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def set_event_access(event_id: int, pracownik_ids: List[int]) -> None:
    """Zastępuje listę pracowników z dostępem do wydarzenia (właściciel jest pomijany)."""
    conn = get_conn()
    try:
        owner = conn.execute(
            "SELECT pracownik_id FROM wydarzenia WHERE id = ?", (event_id,)
        ).fetchone()
        owner_id = owner[0] if owner else None
        conn.execute("DELETE FROM wydarzenia_dostep WHERE wydarzenie_id = ?", (event_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO wydarzenia_dostep (wydarzenie_id, pracownik_id) VALUES (?, ?)",
            [(event_id, int(p)) for p in set(pracownik_ids) if int(p) != owner_id],
        )
        conn.commit()
    finally:
        conn.close()


def get_event(event_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM wydarzenia WHERE id = ?", (event_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_event_by_google_id(pracownik_id: int, google_event_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM wydarzenia WHERE pracownik_id = ? AND google_event_id = ?",
            (pracownik_id, google_event_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_unsynced_events(pracownik_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM wydarzenia WHERE pracownik_id = ? AND google_event_id IS NULL",
            (pracownik_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def save_event(pracownik_id: int, data: Dict[str, Any]) -> int:
    values = {k: data.get(k) for k in EVENT_FIELDS}
    values["caly_dzien"] = 1 if values["caly_dzien"] else 0
    if not values["tytul"] or not values["start"] or not values["koniec"]:
        raise ValueError("Tytuł, początek i koniec są wymagane.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        event_id = data.get("id")
        if event_id:
            sets = ", ".join(f"{k} = ?" for k in EVENT_FIELDS)
            cur.execute(
                f"UPDATE wydarzenia SET {sets}, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND pracownik_id = ?",
                (*values.values(), event_id, pracownik_id),
            )
        else:
            cols = ", ".join(EVENT_FIELDS)
            marks = ", ".join("?" for _ in EVENT_FIELDS)
            cur.execute(
                f"INSERT INTO wydarzenia (pracownik_id, {cols}) VALUES (?, {marks})",
                (pracownik_id, *values.values()),
            )
            event_id = cur.lastrowid
        conn.commit()
        return int(event_id)
    finally:
        conn.close()


def set_google_event_id(event_id: int, google_event_id: Optional[str]) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE wydarzenia SET google_event_id = ? WHERE id = ?",
            (google_event_id, event_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_event(event_id: int, pracownik_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM wydarzenia WHERE id = ? AND pracownik_id = ?",
            (event_id, pracownik_id),
        )
        conn.commit()
    finally:
        conn.close()
