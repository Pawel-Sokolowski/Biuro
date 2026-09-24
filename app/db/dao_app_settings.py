# app/db/dao_app_settings.py
from __future__ import annotations

from typing import Dict, Iterable, Optional

from app.db.dao_conn import get_conn


TRUTHY_VALUES = {"1", "true", "tak", "t", "yes", "y", "x"}


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT wartosc FROM app_settings WHERE klucz = ?", (key,))
        row = cur.fetchone()
        if not row:
            return default
        return row["wartosc"] if hasattr(row, "keys") else row[0]
    finally:
        conn.close()


def get_setting_first(keys: Iterable[str], default: Optional[str] = None) -> Optional[str]:
    for key in keys:
        val = get_setting(key, None)
        if val is not None and str(val).strip() != "":
            return val
    return default


def get_setting_bool(keys: Iterable[str], default: bool = False) -> bool:
    val = get_setting_first(keys, None)
    if val is None:
        return default
    return str(val).strip().lower() in TRUTHY_VALUES


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO app_settings (klucz, wartosc)
            VALUES (?, ?)
            ON CONFLICT(klucz) DO UPDATE SET wartosc = excluded.wartosc
            """,
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def delete_setting(key: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM app_settings WHERE klucz = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def list_settings() -> Dict[str, str]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT klucz, wartosc FROM app_settings ORDER BY klucz")
        rows = cur.fetchall()
        out: Dict[str, str] = {}
        for r in rows:
            if hasattr(r, "keys"):
                out[r["klucz"]] = r["wartosc"]
            else:
                out[r[0]] = r[1]
        return out
    finally:
        conn.close()
