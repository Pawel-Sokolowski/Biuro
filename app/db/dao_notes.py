import sqlite3
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.db.dao_conn import get_conn


def get_latest_notes_for_firms(firm_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if not firm_ids:
        return {}
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in firm_ids)
    cur.execute(
        f"""
        SELECT cn.firma_id, cn.id AS note_id, cn.tresc, cn.autor_id, cn.data_utworzenia,
               p.imie || ' ' || p.nazwisko AS autor_nazwa
        FROM company_notes cn
        LEFT JOIN pracownicy p ON p.id = cn.autor_id
        WHERE cn.firma_id IN ({placeholders})
          AND cn.data_utworzenia = (
              SELECT MAX(data_utworzenia) FROM company_notes cn2 WHERE cn2.firma_id = cn.firma_id
          )
    """,
        tuple(firm_ids),
    )
    rows = cur.fetchall()
    conn.close()
    result = {}
    for r in rows:
        result[r["firma_id"]] = {
            "id": r["note_id"],
            "tresc": r["tresc"],
            "autor_id": r["autor_id"],
            "autor_nazwa": r["autor_nazwa"] or "Nieznany",
            "data_utworzenia": r["data_utworzenia"],
        }
    return result


def get_notes_for_company(firma_id: int, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    q = """
        SELECT cn.id, cn.tresc, cn.autor_id, cn.data_utworzenia,
               p.imie || ' ' || p.nazwisko AS autor_nazwa
        FROM company_notes cn
        LEFT JOIN pracownicy p ON p.id = cn.autor_id
        WHERE cn.firma_id = ?
        ORDER BY cn.data_utworzenia DESC
    """
    if limit:
        q += " LIMIT ?"
        cur.execute(q, (firma_id, limit))
    else:
        cur.execute(q, (firma_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def save_company_note(firma_id: int, tresc: str, autor_id: Optional[int]) -> int:
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO company_notes (firma_id, tresc, autor_id, data_utworzenia)
        VALUES (?, ?, ?, ?)
    """,
        (firma_id, tresc, autor_id, now),
    )
    nid = cur.lastrowid
    conn.commit()
    conn.close()
    return nid


def update_company_note(note_id: int, new_text: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "UPDATE company_notes SET tresc = ?, data_utworzenia = ? WHERE id = ?",
        (new_text, now, note_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def delete_company_note(note_id: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM company_notes WHERE id = ?", (note_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
