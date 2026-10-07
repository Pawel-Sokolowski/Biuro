import csv
import openpyxl
import sqlite3
import re
import os
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()
DB_NAME = os.getenv("DB_NAME")

from app.utils.parsing import (
    normalize_identifier,
    normalize_pkd_codes,
    parse_date_value,
    resolve_employee_ids,
)


# ============================================================
# PARSERY POMOCNICZE
# ============================================================
def split_address_line(line: str) -> Tuple[str, str, str]:
    if not line:
        return "", "", ""

    t = str(line).strip()

    m = re.match(r"(.+?)\s+(\d+)\s*/\s*(\d+)", t)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()

    m = re.match(r"(.+?)\s+(\d+)$", t)
    if m:
        return m.group(1).strip(), m.group(2).strip(), ""

    m = re.match(r"(\d+)\s*/\s*(\d+)", t)
    if m:
        return "", m.group(1).strip(), m.group(2).strip()

    m = re.match(r"(\d+)$", t)
    if m:
        return "", m.group(1).strip(), ""

    return t, "", ""


def parse_num(val) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "."))
    except Exception:
        return None


# ============================================================
# PARSERY CENNIKÓW
# ============================================================
def parse_cennik_ksiegowy(text: str) -> Dict[str, Any]:
    out = {
        "limit_dokumentow": None,
        "cena_za_dokument": None,
        "ryczalt_ksiegowy": None,
        "zdrowotna": None,
    }

    if not text:
        return out

    t = text.lower()

    m = re.search(r"limit\s+(\d+)", t)
    if m:
        out["limit_dokumentow"] = int(m.group(1))

    m = re.search(r"ponad\s+limit.*?(\d+[,.]\d+)", t)
    if m:
        out["cena_za_dokument"] = float(m.group(1).replace(",", "."))

    m = re.search(r"ryczałt.*?(\d+[,.]\d+)", t)
    if m:
        out["ryczalt_ksiegowy"] = float(m.group(1).replace(",", "."))

    m = re.search(r"zdrowotna.*?(\d+[,.]\d+)", t)
    if m:
        out["zdrowotna"] = float(m.group(1).replace(",", "."))

    return out


def parse_cennik_kadrowy(text: str) -> Dict[str, Any]:
    out = {
        "zdrowotna": None,
        "uop_ilosc": 0,
        "uop_cennik": None,
        "uz_ilosc": 0,
        "uz_cennik": None,
        "uod_ilosc": 0,
        "uod_cennik": None,
    }

    if not text:
        return out

    t = text.lower()

    m = re.search(r"zdrowotna.*?(\d+[,.]\d+)", t)
    if m:
        out["zdrowotna"] = float(m.group(1).replace(",", "."))

    m = re.search(r"uop.*?(\d+[,.]\d+)", t)
    if m:
        out["uop_ilosc"] = 1
        out["uop_cennik"] = float(m.group(1).replace(",", "."))

    m = re.search(r"uz.*?(\d+[,.]\d+)", t)
    if m:
        out["uz_ilosc"] = 1
        out["uz_cennik"] = float(m.group(1).replace(",", "."))

    m = re.search(r"uod.*?(\d+[,.]\d+)", t)
    if m:
        out["uod_ilosc"] = 1
        out["uod_cennik"] = float(m.group(1).replace(",", "."))

    return out


def parse_cennik_czasowy(text: str) -> Dict[str, Any]:
    out = {"ryczalt_czasowy": None}

    if not text:
        return out

    t = text.lower()

    m = re.search(r"ryczałt.*?(\d+[,.]\d+)", t)
    if m:
        out["ryczalt_czasowy"] = float(m.group(1).replace(",", "."))

    return out


# ============================================================
# WCZYTANIE PLIKU CSV/XLSX
# ============================================================
def _load_rows_from_file(filepath: str) -> Tuple[List[str], List[Any]]:
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".csv":
        with open(filepath, newline="", encoding="utf-8") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiter = "," if sample.count(",") >= sample.count(";") else ";"
            reader = csv.reader(f, delimiter=delimiter)
            header = next(reader, None)
            rows = [row for row in reader]
    else:
        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active
        header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
        rows = [tuple(cell for cell in row) for row in ws.iter_rows(min_row=2, values_only=True)]

    if not header:
        raise ValueError("Brak nagłówków w pliku.")

    headers = [str(h).strip() if h is not None else "" for h in header]
    return headers, rows


