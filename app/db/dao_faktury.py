# app/db/dao_faktury.py
import json
import sqlite3
from typing import List, Dict, Any, Optional

from app.db.dao_conn import get_conn


def create_or_update_invoice(
    firma_id: int,
    miesiac: str,
    pozycje: List[Dict[str, Any]],
    kwota_netto: float,
    kwota_brutto: float
) -> int:
    """
    Tworzy lub aktualizuje fakturę miesięczną dla firmy.
    Jedna faktura per firma per miesiąc (UNIQUE(firma_id, miesiac)).
    Zwraca ID faktury.
    """
    conn = get_conn()
    cur = conn.cursor()

    pozycje_json = json.dumps(pozycje, ensure_ascii=False)

    cur.execute("""
        SELECT id FROM faktury
        WHERE firma_id = ? AND miesiac = ?
    """, (firma_id, miesiac))
    row = cur.fetchone()

    if row:
        invoice_id = row["id"]
        cur.execute("""
            UPDATE faktury
            SET kwota_netto = ?, kwota_brutto = ?, pozycje_json = ?, data_utworzenia = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (kwota_netto, kwota_brutto, pozycje_json, invoice_id))
    else:
        cur.execute("""
            INSERT INTO faktury (firma_id, miesiac, kwota_netto, kwota_brutto, pozycje_json)
            VALUES (?, ?, ?, ?, ?)
        """, (firma_id, miesiac, kwota_netto, kwota_brutto, pozycje_json))
        invoice_id = cur.lastrowid

    conn.commit()
    conn.close()
    return invoice_id


def get_invoice_by_id(invoice_id: int) -> Optional[Dict[str, Any]]:
    """
    Zwraca pojedynczą fakturę po ID.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, firma_id, miesiac, kwota_netto, kwota_brutto, pozycje_json, data_utworzenia
        FROM faktury
        WHERE id = ?
    """, (invoice_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row["id"],
        "firma_id": row["firma_id"],
        "miesiac": row["miesiac"],
        "kwota_netto": row["kwota_netto"],
        "kwota_brutto": row["kwota_brutto"],
        "pozycje": json.loads(row["pozycje_json"] or "[]"),
        "data_utworzenia": row["data_utworzenia"],
    }


def get_invoices_for_company(firma_id: int) -> List[Dict[str, Any]]:
    """
    Zwraca listę faktur dla danej firmy (wszystkie miesiące).
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, firma_id, miesiac, kwota_netto, kwota_brutto, pozycje_json, data_utworzenia
        FROM faktury
        WHERE firma_id = ?
        ORDER BY miesiac DESC
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": r["id"],
            "firma_id": r["firma_id"],
            "miesiac": r["miesiac"],
            "kwota_netto": r["kwota_netto"],
            "kwota_brutto": r["kwota_brutto"],
            "pozycje": json.loads(r["pozycje_json"] or "[]"),
            "data_utworzenia": r["data_utworzenia"],
        })
    return out


def get_invoice_for_company_month(firma_id: int, miesiac: str) -> Optional[Dict[str, Any]]:
    """
    Zwraca fakturę dla firmy i konkretnego miesiąca, jeśli istnieje.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, firma_id, miesiac, kwota_netto, kwota_brutto, pozycje_json, data_utworzenia
        FROM faktury
        WHERE firma_id = ? AND miesiac = ?
    """, (firma_id, miesiac))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row["id"],
        "firma_id": row["firma_id"],
        "miesiac": row["miesiac"],
        "kwota_netto": row["kwota_netto"],
        "kwota_brutto": row["kwota_brutto"],
        "pozycje": json.loads(row["pozycje_json"] or "[]"),
        "data_utworzenia": row["data_utworzenia"],
    }


def delete_invoice(invoice_id: int) -> None:
    """
    Usuwa fakturę po ID.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM faktury WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
