# app/db/dao_companies.py
from typing import Optional, Dict, Any, List
from app.db.dao_conn import get_conn

# ============================================================
#  FIRMY
# ============================================================

def list_companies() -> List:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nazwa, nip, miasto,
               glowny_cennik, limit_dokumentow,
               typ_rozliczenia, vat_czynny
        FROM firmy
        ORDER BY nazwa
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_company(firma_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM firmy WHERE id = ?", (firma_id,))
    row = cur.fetchone()
    conn.close()
    return row


def save_company(payload: Dict[str, Any]) -> int:
    """
    payload może zawierać:
      id (opcjonalnie przy update),
      nazwa, nip, miasto, uop, uz, uod,
      glowny_cennik, limit_dokumentow,
      typ_rozliczenia, vat_czynny, glowny_cennik_manualny
    """
    conn = get_conn()
    cur = conn.cursor()

    fields = [k for k in payload.keys() if k != "id"]
    values = [payload[f] for f in fields]

    if "id" in payload and payload["id"]:
        set_clause = ", ".join([f"{f} = ?" for f in fields])
        cur.execute(f"""
            UPDATE firmy
            SET {set_clause}
            WHERE id = ?
        """, values + [payload["id"]])
        firma_id = payload["id"]
    else:
        cols = ", ".join(fields)
        placeholders = ", ".join(["?"] * len(fields))
        cur.execute(f"""
            INSERT INTO firmy ({cols})
            VALUES ({placeholders})
        """, values)
        firma_id = cur.lastrowid

    conn.commit()
    conn.close()
    return firma_id


# ============================================================
#  UDZIAŁOWCY
# ============================================================

def get_company_shareholders(firma_id: int) -> List:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, imie_nazwisko, procent
        FROM udzialowcy
        WHERE firma_id = ?
        ORDER BY id
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def save_company_shareholders(firma_id: int, shareholders: List[Dict[str, Any]]) -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM udzialowcy WHERE firma_id = ?", (firma_id,))

    for sh in shareholders:
        cur.execute("""
            INSERT INTO udzialowcy (firma_id, imie_nazwisko, procent)
            VALUES (?, ?, ?)
        """, (firma_id, sh["name"], sh["share"]))

    conn.commit()
    conn.close()


# ============================================================
#  STATUSY MIESIĘCZNE — zgodne z nowym schema.py
# ============================================================

def get_month_status(firma_id: int, miesiac: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM statusy_miesieczne
        WHERE firma_id = ? AND miesiac = ?
    """, (firma_id, miesiac))
    row = cur.fetchone()
    conn.close()
    return row


def save_month_status(firma_id: int, miesiac: str, payload: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO statusy_miesieczne (
            firma_id, miesiac,
            etap, ilosc_dok,
            sekundy_ksiegowosc, sekundy_kadry,
            wartosc_uslug
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(firma_id, miesiac)
        DO UPDATE SET
            etap = excluded.etap,
            ilosc_dok = excluded.ilosc_dok,
            sekundy_ksiegowosc = excluded.sekundy_ksiegowosc,
            sekundy_kadry = excluded.sekundy_kadry,
            wartosc_uslug = excluded.wartosc_uslug
    """, (
        firma_id,
        miesiac,
        payload.get("etap", "Brak"),
        payload.get("ilosc_dok", 0),
        payload.get("sekundy_ksiegowosc", 0),
        payload.get("sekundy_kadry", 0),
        payload.get("wartosc_uslug", 0.0),
    ))

    conn.commit()
    conn.close()


# ============================================================
#  DOKUMENTY
# ============================================================

def count_documents_for_month(firma_id: int, miesiac: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) AS cnt
        FROM dokumenty
        WHERE firma_id = ?
          AND substr(data_wyst, 1, 7) = ?
    """, (firma_id, miesiac))
    row = cur.fetchone()
    conn.close()
    return int(row["cnt"] or 0)


# ============================================================
#  CENNIKI
# ============================================================

def _extract_limit_from_key(klucz: str) -> Optional[int]:
    try:
        return int(klucz.split("_")[-1])
    except Exception:
        return None


def wybierz_cennik(typ: str, vat_czynny: bool, limit_dokumentow: int):
    """
    Zwraca rekord z tabeli cenniki dla:
      typ: 'Ryczałt' / 'KPIR' / 'KH'
      vat_czynny: bool
      limit_dokumentow: int

    klucz: np. Ryczałt_N_20, KPIR_T_50
    """
    conn = get_conn()
    cur = conn.cursor()

    vat_suffix = "T" if vat_czynny else "N"
    pattern = f"{typ}_{vat_suffix}_%"

    cur.execute("""
        SELECT *
        FROM cenniki
        WHERE klucz LIKE ?
        ORDER BY klucz ASC
    """, (pattern,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None

    best_row = None
    best_limit = None

    for row in rows:
        lim = _extract_limit_from_key(row["klucz"])
        if lim is None:
            continue
        if lim <= limit_dokumentow and (best_limit is None or lim > best_limit):
            best_limit = lim
            best_row = row

    if best_row is None:
        return rows[0]

    return best_row
