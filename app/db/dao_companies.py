# app/db/dao_companies.py
import psycopg
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from app.db.dao_conn import get_conn, Row
from app.security.authorization import assigned_company_ids, can, is_administrator

# ============================================================
#  FIELDS — zgodne z aktualnym schematem (rozszerzone)
# ============================================================

FIRMA_FIELDS = [
    "nazwa", "nazwa_skrocona", "numer_klienta",
    "nip", "regon", "krs", "pesel",

    "miasto", "kod_pocztowy", "ulica", "adres_budynek", "adres_lokal",
    "kraj", "wojewodztwo", "powiat", "gmina",

    "forma_dzialalnosci", "forma_opodatkowania", "analityka_fk",
    "osoba_kontaktowa", "adres_email", "nr_telefonu",

    "adres_zam_miasto", "adres_zam_kod_pocztowy", "adres_zam_ulica",
    "adres_zam_budynek", "adres_zam_lokal",
    "adres_zam_kraj", "adres_zam_wojewodztwo",
    "adres_zam_powiat", "adres_zam_gmina",

    "adres_koresp_miasto", "adres_koresp_kod_pocztowy", "adres_koresp_ulica",
    "adres_koresp_budynek", "adres_koresp_lokal",
    "adres_koresp_kraj", "adres_koresp_wojewodztwo",
    "adres_koresp_powiat", "adres_koresp_gmina",

    "data_rozpoczecia_dg", "data_rozpoczecia_sprzedazy",
    "data_koniec_zus_start", "data_koniec_zus_pref",
    "data_koniec_maly_zus", "data_koniec_a1",
    "data_rozpoczecia_obslugi", "data_zakonczenia_obslugi",

    "zawieszenia_dzialalnosci", "kody_pkd",

    "cennik_ksiegowy", "cennik_kadrowy", "cennik_czasowy",
    "glowny_cennik", "glowny_cennik_manualny",

    "limit_dokumentow",
    "liczba_pracownikow",

    "uop_ilosc", "uz_ilosc", "uod_ilosc",
    "uop_cennik", "uz_cennik", "uod_cennik",

    "typ_rozliczenia", "vat_czynny", "status", "uslugi_zawieszone",

    "cennik_kpir_auto", "cena_ksiegowosc_auto",
    "cennik_kpir_manual", "manual_cennik_override",

    "zdrowotna", "cena_netto_ksiegowosc", "cena_ponad_limit",

    "wysylac_do_ksef",
    "wysylac_do_kontrahenta",
    "metoda_kasowa",
    "wliczac_zdrowotna",
    "numer_umowy", "status_vat",
    "fakturowac",
    "fakturowac_ksiegowosc", "fakturowac_kadry",
    "stala_kwota_ksiegowosc", "stala_kwota_kadry",
    "nazwa_saldeo",

    "adres_doreczen_elektronicznych",
    "urzad_skarbowy",

    "miesiac_dok_fakturowanie",
    "miesiac_prac_fakturowanie",
    "dzien_dostarczania_dokumentow",

    "seria_fv",
    "seria_kfv",

    "sposob_zaplaty",

    "status_podatkowy",

    "konto_bankowe",
    "uwagi",
    "kategoria_domyslna",

    "ksiegowa_id",
    "kadrowa_id",
]

ASSIGNEE_FIELDS = {"ksiegowa_id": "firma:ksiegowa", "kadrowa_id": "firma:kadrowa"}

# NULL w tych polach = wartość z ustawień globalnych
GLOBAL_DEFAULT_FLAGS = ("metoda_kasowa",)


# ============================================================
#  LIST / FILTER
# ============================================================

