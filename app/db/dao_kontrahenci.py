from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn

FIELDS = ("typ", "nazwa", "nip", "regon", "adres", "kod", "miasto", "kraj", "email", "telefon")


def _values(data: Dict[str, Any]) -> Dict[str, Any]:
    typ = (data.get("typ") or "").strip()
    nazwa = (data.get("nazwa") or "").strip()
    if typ not in ("osoba_fizyczna", "firma"):
        raise ValueError("Wybierz typ kontrahenta.")
    if not nazwa:
        raise ValueError("Nazwa kontrahenta jest wymagana.")
    nip = "".join(ch for ch in (data.get("nip") or "") if ch.isdigit())
    if nip and (len(nip) != 10 or not _valid_nip(nip)):
        raise ValueError("Podany NIP jest nieprawid?owy.")
    result = {key: (data.get(key) or "").strip() or None for key in FIELDS}
    result.update(typ=typ, nazwa=nazwa, nip=nip or None, kraj=(data.get("kraj") or "Polska").strip() or "Polska")
    return result


def _valid_nip(nip: str) -> bool:
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    checksum = sum(int(nip[i]) * weights[i] for i in range(9)) % 11
    return checksum != 10 and checksum == int(nip[9])


def save_kontrahent(data: Dict[str, Any], kontrahent_id: Optional[int] = None) -> int:
    values = _values(data)
    conn = get_conn()
    try:
        if kontrahent_id:
            assignments = ", ".join(f"{key} = ?" for key in FIELDS)
            cur = conn.execute(f"UPDATE kontrahenci SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                               [values[key] for key in FIELDS] + [kontrahent_id])
            if not cur.rowcount:
                raise ValueError("Nie znaleziono kontrahenta.")
            result = int(kontrahent_id)
        else:
            columns = ", ".join(FIELDS)
            marks = ", ".join("?" for _ in FIELDS)
            cur = conn.execute(f"INSERT INTO kontrahenci ({columns}) VALUES ({marks})", [values[key] for key in FIELDS])
            result = int(cur.lastrowid)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_kontrahent(kontrahent_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM kontrahenci WHERE id = ?", (kontrahent_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_kontrahenci() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM kontrahenci ORDER BY nazwa COLLATE NOCASE, id")]
    finally:
        conn.close()


def search_kontrahenci(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    term = (query or "").strip()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        if term:
            like = f"%{term}%"
            rows = conn.execute("SELECT * FROM kontrahenci WHERE nazwa LIKE ? COLLATE NOCASE OR nip LIKE ? ORDER BY nazwa COLLATE NOCASE LIMIT ?",
                                (like, like, max(1, int(limit)))).fetchall()
        else:
            rows = conn.execute("SELECT * FROM kontrahenci ORDER BY nazwa COLLATE NOCASE LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_kontrahent(kontrahent_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM kontrahenci WHERE id = ?", (kontrahent_id,))
        conn.commit()
    finally:
        conn.close()
