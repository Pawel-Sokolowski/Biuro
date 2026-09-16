# app/db/dao_companies.py
import sqlite3
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from app.db.dao_conn import get_conn

# Lista pól, które akceptujemy przy zapisie/odczycie firmy
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
    "limit_kadry", "limit_ryczalt", "limit_ksiegowosc",
    "zdrowotna",
    "typ_rozliczenia", "vat_czynny", "status"
]


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
    """
    Pobiera firmy z opcjonalnym filtrem tekstowym, filtrem statusu i filtrem przypisanego pracownika.
    Filtrowanie wykonywane po stronie SQL dla wydajności.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    base_select = "SELECT f.id, f.nazwa, f.nip, f.miasto, COALESCE(f.status, 'Aktywne') AS status, f.glowny_cennik, f.limit_dokumentow"
    base_from = " FROM firmy f"
    where_clauses: List[str] = []
    params: List[Any] = []

    # Jeśli filtr pracownika - dołączamy JOIN na pracownik_firma
    if employee_id:
        base_from += " JOIN pracownik_firma pf ON pf.firma_id = f.id"
        where_clauses.append("pf.pracownik_id = ?")
        params.append(employee_id)

    if search:
        where_clauses.append("(LOWER(f.nazwa) LIKE ? OR LOWER(f.nip) LIKE ?)")
        q = f"%{search.lower()}%"
        params.extend([q, q])

    if status and status != "Wszystkie":
        where_clauses.append("COALESCE(f.status, 'Aktywne') = ?")
        params.append(status)

    sql = base_select + base_from
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)

    sql += " ORDER BY f.nazwa"

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    return [dict(r) for r in rows]


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


def _find_company_by_nip(nip: str) -> Optional[int]:
    if not nip:
        return None
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id FROM firmy WHERE nip = ?", (nip.strip(),))
    row = cur.fetchone()
    conn.close()
    return int(row["id"]) if row else None


def save_company(payload: Dict[str, Any]) -> int:
    """
    Bezpieczne zapisywanie/aktualizacja firmy.
    - Przyjmuje payload z dowolnym podzbiorem pól.
    - Jeśli payload zawiera 'id' -> UPDATE tej firmy (tylko dostarczone pola).
    - Jeśli nie ma 'id' ale jest 'nip' i firma z takim NIP istnieje -> UPDATE tej firmy (zapobiega duplikatom).
    - W przeciwnym razie INSERT nowej firmy (tylko dostarczone pola).
    Zwraca id firmy.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Przygotuj mapę pól, ale tylko te, które są w FIRMA_FIELDS i nie są None
    data = {f: payload.get(f) for f in FIRMA_FIELDS if payload.get(f) is not None}

    try:
        # Jeśli podano id -> aktualizuj
        if payload.get("id"):
            firma_id = int(payload["id"])
            if data:
                set_clause = ", ".join([f"{col} = ?" for col in data.keys()])
                params = [data[col] for col in data.keys()] + [firma_id]
                cur.execute(f"UPDATE firmy SET {set_clause} WHERE id = ?", params)
            else:
                # nic do zaktualizowania
                pass

        else:
            # jeśli nie ma id, sprawdź NIP - jeśli istnieje, traktuj jako update (zapobiegamy duplikatom)
            nip = payload.get("nip")
            existing_id = _find_company_by_nip(nip) if nip else None

            if existing_id:
                firma_id = existing_id
                if data:
                    set_clause = ", ".join([f"{col} = ?" for col in data.keys()])
                    params = [data[col] for col in data.keys()] + [firma_id]
                    cur.execute(f"UPDATE firmy SET {set_clause} WHERE id = ?", params)
            else:
                # INSERT tylko dostarczonych pól
                if not data:
                    raise ValueError("Brak pól do wstawienia dla nowej firmy.")
                cols = ", ".join(data.keys())
                placeholders = ", ".join(["?"] * len(data))
                params = [data[col] for col in data.keys()]
                cur.execute(f"INSERT INTO firmy ({cols}) VALUES ({placeholders})", params)
                firma_id = cur.lastrowid

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
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
        # obsługa różnych separatorów: KPIR_N_10, KPIR-10, KPIR_10
        if not klucz:
            return None
        parts = klucz.replace("-", "_").split("_")
        for part in reversed(parts):
            try:
                return int(part)
            except Exception:
                continue
        return None
    except Exception:
        return None


def opis_cennika_z_klucza(klucz: str) -> str:
    try:
        parts = klucz.split("_")
        rodzaj = parts[0]
        vat = parts[1] if len(parts) > 1 else ""
        limit = parts[2] if len(parts) > 2 else ""

        vat_txt = "bez VAT" if vat == "N" else "VAT czynny"
        limit_txt = f"limit {limit} dokumentów" if limit else ""
        return " • ".join([p for p in [rodzaj, vat_txt, limit_txt] if p])
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