# ============================================================
# BUDOWANIE PAYLOADU Z WIERSZA
# ============================================================
def _build_payload_from_row(
    headers: List[str],
    header_index: Dict[str, int],
    row: Any,
) -> Dict[str, Any]:

    def get(col: str) -> Any:
        idx = header_index.get(col)
        return row[idx] if idx is not None else None

    payload: Dict[str, Any] = {}

    # podstawowe
    payload["nazwa"] = get("Nazwa Firmy")
    payload["nazwa_skrocona"] = get("Nazwa skrócona")
    payload["nip"] = normalize_identifier(get("NIP"), {10})
    payload["regon"] = normalize_identifier(get("REGON"), {9, 14})
    payload["krs"] = normalize_identifier(get("KRS"), {10})
    payload["pesel"] = normalize_identifier(get("PESEL"), {11})
    payload["forma_dzialalnosci"] = get("Forma działalności")
    payload["forma_opodatkowania"] = get("Forma opodatkowania")
    payload["analityka_fk"] = get("Analityka FK")
    payload["osoba_kontaktowa"] = get("Osoba kontaktowa")
    payload["adres_email"] = get("Adres email")
    payload["nr_telefonu"] = get("Nr telefonu")

    # adres główny
    payload["miasto"] = get("Miasto")
    payload["kod_pocztowy"] = get("Kod pocztowy")
    ulica_raw = get("Ulica")
    ulica, budynek, lokal = split_address_line(ulica_raw)
    payload["ulica"] = ulica
    payload["adres_budynek"] = budynek
    payload["adres_lokal"] = lokal

    # adres zamieszkania
    payload["adres_zam_miasto"] = get("Adres zamieszkania - miasto")
    payload["adres_zam_kod_pocztowy"] = get("Adres zamieszkania - kod pocztowy")
    ulica_zam_raw = get("Adres zamieszkania - ulica")
    ulica_zam, budynek_zam, lokal_zam = split_address_line(ulica_zam_raw)
    payload["adres_zam_ulica"] = ulica_zam
    payload["adres_zam_budynek"] = budynek_zam
    payload["adres_zam_lokal"] = lokal_zam

    # adres korespondencyjny
    payload["adres_koresp_miasto"] = (
        get("Adres korespondencyjny - miasto")
        or get("Adres korespondpondencyjny - miasto")
    )
    payload["adres_koresp_kod_pocztowy"] = get("Adres korespondencyjny - kod pocztowy")
    ulica_koresp_raw = get("Adres korespondencyjny - ulica")
    ulica_koresp, budynek_koresp, lokal_koresp = split_address_line(ulica_koresp_raw)
    payload["adres_koresp_ulica"] = ulica_koresp
    payload["adres_koresp_budynek"] = budynek_koresp
    payload["adres_koresp_lokal"] = lokal_koresp

    # daty, pkd, zawieszenia
    payload["data_rozpoczecia_dg"] = parse_date_value(get("Data rozpoczęcia działalności gospodarczej"))
    payload["data_rozpoczecia_sprzedazy"] = parse_date_value(get("Data rozpoczęcia sprzedaży dla osoby fizycznej"))
    payload["data_koniec_zus_start"] = parse_date_value(get("Data zakończenia ulgi ZUS na start"))
    payload["data_koniec_zus_pref"] = parse_date_value(get("Data zakończenia ZUS preferencyjny"))
    payload["data_koniec_maly_zus"] = parse_date_value(get("Data zakończenia małego ZUS PLUS"))
    payload["data_koniec_a1"] = parse_date_value(get("Data zakończenia A1"))
    payload["data_rozpoczecia_obslugi"] = parse_date_value(get("Data rozpoczęcia obsługi"))
    payload["data_zakonczenia_obslugi"] = parse_date_value(get("Data zakończenia obsługi"))
    payload["zawieszenia_dzialalnosci"] = get("Zawieszenia działalności")
    payload["kody_pkd"] = normalize_pkd_codes(get("Kody PKD"))

    # cenniki księgowe
    cennik_ksiegowy_text = str(get("Cenniki usług księgowych") or "")
    cks = parse_cennik_ksiegowy(cennik_ksiegowy_text)
    payload["cennik_ksiegowy"] = cennik_ksiegowy_text
    payload["cennik_ksiegowy_parsed"] = cks
    payload["limit_dokumentow"] = cks["limit_dokumentow"]
    payload["zdrowotna"] = cks["zdrowotna"]

    # cenniki kadrowe
    cennik_kadrowy_text = str(get("Cenniki usług kadrowych") or "")
    ckad = parse_cennik_kadrowy(cennik_kadrowy_text)
    payload["cennik_kadrowy"] = cennik_kadrowy_text
    payload["cennik_kadrowy_parsed"] = ckad
    payload["uop_ilosc"] = ckad["uop_ilosc"]
    payload["uop_cennik"] = ckad["uop_cennik"]
    payload["uz_ilosc"] = ckad["uz_ilosc"]
    payload["uz_cennik"] = ckad["uz_cennik"]
    payload["uod_ilosc"] = ckad["uod_ilosc"]
    payload["uod_cennik"] = ckad["uod_cennik"]

    # cenniki czasowe
    cennik_czasowy_text = str(get("Cenniki czasowe") or "")
    cczas = parse_cennik_czasowy(cennik_czasowy_text)
    payload["cennik_czasowy"] = cennik_czasowy_text
    payload["cennik_czasowy_parsed"] = cczas
    payload["ryczalt_czasowy"] = cczas["ryczalt_czasowy"]

    # przypisani pracownicy
    payload["assigned_raw"] = get("Przypisani pracownicy")

    return payload


