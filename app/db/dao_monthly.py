# app/db/dao_monthly.py
import sqlite3
from typing import Dict, Any, Optional
from app.db.dao_conn import get_conn
from app.utils.months import month_bounds


def get_month_status(firma_id: int, miesiac: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM statusy_miesieczne
        WHERE firma_id = ? AND miesiac = ?
    """, (firma_id, miesiac))

    row = cur.fetchone()
    conn.close()

    return dict(row) if row else None


def save_month_status(firma_id: int, miesiac: str, payload: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO statusy_miesieczne (
            firma_id, miesiac,
            etap, ilosc_dok,
            sekundy_ksiegowosc, sekundy_kadry,
            wartosc_uslug, dokumenty_dostarczone
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(firma_id, miesiac)
        DO UPDATE SET
            etap = excluded.etap,
            ilosc_dok = excluded.ilosc_dok,
            sekundy_ksiegowosc = excluded.sekundy_ksiegowosc,
            sekundy_kadry = excluded.sekundy_kadry,
            wartosc_uslug = excluded.wartosc_uslug,
            dokumenty_dostarczone = excluded.dokumenty_dostarczone
    """, (
        firma_id,
        miesiac,
        payload.get("etap", "Brak"),
        payload.get("ilosc_dok", 0),
        payload.get("sekundy_ksiegowosc", 0),
        payload.get("sekundy_kadry", 0),
        payload.get("wartosc_uslug", 0.0),
        1 if payload.get("dokumenty_dostarczone") else 0,
    ))

    conn.commit()
    conn.close()


def count_documents_for_month(firma_id: int, miesiac: str) -> int:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    start, end = month_bounds(miesiac)
    cur.execute("""
        SELECT COUNT(*) AS cnt
        FROM dokumenty
        WHERE firma_id = ?
          AND data_wyst >= ?
          AND data_wyst < ?
    """, (firma_id, start, end))

    row = cur.fetchone()
    conn.close()

    return int(row["cnt"] or 0)
