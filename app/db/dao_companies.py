# app/db/dao_companies.py
from typing import Optional, Dict, Any, List
from app.db.dao_conn import get_conn
from app.companies.constants import COMPANY_FIELD_ORDER
from app.companies.companies_model import (
    serialize_company_payload,
    update_company_employee_assignments,
    get_company_assigned_employee_ids
)

# ============================================================
#  LISTA FIRM
# ============================================================

def list_companies() -> List:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nazwa, nip, miasto, status,
               glowny_cennik, limit_dokumentow,
               typ_rozliczenia, vat_czynny
        FROM firmy
        ORDER BY nazwa
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

# ============================================================
#  POBIERANIE FIRMY
# ============================================================

def get_company(firma_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM firmy WHERE id = ?", (firma_id,))
    row = cur.fetchone()
    conn.close()
    return row

# ============================================================
#  ZAPIS FIRMY (FULL)
# ============================================================

def save_company(payload: Dict[str, Any]) -> int:
    """
    Pełny zapis firmy zgodny z COMPANY_FIELD_ORDER.
    Obsługuje:
      - insert
      - update
      - przypisania pracowników
      - udziałowców (osobno)
    """

    conn = get_conn()
    cur = conn.cursor()

    data = serialize_company_payload(payload)
    employee_ids = payload.get("assigned_employee_ids", [])

    if "id" in payload and payload["id"]:
        # UPDATE
        firma_id = payload["id"]
        set_clause = ", ".join([f"{f} = ?" for f in COMPANY_FIELD_ORDER])
        cur.execute(
            f"UPDATE firmy SET {set_clause} WHERE id = ?",
            [data[f] for f in COMPANY_FIELD_ORDER] + [firma_id]
        )
    else:
        # INSERT
        cols = ", ".join(COMPANY_FIELD_ORDER)
        placeholders = ", ".join(["?"] * len(COMPANY_FIELD_ORDER))
        cur.execute(
            f"INSERT INTO firmy ({cols}) VALUES ({placeholders})",
            [data[f] for f in COMPANY_FIELD_ORDER]
        )
        firma_id = cur.lastrowid

    # Przypisania pracowników
    update_company_employee_assignments(cur, firma_id, employee_ids)

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
#  NOTATKI
# ============================================================

def get_company_notes(firma_id: int) -> List:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, tresc, data_utworzenia
        FROM company_notes
        WHERE firma_id = ?
        ORDER BY data_utworzenia DESC
        LIMIT 50
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def add_company_note(firma_id: int, tresc: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO company_notes (firma_id, tresc)
        VALUES (?, ?)
    """, (firma_id, tresc))
    conn.commit()
    conn.close()

# ============================================================
#  KONTA EMAIL (ADMIN / OGÓLNE)
# ============================================================

def get_email_accounts() -> List[Dict[str, Any]]:
    """
    Zwraca wszystkie konta email (użyteczne dla panelu admina).
    Zwracamy porty jako INTEGER oraz pola pracownik_id i aktywne.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            id,
            pracownik_id,
            email,
            serwer_imap,
            CAST(port_imap AS INTEGER) AS port_imap,
            ssl_imap,
            serwer_smtp,
            CAST(port_smtp AS INTEGER) AS port_smtp,
            ssl_smtp,
            haslo_enc,
            aktywne
        FROM konta_email
        ORDER BY id
    """)
    rows = cur.fetchall()
    conn.close()
    # debug: pokaż klucze pierwszego rekordu (jeśli istnieje)
    if rows:
        try:
            print("DEBUG get_email_accounts: keys =", list(dict(rows[0]).keys()))
            print("DEBUG get_email_accounts: sample =", dict(rows[0]))
        except Exception:
            pass
    return [dict(r) for r in rows]

def get_email_accounts_for_employee(pracownik_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            id,
            pracownik_id,
            email,
            serwer_imap,
            CAST(port_imap AS INTEGER) AS port_imap,
            ssl_imap,
            serwer_smtp,
            CAST(port_smtp AS INTEGER) AS port_smtp,
            ssl_smtp,
            haslo_enc,
            aktywne
        FROM konta_email
        WHERE pracownik_id = ?
        ORDER BY id
    """, (pracownik_id,))
    rows = cur.fetchall()
    conn.close()
    if rows:
        try:
            print(f"DEBUG get_email_accounts_for_employee({pracownik_id}): keys =", list(dict(rows[0]).keys()))
            print(f"DEBUG get_email_accounts_for_employee({pracownik_id}): sample =", dict(rows[0]))
        except Exception:
            pass
    return [dict(r) for r in rows]

# ============================================================
#  STATUSY MIESIĘCZNE
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
