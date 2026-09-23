import json
import sqlite3
import logging
import os
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from app.db.dao_conn import get_conn

PROJECT_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ERROR_LOG_PATH = os.path.join(PROJECT_ROOT, "faktury_errors.log")
RUN_LOG_PATH = os.path.join(PROJECT_ROOT, "generate_invoices_run.log")

logger = logging.getLogger("dao_faktury")
if not logger.handlers:
    fh = logging.FileHandler(ERROR_LOG_PATH, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
logger.setLevel(logging.INFO)

run_logger = logging.getLogger("generate_invoices")
if not run_logger.handlers:
    rfh = logging.FileHandler(RUN_LOG_PATH, encoding="utf-8")
    rfh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    run_logger.addHandler(rfh)
run_logger.setLevel(logging.DEBUG)


def _safe_commit(conn: sqlite3.Connection):
    try:
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def create_or_update_invoice(
    firma_id: int,
    miesiac: str,
    pozycje: List[Dict[str, Any]],
    kwota_netto: float,
    kwota_brutto: float,
    existing_invoice_id: Optional[int] = None
) -> int:

    if not isinstance(miesiac, str) or len(miesiac) < 7:
        raise ValueError("Nieprawidłowy miesiąc. Oczekiwany format 'YYYY-MM'.")

    if not pozycje or not isinstance(pozycje, list):
        raise ValueError("Faktura musi zawierać co najmniej jedną pozycję.")

    conn = get_conn()
    cur = conn.cursor()

    pozycje_json = json.dumps(pozycje, ensure_ascii=False)
    today = datetime.now().strftime("%Y-%m-%d")
    termin = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        if existing_invoice_id:
            cur.execute("""
                UPDATE faktury
                SET kwota_netto = ?, kwota_brutto = ?, pozycje_json = ?,
                    data_wystawienia = ?, data_sprzedazy = ?, termin_platnosci = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (round(kwota_netto, 2), round(kwota_brutto, 2), pozycje_json,
                  today, today, termin, existing_invoice_id))

            if cur.rowcount == 0:
                raise ValueError(f"Nie znaleziono faktury o id {existing_invoice_id}.")

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

        _safe_commit(conn)
        run_logger.debug(f"Invoice saved: firma_id={firma_id}, miesiac={miesiac}, invoice_id={invoice_id}")
        return invoice_id

    except sqlite3.IntegrityError as e:
        conn.rollback()
        msg = f"SQLite IntegrityError podczas zapisu faktury dla firmy {firma_id}, miesiac={miesiac}: {e}"
        logger.exception(msg)
        run_logger.exception(msg)
        raise ValueError(msg)

    except ValueError:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        msg = f"Nieoczekiwany błąd podczas zapisu faktury dla firmy {firma_id}, miesiac={miesiac}: {e}"
        logger.exception(msg)
        run_logger.exception(msg)
        raise

    finally:
        conn.close()
def get_invoice_for_company_month(firma_id: int, miesiac: str):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM faktury WHERE firma_id = ? AND miesiac = ?", (firma_id, miesiac))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_invoices_for_company(firma_id: int):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM faktury
        WHERE firma_id = ?
        ORDER BY miesiac DESC, created_at DESC
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_exported_invoices():
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


def mark_exported(invoice_ids: List[int]):
    if not invoice_ids:
        return
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(invoice_ids))
    cur.execute(f"UPDATE faktury SET eksportowana = 1, data_eksportu = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", invoice_ids)
    conn.commit()
    conn.close()


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


def archive_invoice_positions(conn: sqlite3.Connection, invoice_row: Dict[str, Any]):
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
def delete_invoice_keep_positions(invoice_id: int):
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
        archive_invoice_positions(conn, invoice)

        cur.execute("DELETE FROM faktury WHERE id = ?", (invoice_id,))
        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def generate_monthly_invoices_for_month(miesiac: str):
    report = {"generated": [], "skipped": [], "errors": []}
    run_logger.info(f"Start generating invoices for month: {miesiac}")

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("SELECT id, nazwa, glowny_cennik, limit_dokumentow, zdrowotna, fakturowac, forma_dzialalnosci, vat_czynny FROM firmy")
        firms = [dict(r) for r in cur.fetchall()]

        for firma in firms:
            fid = firma["id"]
            fname = firma.get("nazwa", "<brak>")

            fakturowac_flag = bool(firma.get("fakturowac", 0))

            cur2 = conn.cursor()
            cur2.execute("SELECT id FROM faktury WHERE firma_id = ? AND miesiac = ?", (fid, miesiac))
            if cur2.fetchone():
                report["skipped"].append((fid, "Faktura już istnieje"))
                continue

            if fakturowac_flag:
                klucz = firma.get("glowny_cennik")

                ccur = conn.cursor()
                ccur.execute("SELECT cena_netto_ksiegowosc, zdrowotna, netto FROM cenniki WHERE klucz = ?", (klucz,))
                crow = ccur.fetchone()

                if not crow:
                    report["errors"].append((fid, f"Brak cennika dla klucza '{klucz}'"))
                    continue

                if isinstance(crow, sqlite3.Row):
                    cena_netto_ks = float(crow["cena_netto_ksiegowosc"] or 0.0)
                    zdrowotna_val = float(crow["zdrowotna"] or 0.0)
                    netto_total = float(crow["netto"] or 0.0)
                else:
                    cena_netto_ks = float(crow[0] or 0.0)
                    zdrowotna_val = float(crow[1] or 0.0)
                    netto_total = float(crow[2] or 0.0)

                pozycje = []
                wliczaj_zdrowotna = bool(firma.get("zdrowotna", 0))

                if wliczaj_zdrowotna:
                    pozycje.append({
                        "opis": "Księgowość (miesięczna) - podstawowa opłata",
                        "ilosc": 1,
                        "cena_netto": round(netto_total, 2),
                        "stawka_vat": "0",
                        "wartosc_netto": round(netto_total, 2),
                        "wartosc_brutto": round(netto_total * 1.23, 2)
                    })
                    total_netto = round(netto_total, 2)
                    total_brutto = round(netto_total * 1.23, 2)

                else:
                    pozycje.append({
                        "opis": "Księgowość (miesięczna) - podstawa",
                        "ilosc": 1,
                        "cena_netto": round(cena_netto_ks, 2),
                        "stawka_vat": "0",
                        "wartosc_netto": round(cena_netto_ks, 2),
                        "wartosc_brutto": round(cena_netto_ks * 1.23, 2)
                    })

                    if zdrowotna_val > 0:
                        pozycje.append({
                            "opis": "Składka zdrowotna (miesięczna)",
                            "ilosc": 1,
                            "cena_netto": round(zdrowotna_val, 2),
                            "stawka_vat": "0",
                            "wartosc_netto": round(zdrowotna_val, 2),
                            "wartosc_brutto": round(zdrowotna_val * 1.23, 2)
                        })

                    total_netto = round(sum(p["wartosc_netto"] for p in pozycje), 2)
                    total_brutto = round(sum(p["wartosc_brutto"] for p in pozycje), 2)

                invoice_id = create_or_update_invoice(fid, miesiac, pozycje, total_netto, total_brutto)
                report["generated"].append((fid, invoice_id))

            else:
                report["skipped"].append((fid, "fakturowac=0"))

        return report

    finally:
        conn.close()