# ============================================================
# IMPORT DO PAYLOADÓW (dla wizardu)
# ============================================================
def import_companies_from_excel_file_payloads(filepath: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    try:
        headers, rows = _load_rows_from_file(filepath)
    except Exception as e:
        return [], [f"Plik nie mógł zostać wczytany: {e}"]

    header_index = {header: idx for idx, header in enumerate(headers)}

    payloads: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for row_number, row in enumerate(rows, start=2):
        if not any(v not in (None, "") for v in row):
            continue

        payload = _build_payload_from_row(headers, header_index, row)

        assigned_raw = payload.get("assigned_raw")
        if assigned_raw:
            try:
                unresolved = []
                parts = [p.strip() for p in str(assigned_raw).split(",") if p.strip()]
                for p in parts:
                    if not re.match(r"^[A-Za-zĄąĆćĘęŁłŃńÓóŚśŹźŻż ]+$", p):
                        unresolved.append(p)

                if unresolved:
                    warnings.append(
                        f"Wiersz {row_number}: nie znaleziono pracowników {', '.join(unresolved)}."
                    )
            except Exception:
                pass

        payloads.append(payload)

    return payloads, warnings


# ============================================================
# ZAPIS DO DB (stary importer)
# ============================================================
def _pricing_key(prefix: str, values: Dict[str, Any]) -> str:
    raw = "|".join(f"{key}={values.get(key)!r}" for key in sorted(values))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()
    return f"IMPORT_{prefix}_{digest}"


def _ensure_accounting_cennik(
    cursor: sqlite3.Cursor,
    payload: Dict[str, Any],
) -> Optional[str]:
    parsed = payload.get("cennik_ksiegowy_parsed") or {}
    text = str(payload.get("cennik_ksiegowy") or "").strip()
    if not text:
        return None

    limit_price = parsed.get("cena_za_dokument")
    base_price = parsed.get("ryczalt_ksiegowy")
    zdrowotna = parsed.get("zdrowotna") or 0
    if limit_price is None and base_price is None and zdrowotna is None:
        return None

    values = {
        "typ": "RYCZALT" if "rycza" in str(payload.get("forma_opodatkowania") or "").lower() else "KSIEGOWOSC",
        "limit": parsed.get("limit_dokumentow"),
        "limit_price": limit_price,
        "base_price": base_price,
        "zdrowotna": zdrowotna,
    }
    cursor.execute(
        """
        SELECT klucz FROM cenniki
        WHERE typ = ?
          AND cena_za_dokument_ponad_limit = ?
          AND netto = ?
          AND zdrowotna = ?
        ORDER BY id
        LIMIT 1
        """,
        (values["typ"], limit_price or 0, base_price, zdrowotna or 0),
    )
    existing = cursor.fetchone()
    key = existing[0] if existing else _pricing_key("KS", values)

    cursor.execute(
        """
        INSERT INTO cenniki (
            klucz, typ, cena_netto_ksiegowosc, cena_brutto_ksiegowosc,
            cena_za_dokument_ponad_limit, zdrowotna, netto, brutto
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(klucz) DO UPDATE SET
            typ = excluded.typ,
            cena_netto_ksiegowosc = excluded.cena_netto_ksiegowosc,
            cena_brutto_ksiegowosc = excluded.cena_brutto_ksiegowosc,
            cena_za_dokument_ponad_limit = excluded.cena_za_dokument_ponad_limit,
            zdrowotna = excluded.zdrowotna,
            netto = excluded.netto,
            brutto = excluded.brutto,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            key,
            values["typ"],
            base_price or 0,
            round((base_price or 0) * 1.23, 2),
            limit_price or 0,
            zdrowotna or 0,
            base_price,
            round((base_price or 0) * 1.23, 2) if base_price is not None else None,
        ),
    )
    return key


def _ensure_employee_cenniki(cursor: sqlite3.Cursor, payload: Dict[str, Any]) -> None:
    parsed = payload.get("cennik_kadrowy_parsed") or {}
    text = str(payload.get("cennik_kadrowy") or "").strip()
    if not text:
        return

    for service, price in (
        ("ZDROWOTNA", parsed.get("zdrowotna")),
        ("UOP", parsed.get("uop_cennik")),
        ("UZ", parsed.get("uz_cennik")),
        ("UOD", parsed.get("uod_cennik")),
    ):
        if price is None:
            continue
        cursor.execute(
            """
            SELECT klucz FROM cenniki_kadry
            WHERE cena_netto_kadry = ?
            ORDER BY id
            LIMIT 1
            """,
            (price,),
        )
        existing = cursor.fetchone()
        key = existing[0] if existing else _pricing_key(f"KADRY_{service}", {"price": price})
        cursor.execute(
            """
            INSERT INTO cenniki_kadry (
                klucz, cena_netto_kadry, cena_brutto_kadry, opis
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(klucz) DO UPDATE SET
                cena_netto_kadry = excluded.cena_netto_kadry,
                cena_brutto_kadry = excluded.cena_brutto_kadry,
                opis = excluded.opis,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, price, round(price * 1.23, 2), f"Import z Excela: {service}"),
        )


def _assign_employees(
    cursor: sqlite3.Cursor,
    firma_id: int,
    assigned_raw: Any,
    warnings: List[str],
    row_number: int,
) -> None:
    if not assigned_raw:
        return
    assigned_ids, unresolved = resolve_employee_ids(cursor, assigned_raw)
    for employee_id in assigned_ids:
        cursor.execute(
            """
            INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id)
            VALUES (?, ?, NULL)
            """,
            (employee_id, firma_id),
        )
    if unresolved:
        warnings.append(
            f"Wiersz {row_number}: nie znaleziono pracowników {', '.join(unresolved)}."
        )


def _apply_payload_to_db(
    cursor: sqlite3.Cursor,
    payload: Dict[str, Any],
    row_number: int,
    warnings: List[str],
) -> bool:

    company_name = str(payload.get("nazwa", "") or "").strip()
    if not company_name:
        warnings.append(f"Wiersz {row_number}: pominięto pustą nazwę firmy.")
        return False

    normalized_nip = normalize_identifier(payload.get("nip"), {10})
    company_id = None

    if normalized_nip:
        cursor.execute("SELECT id FROM firmy WHERE nip = ?", (normalized_nip,))
        row_db = cursor.fetchone()
        company_id = row_db[0] if row_db else None
    if company_id is None:
        cursor.execute("SELECT id FROM firmy WHERE nazwa = ?", (company_name,))
        row_db = cursor.fetchone()
        company_id = row_db[0] if row_db else None

    accounting_key = _ensure_accounting_cennik(cursor, payload)
    _ensure_employee_cenniki(cursor, payload)

    company_fields = [
        "nazwa", "nazwa_skrocona", "nip", "regon", "krs", "pesel",
        "forma_dzialalnosci", "forma_opodatkowania", "analityka_fk",
        "osoba_kontaktowa", "adres_email", "nr_telefonu", "miasto",
        "kod_pocztowy", "ulica", "adres_budynek", "adres_lokal",
        "adres_zam_miasto", "adres_zam_kod_pocztowy", "adres_zam_ulica",
        "adres_zam_budynek", "adres_zam_lokal", "adres_koresp_miasto",
        "adres_koresp_kod_pocztowy", "adres_koresp_ulica",
        "adres_koresp_budynek", "adres_koresp_lokal",
        "data_rozpoczecia_dg", "data_rozpoczecia_sprzedazy",
        "data_koniec_zus_start", "data_koniec_zus_pref",
        "data_koniec_maly_zus", "data_koniec_a1",
        "data_rozpoczecia_obslugi", "data_zakonczenia_obslugi",
        "zawieszenia_dzialalnosci", "kody_pkd", "cennik_ksiegowy",
        "cennik_kadrowy", "cennik_czasowy", "limit_dokumentow",
        "zdrowotna", "uop_ilosc", "uop_cennik", "uz_ilosc",
        "uz_cennik", "uod_ilosc", "uod_cennik",
    ]
    if accounting_key:
        company_fields.append("glowny_cennik")
        payload["glowny_cennik"] = accounting_key

    values = []
    fields = []
    for field in company_fields:
        value = payload.get(field)
        if value not in (None, "") or field == "nazwa":
            fields.append(field)
            values.append(value)

    if company_id:
        assignments = ", ".join(f"{field} = ?" for field in fields)
        cursor.execute(
            f"UPDATE firmy SET {assignments} WHERE id = ?",
            (*values, company_id),
        )
        created = False
    else:
        placeholders = ", ".join("?" for _ in fields)
        cursor.execute(
            f"INSERT INTO firmy ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
        company_id = int(cursor.lastrowid)
        created = True

    _assign_employees(cursor, company_id, payload.get("assigned_raw"), warnings, row_number)
    return created


# ============================================================
# KLASYCZNY IMPORTER — ZAPIS DO DB
# ============================================================
def import_companies_from_excel_file(
    filepath: str, db_path: str = DB_NAME
) -> Dict[str, Any]:
    try:
        headers, rows = _load_rows_from_file(filepath)
    except Exception as e:
        return {"created": 0, "updated": 0, "warnings": [f"Plik nie mógł zostać wczytany: {e}"]}

    header_index = {header: idx for idx, header in enumerate(headers)}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    created_count = 0
    updated_count = 0
    warnings: List[str] = []

    try:
        for row_number, row in enumerate(rows, start=2):
            if not any(v not in (None, "") for v in row):
                continue

            payload = _build_payload_from_row(headers, header_index, row)

            if _apply_payload_to_db(cursor, payload, row_number, warnings):
                created_count += 1
            else:
                updated_count += 1

            if row_number % 20 == 0:
                try:
                    conn.commit()
                except Exception:
                    pass

        conn.commit()
        return {"created": created_count, "updated": updated_count, "warnings": warnings}
    finally:
        conn.close()


def save_imported_payload(
    payload: Dict[str, Any],
    db_path: str = DB_NAME,
    row_number: int = 2,
) -> Dict[str, Any]:
    """Save one reviewed wizard payload using the complete import path."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    warnings: List[str] = []
    try:
        created = _apply_payload_to_db(cursor, payload, row_number, warnings)
        conn.commit()
        return {"created": int(created), "updated": int(not created), "warnings": warnings}
    finally:
        conn.close()


# ============================================================
# SZYBKI IMPORTER — ZAPIS DO DB
# ============================================================
def import_companies_fast_from_excel_file(
    filepath: str, db_path: str = DB_NAME
) -> Dict[str, Any]:
    try:
        headers, rows = _load_rows_from_file(filepath)
    except Exception as e:
        return {"created": 0, "updated": 0, "warnings": [f"Plik nie mógł zostać wczytany: {e}"]}

    header_index = {header: idx for idx, header in enumerate(headers)}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")

    created = 0
    updated = 0
    warnings: List[str] = []

    try:
        for row_number, row in enumerate(rows, start=2):
            if not any(v not in (None, "") for v in row):
                continue

            payload = _build_payload_from_row(headers, header_index, row)

            if _apply_payload_to_db(cursor, payload, row_number, warnings):
                created += 1
            else:
                updated += 1

        conn.commit()
        return {"created": created, "updated": updated, "warnings": warnings}
    finally:
        conn.close()


# ============================================================
# IMPORT CENNIKA KSIĘGOWEGO — ZWRACA PAYLOADY
# ============================================================
def import_cennik_from_excel(filepath: str, db_path: str = DB_NAME) -> List[Dict[str, Any]]:
    ext = os.path.splitext(filepath)[1].lower()

    try:
        if ext == ".csv":
            with open(filepath, newline="", encoding="utf-8") as f:
                sample = f.read(4096)
                f.seek(0)
                delimiter = "," if sample.count(",") >= sample.count(";") else ";"
                reader = csv.reader(f, delimiter=delimiter)
                header = next(reader, None)
                rows = [row for row in reader]
        else:
            wb = openpyxl.load_workbook(filepath, data_only=True)
            ws = wb.active
            header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
            rows = [tuple(cell for cell in row) for row in ws.iter_rows(min_row=2, values_only=True)]
    except Exception:
        return []

    if not header:
        return []

    headers = [str(h).strip().lower() if h is not None else "" for h in header]
    idx = {h: i for i, h in enumerate(headers)}

    required = [
        "klucz",
        "cena netto 2024",
        "za przekroczenie limitu netto",
        "zdrowotna",
        "netto",
        "brutto",
    ]

    for col in required:
        if col not in idx:
            return []

    payloads = []

    def get_cell(row, key):
        i = idx.get(key)
        if i is None:
            return None
        try:
            return row[i]
        except Exception:
            return None

    for row in rows:
        klucz_raw = get_cell(row, "klucz")
        if klucz_raw is None or str(klucz_raw).strip() == "":
            continue

        klucz = str(klucz_raw).strip()

        payload = {
            "klucz": klucz,
            "cena_netto_ksiegowosc": parse_num(get_cell(row, "cena netto 2024")) or 0.0,
            "cena_za_dokument_ponad_limit": parse_num(get_cell(row, "za przekroczenie limitu netto")) or 0.0,
            "zdrowotna": parse_num(get_cell(row, "zdrowotna")) or 0.0,
            "netto": parse_num(get_cell(row, "netto")) or 0.0,
            "brutto": parse_num(get_cell(row, "brutto")) or 0.0,
        }

        payloads.append(payload)

    return payloads


# ============================================================
# IMPORT CEN ZA DOKUMENT — ZWRACA PAYLOADY
# ============================================================
def import_ceny_za_dokument_from_excel(filepath: str, db_path: str = DB_NAME) -> List[Dict[str, Any]]:
    ext = os.path.splitext(filepath)[1].lower()

    try:
        if ext == ".csv":
            with open(filepath, newline="", encoding="utf-8") as f:
                sample = f.read(4096)
                f.seek(0)
                delimiter = "," if sample.count(",") >= sample.count(";") else ";"
                reader = csv.reader(f, delimiter=delimiter)
                header = next(reader, None)
                rows = [row for row in reader]
        else:
            wb = openpyxl.load_workbook(filepath, data_only=True)
            ws = wb.active
            header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
            rows = [tuple(cell for cell in row) for row in ws.iter_rows(min_row=2, values_only=True)]
    except Exception:
        return []

    if not header:
        return []

    headers = [str(h).strip().lower() if h is not None else "" for h in header]
    idx = {h: i for i, h in enumerate(headers)}

    if "klucz" not in idx or "cena" not in idx:
        return []

    payloads = []

    for row in rows:
        if not any(c not in (None, "") for c in row):
            continue

        klucz = str(row[idx["klucz"]]).strip()
        cena = parse_num(row[idx["cena"]])

        if not klucz:
            continue

        typ = klucz
        vat = 0

        if "_" in klucz:
            parts = klucz.split("_")
            typ = parts[0]
            try:
                vat = int(parts[-1]) if parts[-1].isdigit() else 0
            except Exception:
                vat = 0

        payloads.append({
            "typ": typ,
            "vat": vat,
            "cena": cena,
        })

    return payloads
