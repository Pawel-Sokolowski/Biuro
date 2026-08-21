# app/global/pricing.py
import sqlite3
from app.config import DB_NAME


def _extract_limit_from_key(klucz: str):
    try:
        return int(klucz.split("_")[-1])
    except Exception:
        return None


def wybierz_cennik(typ: str, vat_czynny: bool, limit_dokumentow: int):
    """
    Zwraca najlepszy cennik dla firmy:
    - typ: Ryczałt / KPIR / KH
    - vat_czynny: True/False
    - limit_dokumentow: np. 20, 50, 100
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    vat_suffix = "T" if vat_czynny else "N"
    pattern = f"{typ}_{vat_suffix}_%"

    cur.execute("SELECT * FROM cenniki WHERE klucz LIKE ? ORDER BY klucz", (pattern,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None

    best_row = None
    best_limit = None

    for r in rows:
        lim = _extract_limit_from_key(r[1])  # klucz
        if lim is None:
            continue

        if lim <= limit_dokumentow and (best_limit is None or lim > best_limit):
            best_limit = lim
            best_row = r

    if best_row is None:
        rows_sorted = sorted(rows, key=lambda r: _extract_limit_from_key(r[1]) or 999999)
        best_row = rows_sorted[0]

    return best_row


def pobierz_cene_za_dokument(typ: str, vat_czynny: bool):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    vat = 1 if vat_czynny else 0
    cur.execute("""
        SELECT cena FROM globalne_ceny_za_dokument
        WHERE typ = ? AND vat = ?
    """, (typ, vat))

    row = cur.fetchone()
    conn.close()
    return row[0] if row else None
