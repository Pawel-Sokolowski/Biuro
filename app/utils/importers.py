import csv
import openpyxl
import sqlite3
import re
import os
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()
DB_NAME = os.getenv("DB_NAME")

from app.utils.parsing import normalize_identifier, resolve_employee_ids


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
    payload["nip"] = get("NIP")
    payload["regon"] = get("REGON")
    payload["krs"] = get("KRS")
    payload["pesel"] = get("PESEL")
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
    payload["adres_koresp_miasto"] = get("Adres korespondpondencyjny - miasto")
    payload["adres_koresp_kod_pocztowy"] = get("Adres korespondencyjny - kod pocztowy")
    ulica_koresp_raw = get("Adres korespondencyjny - ulica")
    ulica_koresp, budynek_koresp, lokal_koresp = split_address_line(ulica_koresp_raw)
    payload["adres_koresp_ulica"] = ulica_koresp
    payload["adres_koresp_budynek"] = budynek_koresp
    payload["adres_koresp_lokal"] = lokal_koresp

    # daty, pkd, zawieszenia
    payload["data_rozpoczecia_dg"] = get("Data rozpoczęcia działalności gospodarczej")
    payload["data_rozpoczecia_sprzedazy"] = get("Data rozpoczęcia sprzedaży dla osoby fizycznej")
    payload["data_koniec_zus_start"] = get("Data zakończenia ulgi ZUS na start")
    payload["data_koniec_zus_pref"] = get("Data zakończenia ZUS preferencyjny")
    payload["data_koniec_maly_zus"] = get("Data zakończenia małego ZUS PLUS")
    payload["data_koniec_a1"] = get("Data zakończenia A1")
    payload["data_rozpoczecia_obslugi"] = get("Data rozpoczęcia obsługi")
    payload["data_zakonczenia_obslugi"] = get("Data zakończenia obsługi")
    payload["zawieszenia_dzialalnosci"] = get("Zawieszenia działalności")
    payload["kody_pkd"] = get("Kody PKD")

    # cenniki księgowe
    cennik_ksiegowy_text = str(get("Cenniki usług księgowych") or "")
    cks = parse_cennik_ksiegowy(cennik_ksiegowy_text)
    payload["cennik_ksiegowy"] = cennik_ksiegowy_text
    payload["limit_dokumentow"] = cks["limit_dokumentow"]
    payload["zdrowotna"] = cks["zdrowotna"]

    # cenniki kadrowe
    cennik_kadrowy_text = str(get("Cenniki usług kadrowych") or "")
    ckad = parse_cennik_kadrowy(cennik_kadrowy_text)
    payload["cennik_kadrowy"] = cennik_kadrowy_text
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
def _apply_payload_to_db(
    cursor: sqlite3.Cursor,
    payload: Dict[str, Any],
    row_number: int,
    warnings: List[str],
) -> None:

    company_name = str(payload.get("nazwa", "") or "").strip()
    if not company_name:
        warnings.append(f"Wiersz {row_number}: pominięto pustą nazwę firmy.")
        return

    normalized_nip = normalize_identifier(payload.get("nip"), {10})
    company_id = None

    if normalized_nip:
        cursor.execute("SELECT id FROM firmy WHERE nip = ?", (normalized_nip,))
        row_db = cursor.fetchone()
        company_id = row_db[0] if row_db else None

    fields_update = []
    values_update = []

    def add_field(db_field: str, excel_key: str):
        fields_update.append(f"{db_field} = ?")
        values_update.append(payload.get(excel_key))

    # podstawowe
    add_field("nazwa", "nazwa")
    add_field("nazwa_skrocona", "nazwa_skrocona")
    add_field("nip", "nip")
    add_field("regon", "regon")
    add_field("krs", "krs")
    add_field("pesel", "pesel")
    add_field("forma_dzialalnosci", "forma_dzialalnosci")
    add_field("forma_opodatkowania", "forma_opodatkowania")
    add_field("analityka_fk", "analityka_fk")
    add_field("osoba_kontaktowa", "osoba_kontaktowa")
    add_field("adres_email", "adres_email")
    add_field("nr_telefonu", "nr_telefonu")

    # adres główny
    add_field("miasto", "miasto")
    add_field("kod_pocztowy", "kod_pocztowy")
    add_field("ulica", "ulica")
    add_field("adres_budynek", "adres_budynek")
    add_field("adres_lokal", "adres_lokal")

    # adres zamieszkania
    add_field("adres_zam_miasto", "adres_zam_miasto")
    add_field("adres_zam_kod_pocztowy", "adres_zam_kod_pocztowy")
    add_field("adres_zam_ulica", "adres_zam_ulica")
    add_field("adres_zam_budynek", "adres_zam_budynek")
    add_field("adres_zam_lokal", "adres_zam_lokal")

    # adres korespondencyjny
    add_field("adres_koresp_miasto", "adres_koresp_miasto")
    add_field("adres_koresp_kod_pocztowy", "adres_koresp_kod_pocztowy")
    add_field("adres_koresp_ulica", "adres_koresp_ulica")
    add_field("adres_koresp_budynek", "adres_koresp_budynek")
    add_field("adres_koresp_lokal", "adres_koresp_lokal")

    # daty, pkd, zawieszenia
    add_field("data_rozpoczecia_dg", "data_rozpoczecia_dg")
    add_field("data_rozpoczecia_sprzedazy", "data_rozpoczecia_sprzedazy")
    add_field("data_koniec_zus_start", "data_koniec_zus_start")
    add_field("data_koniec_zus_pref", "data_koniec_zus_pref")
    add_field("data_koniec_maly_zus", "data_koniec_maly_zus")
    add_field("data_koniec_a1", "data_koniec_a1")
    add_field("data_rozpoczecia_obslugi", "data_rozpoczecia_obslugi")
    add_field("data_zakonczenia_obslugi", "data_zakonczenia_obslugi")
    add_field("zawieszenia_dzialalnosci", "zawieszenia_dzialalnosci")
    add_field("kody_pkd", "kody_pkd")

    if company_id:
        sql = f"UPDATE firmy SET {', '.join(fields_update)} WHERE id = ?"
        values_update.append(company_id)
        cursor.execute(sql, tuple(values_update))
    else:
        cursor.execute(
            """
            INSERT INTO firmy (nazwa, nip, glowny_cennik, limit_dokumentow, typ_rozliczenia, vat_czynny)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                company_name,
                normalized_nip,
                payload.get("cennik_ksiegowy", ""),
                payload.get("limit_dokumentow") or 0,
                payload.get("forma_opodatkowania") or "Ryczałt",
                0,
            ),
        )


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

            assigned_raw = payload.get("assigned_raw")
            if assigned_raw:
                try:
                    assigned_ids, unresolved = resolve_employee_ids(cursor, assigned_raw)
                    if unresolved:
                        warnings.append(
                            f"Wiersz {row_number}: nie znaleziono pracowników {', '.join(unresolved)}."
                        )
                except Exception:
                    pass

            before = conn.total_changes
            _apply_payload_to_db(cursor, payload, row_number, warnings)
            after = conn.total_changes

            if after > before:
                if before == 0:
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

            assigned_raw = payload.get("assigned_raw")
            if assigned_raw:
                try:
                    assigned_ids, unresolved = resolve_employee_ids(cursor, assigned_raw)
                    if unresolved:
                        warnings.append(
                            f"Wiersz {row_number}: nie znaleziono pracowników {', '.join(unresolved)}."
                        )
                except Exception:
                    pass

            before = conn.total_changes
            _apply_payload_to_db(cursor, payload, row_number, warnings)
            after = conn.total_changes

            if after > before:
                if before == 0:
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