def get_ksiegowosc_options() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT klucz,
               cena_netto_ksiegowosc AS netto,
               cena_brutto_ksiegowosc AS brutto,
               cena_za_dokument_ponad_limit AS ponad_limit
        FROM cenniki
        ORDER BY klucz
    """)

    rows = cur.fetchall()
    conn.close()

    result: List[Dict[str, Any]] = []
    for r in rows:
        result.append({
            "klucz": r["klucz"],
            "netto": float(r["netto"] or 0),
            "brutto": float(r["brutto"] or 0),
            "ponad_limit": float(r["ponad_limit"] or 0),
        })
    return result


def get_ksiegowosc_by_key(klucz: str) -> Optional[Dict[str, Any]]:
    for item in get_ksiegowosc_options():
        if item["klucz"] == klucz:
            return item
    return None


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


# ----------------------------
# KPIR helper functions (zintegrowane w tym pliku)
# ----------------------------
def get_kpir_cenniki() -> List[Dict[str, Any]]:
    """
    Zwraca listę cenników rozpoznawanych jako KPIR (klucz zaczyna się od 'KPIR' lub typ='KPIR').
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT klucz, cena_netto_ksiegowosc, cena_za_dokument_ponad_limit, netto, brutto
        FROM cenniki
        WHERE typ = 'KPIR' OR klucz LIKE 'KPIR_%' OR klucz LIKE 'KPIR-%' OR klucz LIKE 'KPIR%'
        ORDER BY klucz
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _extract_limit_from_klucz(klucz: str) -> Optional[int]:
    """
    Próbuje wyciągnąć liczbę limitu z końca klucza.
    Obsługuje formaty: KPIR_N_10, KPIR_10, KPIR-10, KPIR_N-10 itp.
    """
    if not klucz:
        return None
    parts = klucz.replace("-", "_").split("_")
    for part in reversed(parts):
        try:
            return int(part)
        except Exception:
            continue
    return None


def choose_kpir_for_limit(limit: int) -> Optional[Tuple[str, float]]:
    """
    Wybiera najlepszy cennik KPIR dla zadanego limitu dokumentów:
      - spośród cenników rozpoznanych jako KPIR wybiera ten z największym limitem <= limit
      - jeśli żaden nie pasuje, zwraca None
    Zwraca (klucz, cena_netto_ksiegowosc)
    """
    cenniki = get_kpir_cenniki()
    best = None
    best_limit = None
    for c in cenniki:
        klucz = c.get("klucz")
        lim = _extract_limit_from_klucz(klucz)
        if lim is None:
            continue
        if lim <= limit:
            if best_limit is None or lim > best_limit:
                best_limit = lim
                cena = c.get("cena_netto_ksiegowosc") if c.get("cena_netto_ksiegowosc") is not None else c.get("netto", 0.0)
                best = (klucz, float(cena))
    return best


def update_company_cennik_auto(firma_id: int, limit_dokumentow: int) -> Optional[Dict[str, Any]]:
    """
    Oblicza automatyczny wybór cennika KPIR i zapisuje go w tabeli firmy:
      - cennik_kpir_auto
      - cena_ksiegowosc_auto
      - limit_dokumentow
    Nie nadpisuje wartości jeśli firma ma manual_cennik_override = 1.
    Zwraca zapisany rekord {'klucz':..., 'cena':...} lub None jeśli brak dopasowania.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT manual_cennik_override FROM firmy WHERE id = ?", (firma_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    if row["manual_cennik_override"]:
        conn.close()
        return None

    choice = choose_kpir_for_limit(limit_dokumentow)
    if not choice:
        cur.execute("""
            UPDATE firmy
            SET cennik_kpir_auto = NULL, cena_ksiegowosc_auto = 0, limit_dokumentow = ?
            WHERE id = ?
        """, (limit_dokumentow, firma_id))
        conn.commit()
        conn.close()
        return None

    klucz, cena = choice
    cur.execute("""
        UPDATE firmy
        SET cennik_kpir_auto = ?, cena_ksiegowosc_auto = ?, limit_dokumentow = ?
        WHERE id = ?
    """, (klucz, cena, limit_dokumentow, firma_id))
    conn.commit()
    conn.close()
    return {"klucz": klucz, "cena": cena}


def set_manual_cennik_for_company(firma_id: int, manual_klucz: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Ustawia ręczny cennik (manual override) dla firmy.
    - jeśli manual_klucz jest None lub '', wyłącza override
    - jeśli podano klucz, zapisuje go w cennik_kpir_manual i ustawia manual_cennik_override = 1
    Zwraca zapisany wybór {'klucz':..., 'cena':...} lub None jeśli klucz nie istnieje lub nie jest KPIR.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if not manual_klucz:
        cur.execute("""
            UPDATE firmy
            SET cennik_kpir_manual = NULL, manual_cennik_override = 0
            WHERE id = ?
        """, (firma_id,))
        conn.commit()
        conn.close()
        return None

    cur.execute("""
        SELECT klucz, cena_netto_ksiegowosc, netto
        FROM cenniki
        WHERE klucz = ? AND (typ = 'KPIR' OR klucz LIKE 'KPIR_%' OR klucz LIKE 'KPIR-%' OR klucz LIKE 'KPIR%')
        LIMIT 1
    """, (manual_klucz,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    cena = row["cena_netto_ksiegowosc"] if row["cena_netto_ksiegowosc"] is not None else row.get("netto", 0.0)
    cur.execute("""
        UPDATE firmy
        SET cennik_kpir_manual = ?, manual_cennik_override = 1, cena_ksiegowosc_auto = ?
        WHERE id = ?
    """, (manual_klucz, cena, firma_id))
    conn.commit()
    conn.close()
    return {"klucz": manual_klucz, "cena": float(cena)}


def get_company_pricing_info(firma_id: int) -> Optional[Dict[str, Any]]:
    """
    Zwraca aktualne pola związane z cennikiem KPIR dla firmy:
      cennik_kpir_auto, cennik_kpir_manual, manual_cennik_override, cena_ksiegowosc_auto, limit_dokumentow
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT cennik_kpir_auto, cennik_kpir_manual, manual_cennik_override, cena_ksiegowosc_auto, limit_dokumentow
        FROM firmy
        WHERE id = ?
    """, (firma_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None
