# app/db/dao_cenniki_kadry.py
import psycopg
from typing import List, Dict, Optional, Any
from app.db.dao_conn import get_conn


def get_all_kadry_pricing() -> List[Dict[str, Any]]:
    """
    Zwraca listę cenników kadrowych z kolumnami netto/brutto.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT klucz,
               COALESCE(cena_netto_kadry, cena) AS cena_netto_kadry,
               COALESCE(cena_brutto_kadry, ROUND(COALESCE(cena_netto_kadry, cena) * 1.23, 2)) AS cena_brutto_kadry,
               opis
        FROM cenniki_kadry
        ORDER BY klucz
    """)

    rows = cur.fetchall()
    conn.close()

    return [dict(r) for r in rows]


def get_kadry_price(klucz: str) -> Optional[float]:
    """
    Pobiera cenę netto (cena_netto_kadry) dla danego klucza.
    Fallback do starego pola 'cena' jeśli istnieje.
    """
    if not klucz:
        return None

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(cena_netto_kadry, cena) AS cena_netto
        FROM cenniki_kadry
        WHERE klucz = %s
    """, (klucz,))

    row = cur.fetchone()
    conn.close()

    return float(row["cena_netto"]) if row and row["cena_netto"] is not None else None


def get_kadry_brutto(klucz: str) -> Optional[float]:
    """
    Pobiera cenę brutto (cena_brutto_kadry) dla danego klucza.
    Fallback: oblicza z netto jeśli brak brutto.
    """
    if not klucz:
        return None

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT cena_brutto_kadry, COALESCE(cena_netto_kadry, cena) AS cena_netto
        FROM cenniki_kadry
        WHERE klucz = %s
    """, (klucz,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    if row["cena_brutto_kadry"] is not None and row["cena_brutto_kadry"] != 0:
        return float(row["cena_brutto_kadry"])
    if row["cena_netto"] is not None:
        return round(float(row["cena_netto"]) * 1.23, 2)
    return None


def get_kadry_description(klucz: str) -> Optional[str]:
    if not klucz:
        return None

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT opis FROM cenniki_kadry WHERE klucz = %s", (klucz,))
    row = cur.fetchone()
    conn.close()
    return row["opis"] if row else None


def import_kadry_pricing(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Import cennika kadrowego z CSV/XLSX.
    Akceptuje kolumny: klucz, cena (netto) lub cena_netto_kadry, opcjonalnie cena_brutto_kadry, opis.
    """
    errors = []
    inserted = 0
    updated = 0

    conn = get_conn()
    cur = conn.cursor()

    for r in rows:
        klucz = str(r.get("klucz", "") or "").strip()
        cena_raw = r.get("cena") or r.get("cena_netto_kadry") or r.get("cena_netto") or ""
        brutto_raw = r.get("cena_brutto_kadry") or r.get("brutto") or ""
        opis = str(r.get("opis", "") or "").strip()

        if not klucz or str(cena_raw).strip() == "":
            errors.append(f"Brak klucza lub ceny: {r}")
            continue

        try:
            cena_netto = float(str(cena_raw).replace(",", "."))
        except Exception:
            errors.append(f"Niepoprawna cena netto dla klucza {klucz}: {cena_raw}")
            continue

        cena_brutto = None
        if brutto_raw not in (None, ""):
            try:
                cena_brutto = float(str(brutto_raw).replace(",", "."))
            except Exception:
                cena_brutto = None

        if cena_brutto is None:
            cena_brutto = round(cena_netto * 1.23, 2)

        cur.execute("SELECT id FROM cenniki_kadry WHERE klucz = %s", (klucz,))
        exists = cur.fetchone()

        if exists:
            cur.execute("""
                UPDATE cenniki_kadry
                SET cena_netto_kadry = %s, cena_brutto_kadry = %s, opis = %s, updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
                WHERE klucz = %s
            """, (cena_netto, cena_brutto, opis, klucz))
            updated += 1
        else:
            cur.execute("""
                INSERT INTO cenniki_kadry (klucz, cena_netto_kadry, cena_brutto_kadry, opis)
                VALUES (%s, %s, %s, %s)
            """, (klucz, cena_netto, cena_brutto, opis))
            inserted += 1

    conn.commit()
    conn.close()

    return {"inserted": inserted, "updated": updated, "errors": errors}