def _attach_pelnomocnictwa(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    initials = get_pelnomocnictwa_initials([r["id"] for r in rows])
    for r in rows:
        data = initials.get(r["id"], {})
        r["pel"] = data.get("PEL", "")
        r["upl"] = data.get("UPL", "")
    return rows


def list_companies(actor_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    sql = """
        SELECT f.id, f.nazwa, f.nip, f.miasto, f.status, f.uslugi_zawieszone,
               f.glowny_cennik, f.limit_dokumentow,
               f.typ_rozliczenia, f.vat_czynny, f.forma_opodatkowania,
               TRIM(COALESCE(pk.imie, '') || ' ' || COALESCE(pk.nazwisko, '')) AS ksiegowa,
               TRIM(COALESCE(pd.imie, '') || ' ' || COALESCE(pd.nazwisko, '')) AS kadrowa
        FROM firmy f
        LEFT JOIN pracownicy pk ON pk.id = f.ksiegowa_id
        LEFT JOIN pracownicy pd ON pd.id = f.kadrowa_id
    """
    params: List[Any] = []
    if actor_id and not is_administrator(actor_id):
        ids = assigned_company_ids(actor_id)
        if not ids:
            conn.close()
            return []
        placeholders = ",".join("%s" for _ in ids)
        sql += f" WHERE f.id IN ({placeholders})"
        params.extend(sorted(ids))
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    from app.utils.polish_sort import polish_sort_key

    return _attach_pelnomocnictwa(sorted(
        (dict(r) for r in rows), key=lambda row: polish_sort_key(row.get("nazwa"))
    ))


def get_companies_filtered(
    search: str,
    status: str,
    employee_id: Optional[int],
    actor_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()

    base_select = """
        SELECT f.id, f.nazwa, f.nip, f.miasto,
               COALESCE(f.status, 'Aktywne') AS status, f.uslugi_zawieszone,
               f.glowny_cennik, f.limit_dokumentow, f.typ_rozliczenia, f.vat_czynny,
               f.forma_opodatkowania,
               TRIM(COALESCE(pk.imie, '') || ' ' || COALESCE(pk.nazwisko, '')) AS ksiegowa,
               TRIM(COALESCE(pd.imie, '') || ' ' || COALESCE(pd.nazwisko, '')) AS kadrowa
    """
    base_from = (" FROM firmy f"
                 " LEFT JOIN pracownicy pk ON pk.id = f.ksiegowa_id"
                 " LEFT JOIN pracownicy pd ON pd.id = f.kadrowa_id")
    where = []
    params = []

    if employee_id:
        base_from += " JOIN pracownik_firma pf ON pf.firma_id = f.id"
        where.append("pf.pracownik_id = %s")
        params.append(employee_id)

    if search:
        where.append("(LOWER(f.nazwa) LIKE %s OR LOWER(f.nip) LIKE %s)")
        q = f"%{search.lower()}%"
        params.extend([q, q])

    if status and status != "Wszystkie":
        where.append("COALESCE(f.status, 'Aktywne') = %s")
        params.append(status)

    sql = base_select + base_from
    if where:
        sql += " WHERE " + " AND ".join(where)
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    from app.utils.polish_sort import polish_sort_key

    return _attach_pelnomocnictwa(sorted(
        (dict(r) for r in rows), key=lambda row: polish_sort_key(row.get("nazwa"))
    ))


# ============================================================
#  PEŁNOMOCNICTWA (PEL) / UPOWAŻNIENIA (UPL)
# ============================================================

PELNOMOCNICTWO_TYPES = ("PEL", "UPL")


def employee_initials(imie: Optional[str], nazwisko: Optional[str]) -> str:
    return f"{(imie or '')[:1]}{(nazwisko or '')[:1]}".upper()


def get_pelnomocnictwa(firma_id: int) -> Dict[str, List[int]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT typ, pracownik_id FROM pelnomocnictwa WHERE firma_id = %s",
        (firma_id,),
    )
    result: Dict[str, List[int]] = {t: [] for t in PELNOMOCNICTWO_TYPES}
    for row in cur.fetchall():
        result[row[0]].append(row[1])
    conn.close()
    return result


def set_pelnomocnictwa(firma_id: int, typ: str, employee_ids: List[int]) -> None:
    if typ not in PELNOMOCNICTWO_TYPES:
        raise ValueError(f"Nieznany typ: {typ}")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM pelnomocnictwa WHERE firma_id = %s AND typ = %s", (firma_id, typ))
    cur.executemany(
        "INSERT INTO pelnomocnictwa (firma_id, pracownik_id, typ) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        [(firma_id, int(e), typ) for e in employee_ids],
    )
    conn.commit()
    conn.close()


def get_pelnomocnictwa_initials(firma_ids: List[int]) -> Dict[int, Dict[str, str]]:
    if not firma_ids:
        return {}
    conn = get_conn()
    cur = conn.cursor()
    result: Dict[int, Dict[str, List[str]]] = {}
    ids = list(firma_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        placeholders = ",".join("%s" for _ in chunk)
        cur.execute(
            f"""
            SELECT p.firma_id, p.typ, e.imie, e.nazwisko
            FROM pelnomocnictwa p
            JOIN pracownicy e ON e.id = p.pracownik_id
            WHERE p.firma_id IN ({placeholders})
            ORDER BY e.nazwisko, e.imie
            """,
            chunk,
        )
        for firma_id, typ, imie, nazwisko in cur.fetchall():
            result.setdefault(firma_id, {}).setdefault(typ, []).append(
                employee_initials(imie, nazwisko)
            )
    conn.close()
    return {fid: {t: " ".join(v) for t, v in d.items()} for fid, d in result.items()}
# ============================================================

def get_employees_list() -> List[Dict[str, Any]]:
    conn = get_conn()
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


# ============================================================
#  GET COMPANY
# ============================================================

def get_company(firma_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM firmy WHERE id = %s", (firma_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_company_field_options(field: str) -> List[str]:
    allowed = {"forma_dzialalnosci", "forma_opodatkowania", "sposob_zaplaty", "status_podatkowy"}
    if field not in allowed:
        raise ValueError("Unsupported company option field")
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT {field} AS value FROM firmy "
            f"WHERE {field} IS NOT NULL AND TRIM({field}) <> '' ORDER BY {field}"
        ).fetchall()
        return [str(row["value"]) for row in rows]
    finally:
        conn.close()


def get_tax_offices() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM urzedy_skarbowe ORDER BY nazwa"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _find_company_by_nip(nip: str) -> Optional[int]:
    if not nip:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM firmy WHERE nip = %s", (nip.strip(),))
    row = cur.fetchone()
    conn.close()
    return int(row["id"]) if row else None


# ============================================================
#  ALIASES — zgodne z CompanyForm
# ============================================================

def _apply_aliases(payload: Dict[str, Any]) -> Dict[str, Any]:
    mapped = dict(payload)

    # działalność
    if "dz_miasto" in payload:
        mapped["miasto"] = payload["dz_miasto"]
    if "dz_kod" in payload:
        mapped["kod_pocztowy"] = payload["dz_kod"]
    if "dz_ulica" in payload:
        mapped["ulica"] = payload["dz_ulica"]
    if "dz_budynek" in payload:
        mapped["adres_budynek"] = payload["dz_budynek"]
    if "dz_lokal" in payload:
        mapped["adres_lokal"] = payload["dz_lokal"]

    if "dz_wojewodztwo" in payload:
        mapped["wojewodztwo"] = payload["dz_wojewodztwo"]
    if "dz_powiat" in payload:
        mapped["powiat"] = payload["dz_powiat"]
    if "dz_gmina" in payload:
        mapped["gmina"] = payload["dz_gmina"]
    if "dz_kraj" in payload:
        mapped["kraj"] = payload["dz_kraj"]

    # korespondencja
    if "ko_miasto" in payload:
        mapped["adres_koresp_miasto"] = payload["ko_miasto"]
    if "ko_kod" in payload:
        mapped["adres_koresp_kod_pocztowy"] = payload["ko_kod"]
    if "ko_ulica" in payload:
        mapped["adres_koresp_ulica"] = payload["ko_ulica"]
    if "ko_budynek" in payload:
        mapped["adres_koresp_budynek"] = payload["ko_budynek"]
    if "ko_lokal" in payload:
        mapped["adres_koresp_lokal"] = payload["ko_lokal"]

    if "ko_wojewodztwo" in payload:
        mapped["adres_koresp_wojewodztwo"] = payload["ko_wojewodztwo"]
    if "ko_powiat" in payload:
        mapped["adres_koresp_powiat"] = payload["ko_powiat"]
    if "ko_gmina" in payload:
        mapped["adres_koresp_gmina"] = payload["ko_gmina"]
    if "ko_kraj" in payload:
        mapped["adres_koresp_kraj"] = payload["ko_kraj"]

    # zamieszkanie
    if "zam_miasto" in payload:
        mapped["adres_zam_miasto"] = payload["zam_miasto"]
    if "zam_kod" in payload:
        mapped["adres_zam_kod_pocztowy"] = payload["zam_kod"]
    if "zam_ulica" in payload:
        mapped["adres_zam_ulica"] = payload["zam_ulica"]
    if "zam_budynek" in payload:
        mapped["adres_zam_budynek"] = payload["zam_budynek"]
    if "zam_lokal" in payload:
        mapped["adres_zam_lokal"] = payload["zam_lokal"]

    if "zam_wojewodztwo" in payload:
        mapped["adres_zam_wojewodztwo"] = payload["zam_wojewodztwo"]
    if "zam_powiat" in payload:
        mapped["adres_zam_powiat"] = payload["zam_powiat"]
    if "zam_gmina" in payload:
        mapped["adres_zam_gmina"] = payload["zam_gmina"]
    if "zam_kraj" in payload:
        mapped["adres_zam_kraj"] = payload["zam_kraj"]

    # kontakt
    imie = payload.get("kont_imie")
    nazw = payload.get("kont_nazwisko") or payload.get("kont_nazw")
    if imie or nazw:
        mapped["osoba_kontaktowa"] = " ".join([x for x in [imie, nazw] if x]).strip()
    if "kont_email" in payload:
        mapped["adres_email"] = payload["kont_email"]
    if "kont_tel" in payload:
        mapped["nr_telefonu"] = payload["kont_tel"]

    # PKD (jeśli kiedyś przyjdzie jako lista)
    if "kody_pkd" in payload and isinstance(payload["kody_pkd"], list):
        mapped["kody_pkd"] = ", ".join(payload["kody_pkd"])

    return mapped


# ============================================================
#  SAVE COMPANY
# ============================================================

def _sync_assignees(cur, firma_id: int) -> None:
    """Przypisuje wybraną księgową/kadrową do firmy w pracownik_firma,
    dzięki czemu filtr firm pracownika działa automatycznie."""
    row = cur.execute(
        "SELECT ksiegowa_id, kadrowa_id FROM firmy WHERE id = %s", (firma_id,)
    ).fetchone()
    if row is None:
        return
    for column, marker in ASSIGNEE_FIELDS.items():
        emp_id = row[column]
        cur.execute(
            "DELETE FROM pracownik_firma WHERE firma_id = %s AND rola = %s "
            "AND pracownik_id IS DISTINCT FROM %s::integer",
            (firma_id, marker, emp_id),
        )
        if not emp_id:
            continue
        exists = cur.execute(
            "SELECT 1 FROM pracownik_firma WHERE pracownik_id = %s AND firma_id = %s",
            (emp_id, firma_id),
        ).fetchone()
        if not exists:
            cur.execute(
                "INSERT INTO pracownik_firma (pracownik_id, firma_id, rola) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (emp_id, firma_id, marker),
            )


def save_company(payload: Dict[str, Any], actor_id: Optional[int] = None) -> int:
    conn = get_conn()
    cur = conn.cursor()

    mapped = _apply_aliases(payload)
    data = {
        f: mapped.get(f)
        for f in FIRMA_FIELDS
        if mapped.get(f) is not None or (f in GLOBAL_DEFAULT_FLAGS and f in mapped)
    }
    from app.companies.company_names import clean_company_name

    for name_field in ("nazwa", "nazwa_skrocona"):
        if name_field in data:
            data[name_field] = clean_company_name(data[name_field])
    services_changed: Optional[bool] = None

    try:
        if actor_id and not can(actor_id, "can_edit_companies"):
            raise PermissionError("Brak uprawnień do edycji firm.")
        if mapped.get("id"):
            firma_id = int(mapped["id"])
            if (
                actor_id
                and not is_administrator(actor_id)
                and firma_id not in assigned_company_ids(actor_id)
            ):
                raise PermissionError("Brak dostępu do tej firmy.")
            if "uslugi_zawieszone" in data:
                data["uslugi_zawieszone"] = 1 if data["uslugi_zawieszone"] else 0
                cur.execute("SELECT uslugi_zawieszone FROM firmy WHERE id = %s", (firma_id,))
                prev = cur.fetchone()
                if prev and int(prev[0] or 0) != data["uslugi_zawieszone"]:
                    services_changed = bool(data["uslugi_zawieszone"])
            if data:
                set_clause = ", ".join([f"{col} = %s" for col in data.keys()])
                params = [data[col] for col in data.keys()] + [firma_id]
                cur.execute(f"UPDATE firmy SET {set_clause} WHERE id = %s", params)
        else:
            nip = mapped.get("nip")
            existing = _find_company_by_nip(nip) if nip else None
            if existing:
                firma_id = existing
                if data:
                    set_clause = ", ".join([f"{col} = %s" for col in data.keys()])
                    params = [data[col] for col in data.keys()] + [firma_id]
                    cur.execute(f"UPDATE firmy SET {set_clause} WHERE id = %s", params)
            else:
                cols = ", ".join(data.keys())
                placeholders = ", ".join(["%s"] * len(data))
                params = [data[col] for col in data.keys()]
                cur.execute(f"INSERT INTO firmy ({cols}) VALUES ({placeholders}) RETURNING id", params)
                _new_id = cur.fetchone()[0]
                firma_id = _new_id

        if any(k in mapped for k in ASSIGNEE_FIELDS):
            _sync_assignees(cur, firma_id)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if services_changed is not None:
        from app.db.dao_tasks import notify_services_changed
        notify_services_changed(firma_id, services_changed)
    return firma_id


def set_services_suspended(firma_id: int, suspended: bool, actor_id: Optional[int] = None) -> bool:
    """Włącza/wyłącza zawieszenie usług (niezależne od statusu i CEIDG).
    Przy zmianie wysyła powiadomienie do wszystkich pracowników. Zwraca True, gdy był zmiana."""
    if actor_id and not can(actor_id, "can_edit_companies"):
        raise PermissionError("Brak uprawnień do edycji firm.")
    if (
        actor_id
        and not is_administrator(actor_id)
        and firma_id not in assigned_company_ids(actor_id)
    ):
        raise PermissionError("Brak dostępu do tej firmy.")
    flag = 1 if suspended else 0
    conn = get_conn()
    try:
        row = conn.execute("SELECT uslugi_zawieszone FROM firmy WHERE id = %s", (firma_id,)).fetchone()
        if not row or int(row[0] or 0) == flag:
            return False
        conn.execute("UPDATE firmy SET uslugi_zawieszone = %s WHERE id = %s", (flag, firma_id))
        conn.commit()
    finally:
        conn.close()
    from app.db.dao_tasks import notify_services_changed
    notify_services_changed(firma_id, bool(flag))
    return True


# ============================================================
#  DELETE / STATUS

def delete_company(firma_id: int, actor_id: Optional[int] = None) -> None:
    conn = get_conn()
    cur = conn.cursor()
    if actor_id and not can(actor_id, "can_edit_companies"):
        conn.close()
        raise PermissionError("Brak uprawnień do usuwania firm.")
    if (
        actor_id
        and not is_administrator(actor_id)
        and firma_id not in assigned_company_ids(actor_id)
    ):
        conn.close()
        raise PermissionError("Brak dostępu do tej firmy.")
    cur.execute("DELETE FROM firmy WHERE id = %s", (firma_id,))
    cur.execute("DELETE FROM pracownik_firma WHERE firma_id = %s", (firma_id,))
    cur.execute("DELETE FROM company_notes WHERE firma_id = %s", (firma_id,))
    cur.execute("DELETE FROM udzialowcy WHERE firma_id = %s", (firma_id,))
    cur.execute("DELETE FROM company_contacts WHERE firma_id = %s", (firma_id,))
    conn.commit()
    conn.close()


def set_company_status(firma_id: int, status: str, zawieszenia_dzialalnosci: Optional[str] = None) -> None:
    conn = get_conn()
    cur = conn.cursor()
    if zawieszenia_dzialalnosci is not None:
        cur.execute("""
            UPDATE firmy
            SET status = %s, zawieszenia_dzialalnosci = %s
            WHERE id = %s
        """, (status, zawieszenia_dzialalnosci, firma_id))
    else:
        cur.execute("UPDATE firmy SET status = %s WHERE id = %s", (status, firma_id))
    conn.commit()
    conn.close()


def archive_company(firma_id: int) -> None:
    set_company_status(firma_id, "Archiwalne")


def suspend_company_manual(firma_id: int, date_str: str) -> None:
    set_company_status(firma_id, "Zawieszone", date_str)


# ============================================================
#  NOTES
# ============================================================

def get_company_notes(firma_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, tresc, autor_id, data_utworzenia
        FROM company_notes
        WHERE firma_id = %s
        ORDER BY data_utworzenia DESC
        LIMIT %s
    """, (firma_id, limit))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
#  EMPLOYEE ASSIGNMENT
# ============================================================

def assign_employee_to_company(pracownik_id: int, firma_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pracownik_firma WHERE pracownik_id = %s AND firma_id = %s", (pracownik_id, firma_id))
    if not cur.fetchone():
        cur.execute("INSERT INTO pracownik_firma (pracownik_id, firma_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (pracownik_id, firma_id))
        conn.commit()
    conn.close()


def unassign_employee_from_company(pracownik_id: int, firma_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM pracownik_firma WHERE pracownik_id = %s AND firma_id = %s", (pracownik_id, firma_id))
    conn.commit()
    conn.close()


def get_company_employees(firma_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.imie, p.nazwisko, p.email
        FROM pracownicy p
        JOIN pracownik_firma pf ON pf.pracownik_id = p.id
        WHERE pf.firma_id = %s
        ORDER BY p.imie, p.nazwisko
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
#  CENNIKI OGÓLNE
# ============================================================

def _sort_cenniki_rosnaco(rows: List[Row]) -> List[Dict[str, Any]]:
    from app.companies.cennik_keys import cennik_sort_key

    return [dict(r) for r in sorted(rows, key=lambda r: cennik_sort_key(r["klucz"]))]


def get_cennik_by_key(klucz: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cenniki WHERE klucz = %s", (klucz,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
#  GLOBAL PRICING / OPIS CENNIKA
# ============================================================

def get_global_pricing() -> List[Dict[str, Any]]:
    conn = get_conn()
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
    cur = conn.cursor()
    vat_suffix = "T" if vat_czynny else "N"
    pattern = f"{typ}_{vat_suffix}_%"
    cur.execute("""
        SELECT *
        FROM cenniki
        WHERE klucz ILIKE %s
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


# ============================================================
#  KSIĘGOWOŚĆ OPTIONS
# ============================================================

def get_ksiegowosc_options() -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT klucz,
               cena_netto_ksiegowosc AS netto,
               cena_brutto_ksiegowosc AS brutto,
               cena_za_dokument_ponad_limit AS ponad_limit,
               zdrowotna
        FROM cenniki
    """)
    rows = cur.fetchall()
    conn.close()

    sorted_rows = _sort_cenniki_rosnaco(rows)

    return [
        {
            "klucz": r["klucz"],
            "netto": float(r["netto"] or 0),
            "brutto": float(r["brutto"] or 0),
            "ponad_limit": float(r["ponad_limit"] or 0),
            "zdrowotna": float(r["zdrowotna"] or 0),
        }
        for r in sorted_rows
    ]


def get_ksiegowosc_by_key(klucz: str) -> Optional[Dict[str, Any]]:
    for item in get_ksiegowosc_options():
        if item["klucz"] == klucz:
            return item
    return None


# ============================================================
#  PRICING KADRY
# ============================================================

def get_pricing_kadry() -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT klucz, cena_netto_kadry
        FROM cenniki_kadry
        ORDER BY klucz
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
#  KPIR CENNIKI / AUTO / MANUAL
# ============================================================

def get_kpir_cenniki() -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT klucz, cena_netto_ksiegowosc, cena_za_dokument_ponad_limit, netto, brutto
        FROM cenniki
        WHERE typ = 'KPIR' OR klucz ILIKE 'KPIR_%%' OR klucz ILIKE 'KPIR-%%' OR klucz ILIKE 'KPIR%%'
    """)
    rows = cur.fetchall()
    conn.close()
    return _sort_cenniki_rosnaco(rows)


def _extract_limit_from_klucz(klucz: str) -> Optional[int]:
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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT manual_cennik_override FROM firmy WHERE id = %s", (firma_id,))
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
            SET cennik_kpir_auto = NULL, cena_ksiegowosc_auto = 0, limit_dokumentow = %s
            WHERE id = %s
        """, (limit_dokumentow, firma_id))
        conn.commit()
        conn.close()
        return None
    klucz, cena = choice
    cur.execute("""
        UPDATE firmy
        SET cennik_kpir_auto = %s, cena_ksiegowosc_auto = %s, limit_dokumentow = %s
        WHERE id = %s
    """, (klucz, cena, limit_dokumentow, firma_id))
    conn.commit()
    conn.close()
    return {"klucz": klucz, "cena": cena}


def set_manual_cennik_for_company(firma_id: int, manual_klucz: Optional[str]) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    if not manual_klucz:
        cur.execute("""
            UPDATE firmy
            SET cennik_kpir_manual = NULL, manual_cennik_override = 0
            WHERE id = %s
        """, (firma_id,))
        conn.commit()
        conn.close()
        return None
    cur.execute("""
        SELECT klucz, cena_netto_ksiegowosc, netto
        FROM cenniki
        WHERE klucz = %s AND (typ = 'KPIR' OR klucz ILIKE 'KPIR_%%' OR klucz ILIKE 'KPIR-%%' OR klucz ILIKE 'KPIR%%')
        LIMIT 1
    """, (manual_klucz,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    cena = row["cena_netto_ksiegowosc"] if row["cena_netto_ksiegowosc"] is not None else row.get("netto", 0.0)
    cur.execute("""
        UPDATE firmy
        SET cennik_kpir_manual = %s, manual_cennik_override = 1, cena_ksiegowosc_auto = %s
        WHERE id = %s
    """, (manual_klucz, cena, firma_id))
    conn.commit()
    conn.close()
    return {"klucz": manual_klucz, "cena": float(cena)}


def get_company_pricing_info(firma_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT cennik_kpir_auto, cennik_kpir_manual, manual_cennik_override,
               cena_ksiegowosc_auto, limit_dokumentow
        FROM firmy
        WHERE id = %s
    """, (firma_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
#  LIMITY (tylko limit_dokumentow)
# ============================================================

def get_company_limits(firma_id: int) -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT limit_dokumentow
        FROM firmy
        WHERE id = %s
    """, (firma_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"limit_dokumentow": 0}
    return dict(row)


def update_company_limits(firma_id: int, limit_dok: Optional[int] = None) -> None:
    conn = get_conn()
    cur = conn.cursor()
    parts = []
    params = []
    if limit_dok is not None:
        parts.append("limit_dokumentow = %s")
        params.append(limit_dok)
    if parts:
        params.append(firma_id)
        cur.execute(f"UPDATE firmy SET {', '.join(parts)} WHERE id = %s", tuple(params))
        conn.commit()
    conn.close()


# ============================================================
#  IDENTIFIER SEARCH
# ============================================================

def find_company_by_identifier(identifier: str) -> List[Dict[str, Any]]:
    if not identifier:
        return []
    identifier = identifier.strip()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nazwa, nip, regon, krs, miasto
        FROM firmy
        WHERE nip = %s OR regon = %s OR krs = %s
        LIMIT 50
    """, (identifier, identifier, identifier))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
#  STATUS COUNTS
# ============================================================

def count_companies_by_status() -> Dict[str, int]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(status, 'Aktywne') AS status, COUNT(*) AS cnt
        FROM firmy
        GROUP BY COALESCE(status, 'Aktywne')
    """)
    rows = cur.fetchall()
    conn.close()
    return {r[0]: int(r[1]) for r in rows}


# ============================================================
#  RECENT COMPANIES
# ============================================================

def recent_companies(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nazwa, nip, data_rozpoczecia_dg
        FROM firmy
        ORDER BY id DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
#  ENSURE COMPANY COLUMNS EXIST (migracje)
# ============================================================

def ensure_company_columns_exist():
    conn = get_conn()
    cur = conn.cursor()
    cols = {
        "zawieszenia_dzialalnosci": "TEXT",
        "kody_pkd": "TEXT",
        "glowny_cennik": "TEXT",
        "glowny_cennik_manualny": "INTEGER",
        "manual_cennik_override": "INTEGER",
        "cennik_kpir_auto": "TEXT",
        "cennik_kpir_manual": "TEXT",
        "cena_ksiegowosc_auto": "REAL",
        "fakturowac": "INTEGER",
        "adres_lokal": "TEXT",
        "adres_koresp_budynek": "TEXT",
        "adres_koresp_lokal": "TEXT",
        "adres_zam_budynek": "TEXT",
        "adres_zam_lokal": "TEXT",
        "wojewodztwo": "TEXT",
        "powiat": "TEXT",
        "gmina": "TEXT",
        "kraj": "TEXT",
        "numer_klienta": "TEXT",
        "adres_doreczen_elektronicznych": "TEXT",
        "urzad_skarbowy": "TEXT",
        "miesiac_dok_fakturowanie": "TEXT",
        "miesiac_prac_fakturowanie": "TEXT",
        "dzien_dostarczania_dokumentow": "INTEGER",
        "seria_fv": "TEXT",
        "seria_kfv": "TEXT",
        "sposob_zaplaty": "TEXT",
        "status_podatkowy": "TEXT",
        "konto_bankowe": "TEXT",
        "uwagi": "TEXT",
        "kategoria_domyslna": "TEXT",
        "ksiegowa_id": "INTEGER",
        "kadrowa_id": "INTEGER",
    }
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = \'firmy\'"
    )
    existing = {r[0] for r in cur.fetchall()}
    for col, typ in cols.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE firmy ADD COLUMN IF NOT EXISTS {col} {typ}")
    conn.commit()
    conn.close()


# ============================================================
#  EXPORT / IMPORT
# ============================================================

def export_companies_csv(path: str) -> None:
    import csv
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM firmy")
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(rows[0].keys())
        for r in rows:
            writer.writerow([r[k] for k in r.keys()])
    conn.close()


def import_companies_from_rows(rows: List[Dict[str, Any]]) -> int:
    count = 0
    for r in rows:
        try:
            save_company(r)
            count += 1
        except Exception:
            continue
    return count


# ============================================================
#  PARTNERS (udzialowcy) — pełny CRUD
# ============================================================

PARTNER_FIELDS = [
    "imie_pierwsze", "imie_drugie", "nazwisko", "nazwa_organizacji",
    "pesel", "nip", "regon", "krs",
    "ulica", "nr_domu", "nr_mieszkania",
    "wojewodztwo", "powiat", "gmina", "miejscowosc",
    "kod_pocztowy", "poczta", "panstwo",
    "email", "telefon", "data_urodzenia",
    "stanowisko", "procent",
]


def count_zdrowotna_units(firma_id: int) -> int:
    """Liczba sztuk zdrowotnej do faktury: tylu, ilu wspólników; jednoosobowa = 1."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM udzialowcy WHERE firma_id = %s", (firma_id,)
        ).fetchone()
        return max(1, int(row[0] or 0))
    finally:
        conn.close()


def get_partners(firma_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM udzialowcy
        WHERE firma_id = %s
        ORDER BY id
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_partner(firma_id: int, data: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()

    values = [data.get(f) for f in PARTNER_FIELDS]

    if data.get("id"):
        placeholders = ", ".join([f"{f} = %s" for f in PARTNER_FIELDS])
        cur.execute(f"""
            UPDATE udzialowcy SET {placeholders}
            WHERE id = %s
        """, values + [data["id"]])
    else:
        placeholders = ", ".join(["%s" for _ in PARTNER_FIELDS])
        cur.execute(f"""
            INSERT INTO udzialowcy (firma_id, {", ".join(PARTNER_FIELDS)})
            VALUES (%s, {placeholders})
        """, [firma_id] + values)

    conn.commit()
    conn.close()


def delete_partner(partner_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM udzialowcy WHERE id = %s", (partner_id,))
    conn.commit()
    conn.close()


# ============================================================
#  CONTACTS (company_contacts) — pełny CRUD
# ============================================================

CONTACT_FIELDS = ["imie", "nazwisko", "email", "telefon", "rola"]


def get_contacts(firma_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM company_contacts
        WHERE firma_id = %s
        ORDER BY id
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_contact(firma_id: int, data: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()

    values = [data.get(f) for f in CONTACT_FIELDS]

    if data.get("id"):
        placeholders = ", ".join([f"{f} = %s" for f in CONTACT_FIELDS])
        cur.execute(f"""
            UPDATE company_contacts SET {placeholders}
            WHERE id = %s
        """, values + [data["id"]])
    else:
        placeholders = ", ".join(["%s" for _ in CONTACT_FIELDS])
        cur.execute(f"""
            INSERT INTO company_contacts (firma_id, {", ".join(CONTACT_FIELDS)})
            VALUES (%s, {placeholders})
        """, [firma_id] + values)

    conn.commit()
    conn.close()


def delete_contact(contact_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM company_contacts WHERE id = %s", (contact_id,))
    conn.commit()
    conn.close()

