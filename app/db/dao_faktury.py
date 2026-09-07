# app/db/dao_faktury.py
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from app.db.dao_conn import get_conn


def create_or_update_invoice(
    firma_id: int,
    miesiac: str,
    pozycje: List[Dict[str, Any]],
    kwota_netto: float,
    kwota_brutto: float
) -> int:
    conn = get_conn()
    cur = conn.cursor()

    pozycje_json = json.dumps(pozycje, ensure_ascii=False)

    today = datetime.now().strftime("%Y-%m-%d")
    termin = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    cur.execute("""
        SELECT id FROM faktury
        WHERE firma_id = ? AND miesiac = ?
    """, (firma_id, miesiac))
    row = cur.fetchone()

    if row:
        invoice_id = row["id"]
        cur.execute("""
            UPDATE faktury
            SET kwota_netto = ?, kwota_brutto = ?, pozycje_json = ?,
                data_wystawienia = ?, data_sprzedazy = ?, termin_platnosci = ?,
                data_utworzenia = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            kwota_netto, kwota_brutto, pozycje_json,
            today, today, termin,
            invoice_id
        ))
    else:
        cur.execute("""
            INSERT INTO faktury (
                firma_id, miesiac,
                kwota_netto, kwota_brutto,
                data_wystawienia, data_sprzedazy, termin_platnosci,
                pozycje_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            firma_id, miesiac,
            kwota_netto, kwota_brutto,
            today, today, termin,
            pozycje_json
        ))
        invoice_id = cur.lastrowid

    conn.commit()
    conn.close()
    return invoice_id


def mark_exported(invoice_ids: List[int]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    for inv_id in invoice_ids:
        cur.execute("UPDATE faktury SET eksportowana = 1 WHERE id = ?", (inv_id,))
    conn.commit()
    conn.close()


def get_invoice_for_company_month(firma_id: int, miesiac: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM faktury
        WHERE firma_id = ? AND miesiac = ?
    """, (firma_id, miesiac))
    row = cur.fetchone()
    conn.close()

    return dict(row) if row else None


def get_invoices_for_company(firma_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT f.*, c.nazwa AS firma_nazwa
        FROM faktury f
        JOIN firmy c ON f.firma_id = c.id
        WHERE f.firma_id = ? AND f.eksportowana = 0
        ORDER BY f.miesiac DESC
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()

    return [dict(r) for r in rows]


def get_exported_invoices() -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT f.*, c.nazwa AS firma_nazwa
        FROM faktury f
        JOIN firmy c ON f.firma_id = c.id
        WHERE f.eksportowana = 1
        ORDER BY f.data_utworzenia DESC
    """)
    rows = cur.fetchall()
    conn.close()

    return [dict(r) for r in rows]
