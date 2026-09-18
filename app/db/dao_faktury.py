# app/db/dao_faktury.py
import json
import sqlite3
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from app.db.dao_conn import get_conn


def create_or_update_invoice(
    firma_id: int,
    miesiac: str,
    pozycje: List[Dict[str, Any]],
    kwota_netto: float,
    kwota_brutto: float,
    existing_invoice_id: Optional[int] = None
) -> int:
    """
    Tworzy nową fakturę lub aktualizuje istniejącą (bez logiki scalania).
    Zwraca id faktury.
    """
    conn = get_conn()
    cur = conn.cursor()

    pozycje_json = json.dumps(pozycje, ensure_ascii=False)
    today = datetime.now().strftime("%Y-%m-%d")
    termin = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    if existing_invoice_id:
        cur.execute("""
            UPDATE faktury
            SET kwota_netto = ?, kwota_brutto = ?, pozycje_json = ?,
                data_wystawienia = ?, data_sprzedazy = ?, termin_platnosci = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (round(kwota_netto, 2), round(kwota_brutto, 2), pozycje_json, today, today, termin, existing_invoice_id))
        invoice_id = existing_invoice_id
    else:
        cur.execute("""
            INSERT INTO faktury (
                firma_id, miesiac,
                kwota_netto, kwota_brutto,
                data_wystawienia, data_sprzedazy, termin_platnosci,
                pozycje_json, eksportowana, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (firma_id, miesiac, round(kwota_netto, 2), round(kwota_brutto, 2),
              today, today, termin, pozycje_json))
        invoice_id = cur.lastrowid

    conn.commit()
    conn.close()
    return invoice_id


def get_invoice_for_company_month(firma_id: int, miesiac: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM faktury WHERE firma_id = ? AND miesiac = ?", (firma_id, miesiac))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_invoices_for_company(firma_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT f.*, c.nazwa AS firma_nazwa
        FROM faktury f
        JOIN firmy c ON f.firma_id = c.id
        WHERE f.firma_id = ? AND COALESCE(f.eksportowana, 0) = 0
        ORDER BY f.miesiac DESC, f.created_at DESC
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_exported_invoices() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT f.*, c.nazwa AS firma_nazwa
        FROM faktury f
        JOIN firmy c ON f.firma_id = c.id
        WHERE COALESCE(f.eksportowana, 0) = 1
        ORDER BY f.updated_at DESC, f.created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_exported(invoice_ids: List[int]) -> None:
    if not invoice_ids:
        return
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(invoice_ids))
    cur.execute(f"UPDATE faktury SET eksportowana = 1, data_eksportu = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", invoice_ids)
    conn.commit()
    conn.close()


# ---------------------------
# Archiwizacja i usuwanie
# ---------------------------
def _ensure_archiwum_table_exists():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS faktury_archiwum (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faktura_id INTEGER,
            firma_id INTEGER,
            miesiac TEXT,
            pozycje_json TEXT,
            kwota_netto REAL,
            kwota_brutto REAL,
            archived_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def archive_invoice_positions(conn: sqlite3.Connection, invoice_row: Dict[str, Any]) -> None:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO faktury_archiwum (
            faktura_id, firma_id, miesiac, pozycje_json, kwota_netto, kwota_brutto, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        invoice_row.get("id"),
        invoice_row.get("firma_id"),
        invoice_row.get("miesiac"),
        invoice_row.get("pozycje_json"),
        invoice_row.get("kwota_netto"),
        invoice_row.get("kwota_brutto"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))


def delete_invoice_keep_positions(invoice_id: int) -> bool:
    """
    Archiwizuje pozycje faktury i usuwa nagłówek faktury.
    Zwraca True jeśli usunięto, False jeśli brak faktury.
    """
    _ensure_archiwum_table_exists()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM faktury WHERE id = ?", (invoice_id,))
        row = cur.fetchone()
        if not row:
            return False
        invoice = dict(row)
        try:
            archive_invoice_positions(conn, invoice)
            cur.execute("DELETE FROM faktury WHERE id = ?", (invoice_id,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def delete_invoices_for_company_month_keep_positions(firma_id: int, miesiac: str) -> int:
    _ensure_archiwum_table_exists()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM faktury WHERE firma_id = ? AND miesiac = ?", (firma_id, miesiac))
        rows = cur.fetchall()
        if not rows:
            return 0
        deleted = 0
        try:
            for r in rows:
                fid = r["id"]
                cur.execute("SELECT * FROM faktury WHERE id = ?", (fid,))
                invoice_row = dict(cur.fetchone())
                archive_invoice_positions(conn, invoice_row)
                cur.execute("DELETE FROM faktury WHERE id = ?", (fid,))
                deleted += 1
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
