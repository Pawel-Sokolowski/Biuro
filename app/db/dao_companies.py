# app/db/dao_companies.py
import sqlite3
from typing import List, Dict, Any, Optional
from app.db.dao_conn import get_conn


def list_companies() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
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

    return [dict(r) for r in rows]


def get_companies_filtered(search: str, status: str, employee_id: Optional[int]) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT id, nazwa, nip, miasto, status
        FROM firmy
        ORDER BY nazwa
    """)
    rows = cur.fetchall()
    conn.close()

    result: List[Dict[str, Any]] = []

    for r in rows:
        r = dict(r)

        if search and search.lower() not in (r["nazwa"] or "").lower():
            continue

        if status != "Wszystkie" and (r["status"] or "Aktywne") != status:
            continue

        if employee_id:
            conn2 = get_conn()
            cur2 = conn2.cursor()
            cur2.execute("""
                SELECT 1 FROM pracownik_firma
                WHERE firma_id = ? AND pracownik_id = ?
            """, (r["id"], employee_id))
            assigned = cur2.fetchone()
            conn2.close()

            if not assigned:
                continue

        result.append(r)

    return result


def get_employees_list() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT id, imie, nazwisko
        FROM pracownicy
        WHERE aktywny = 1
        ORDER BY imie, nazwisko
    """)

    rows = cur.fetchall()
    conn.close()

    return [dict(r) for r in rows]


def get_company(firma_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM firmy WHERE id = ?", (firma_id,))
    row = cur.fetchone()

    conn.close()
    return dict(row) if row else None


FIRMA_FIELDS = [
    "nazwa", "nazwa_skrocona", "nip", "regon", "krs", "pesel",
    "miasto", "kod_pocztowy", "ulica", "adres_budynek",
    "forma_dzialalnosci", "forma_opodatkowania", "analityka_fk",
    "osoba_kontaktowa", "adres_email", "nr_telefonu",
    "adres_zam_miasto", "adres_zam_kod_pocztowy", "adres_zam_ulica",
    "adres_koresp_miasto", "adres_koresp_kod_pocztowy", "adres_koresp_ulica",
    "data_rozpoczecia_dg", "data_rozpoczecia_sprzedazy",
    "data_koniec_zus_start", "data_koniec_zus_pref", "data_koniec_maly_zus",
    "data_koniec_a1", "data_rozpoczecia_obslugi", "data_zakonczenia_obslugi",
    "zawieszenia_dzialalnosci", "kody_pkd",
    "cennik_ksiegowy", "cennik_kadrowy", "cennik_czasowy",
    "glowny_cennik", "glowny_cennik_manualny",
    "limit_dokumentow", "liczba_pracownikow",
    "uop", "uz", "uod",
    "typ_rozliczenia", "vat_czynny", "status"
]


def save_company(payload: Dict[str, Any]) -> int:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    data = {f: payload.get(f) for f in FIRMA_FIELDS}

    if payload.get("id"):
        firma_id = payload["id"]
        set_clause = ", ".join([f"{f} = ?" for f in FIRMA_FIELDS])
        cur.execute(
            f"UPDATE firmy SET {set_clause} WHERE id = ?",
            [data[f] for f in FIRMA_FIELDS] + [firma_id]
        )
    else:
        cols = ", ".join(FIRMA_FIELDS)
        placeholders = ", ".join(["?"] * len(FIRMA_FIELDS))
        cur.execute(
            f"INSERT INTO firmy ({cols}) VALUES ({placeholders})",
            [data[f] for f in FIRMA_FIELDS]
        )
        firma_id = cur.lastrowid

    conn.commit()
    conn.close()
    return firma_id


def get_global_pricing() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT klucz,
               cena_netto_ksiegowosc AS netto,
               brutto AS brutto,
               cena_za_dokument_ponad_limit AS ponad_limit
        FROM cenniki
        ORDER BY klucz
    """)

    rows = cur.fetchall()
    conn.close()

    return [dict(r) for r in rows]


def _extract_limit_from_key(klucz: str) -> Optional[int]:
    try:
        return int(klucz.split("_")[-1])
    except Exception:
        return None


def opis_cennika_z_klucza(klucz: str) -> str:
    try:
        parts = klucz.split("_")
        rodzaj = parts[0]
        vat = parts[1]
        limit = parts[2]

        vat_txt = "bez VAT" if vat == "N" else "VAT czynny"
        return f"{rodzaj} • {vat_txt} • limit {limit} dokumentów"
    except Exception:
        return klucz


def wybierz_cennik(typ: str, vat_czynny: bool, limit_dokumentow: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
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
        return dict(rows[0])

    return dict(best_row)


def get_pricing_kadry() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT klucz, cena_netto_kadry
        FROM cenniki_kadry
        ORDER BY klucz
    """)

    rows = cur.fetchall()
    conn.close()

    return [dict(r) for r in rows]
