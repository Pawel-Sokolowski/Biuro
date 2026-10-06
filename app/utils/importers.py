import csv
import openpyxl
import sqlite3
import re
import os
import time
from typing import Dict, Any, List, Optional, Tuple

from app.utils.parsing import normalize_identifier, resolve_employee_ids
from app.config import DB_NAME


# ============================================================
# PARSERY POMOCNICZE
# ============================================================
def parse_cennik_text(text: str) -> Dict[str, Any]:
    if not text:
        return {}

    t = str(text).lower()
    out: Dict[str, Any] = {}

    m = re.search(r"limit\s+(\d+)", t)
    if m:
        try:
            out["limit_dokumentow"] = int(m.group(1))
        except Exception:
            pass

    m = re.search(r"(\d+[,.]\d+)\s*zł", t)
    if m:
        try:
            out["cena_za_dokument"] = float(m.group(1).replace(",", "."))
        except Exception:
            pass

    m = re.search(r"ryczałt\s+(\d+[,.]\d+)", t)
    if m:
        try:
            out["ryczalt"] = float(m.group(1).replace(",", "."))
        except Exception:
            pass

    m = re.search(r"zdrowotna\s+(\d+[,.]\d+)", t)
    if m:
        try:
            out["zdrowotna"] = float(m.group(1).replace(",", "."))
        except Exception:
            pass

    return out


def split_address_line(line: str) -> Tuple[str, str, str]:
    """
    Rozbija adres typu 'ul. Królewiecka 21 / 10' na:
    (ulica, budynek, lokal)
    """
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
# WSPÓLNY IMPORTER FIRM (LOGIKA)
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


def _build_payload_from_row(
    headers: List[str],
    header_index: Dict[str, int],
    row: Any,
) -> Dict[str, Any]:
    mapping = {
        "NIP": "nip",
        "REGON": "regon",
        "KRS": "krs",
        "PESEL": "pesel",
        "Nazwa Firmy": "nazwa",
        "Nazwa skrócona": "nazwa_skrocona",
        "Forma działalności": "forma_dzialalnosci",
        "Forma opodatkowania": "forma_opodatkowania",
        "Analityka FK": "analityka_fk",
        "Osoba kontaktowa": "osoba_kontaktowa",
        "Adres email": "adres_email",
        "Nr telefonu": "nr_telefonu",
        "Miasto": "miasto",
        "Kod pocztowy": "kod_pocztowy",
        "Ulica": "ulica",
        "Adres zamieszkania - miasto": "adres_zam_miasto",
        "Adres zamieszkania - kod pocztowy": "adres_zam_kod_pocztowy",
        "Adres zamieszkania - ulica": "adres_zam_ulica",
        "Adres korespondencyjny - miasto": "adres_koresp_miasto",
        "Adres korespondencyjny - kod pocztowy": "adres_koresp_kod_pocztowy",
        "Adres korespondencyjny - ulica": "adres_koresp_ulica",
        "Przypisani pracownicy": "assigned_raw",
        "Data rozpoczęcia działalności gospodarczej": "data_rozpoczecia_dg",
        "Data rozpoczęcia sprzedaży dla osoby fizycznej": "data_rozpoczecia_sprzedazy",
        "Data zakończenia ulgi ZUS na start": "data_koniec_zus_start",
        "Data zakończenia ZUS preferencyjny": "data_koniec_zus_pref",
        "Data zakończenia małego ZUS PLUS": "data_koniec_maly_zus",
        "Data zakończenia A1": "data_koniec_a1",
        "Data rozpoczęcia obsługi": "data_rozpoczecia_obslugi",
        "Data zakończenia obsługi": "data_zakonczenia_obslugi",
        "Zawieszenia działalności": "zawieszenia_dzialalnosci",
        "Kody PKD": "kody_pkd",
        "Cenniki usług księgowych": "cennik_ksiegowy",
        "Cenniki usług kadrowych": "cennik_kadrowy",
        "Cenniki czasowe": "cennik_czasowy",
    }

    payload: Dict[str, Any] = {}

    for src, dst in mapping.items():
        idx = header_index.get(src)
        payload[dst] = row[idx] if idx is not None else None

    # adres główny
    ulica_raw = payload.get("ulica")
    ulica, budynek, lokal = split_address_line(ulica_raw)
    payload["ulica"] = ulica
    payload["adres_budynek"] = budynek
    payload["adres_lokal"] = lokal

    # adres zamieszkania
    ulica_zam_raw = payload.get("adres_zam_ulica")
    ulica_zam, budynek_zam, lokal_zam = split_address_line(ulica_zam_raw)
    payload["adres_zam_ulica"] = ulica_zam
    payload["adres_zam_budynek"] = budynek_zam
    payload["adres_zam_lokal"] = lokal_zam

    # adres korespondencyjny
    ulica_koresp_raw = payload.get("adres_koresp_ulica")
    ulica_koresp, budynek_koresp, lokal_koresp = split_address_line(ulica_koresp_raw)
    payload["adres_koresp_ulica"] = ulica_koresp
    payload["adres_koresp_budynek"] = budynek_koresp
    payload["adres_koresp_lokal"] = lokal_koresp

    # cenniki
    cennik_ksiegowy = parse_cennik_text(str(payload.get("cennik_ksiegowy", "") or ""))
    cennik_czasowy = parse_cennik_text(str(payload.get("cennik_czasowy", "") or ""))

    payload.update(
        {
            "limit_dokumentow": cennik_ksiegowy.get(
                "limit_dokumentow", payload.get("limit_dokumentow")
            ),
            "cena_za_dokument": cennik_ksiegowy.get("cena_za_dokument"),
            "ryczalt_ksiegowy": cennik_ksiegowy.get("ryczalt"),
            "ryczalt_czasowy": cennik_czasowy.get("ryczalt"),
            "zdrowotna": cennik_ksiegowy.get("zdrowotna"),
        }
    )

    return payload


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

    # tylko pola obecne w Excelu (opcja C)
    fields_update = []
    values_update = []

    def add_field_if_present(excel_key: str, db_field: str):
        if payload.get(excel_key) is not None:
            fields_update.append(f"{db_field} = ?")
            values_update.append(payload.get(excel_key))

    # podstawowe
    add_field_if_present("nazwa", "nazwa")
    add_field_if_present("nazwa_skrocona", "nazwa_skrocona")
    add_field_if_present("nip", "nip")
    add_field_if_present("regon", "regon")
    add_field_if_present("krs", "krs")
    add_field_if_present("pesel", "pesel")
    add_field_if_present("forma_dzialalnosci", "forma_dzialalnosci")
    add_field_if_present("forma_opodatkowania", "forma_opodatkowania")
    add_field_if_present("analityka_fk", "analityka_fk")
    add_field_if_present("osoba_kontaktowa", "osoba_kontaktowa")
    add_field_if_present("adres_email", "adres_email")
    add_field_if_present("nr_telefonu", "nr_telefonu")

    # adres główny
    add_field_if_present("miasto", "miasto")
    add_field_if_present("kod_pocztowy", "kod_pocztowy")
    add_field_if_present("ulica", "ulica")
    add_field_if_present("adres_budynek", "adres_budynek")
    add_field_if_present("adres_lokal", "adres_lokal")

    # adres zamieszkania
    add_field_if_present("adres_zam_miasto", "adres_zam_miasto")
    add_field_if_present("adres_zam_kod_pocztowy", "adres_zam_kod_pocztowy")
    add_field_if_present("adres_zam_ulica", "adres_zam_ulica")
    add_field_if_present("adres_zam_budynek", "adres_zam_budynek")
    add_field_if_present("adres_zam_lokal", "adres_zam_lokal")

    # adres korespondencyjny
    add_field_if_present("adres_koresp_miasto", "adres_koresp_miasto")
    add_field_if_present("adres_koresp_kod_pocztowy", "adres_koresp_kod_pocztowy")
    add_field_if_present("adres_koresp_ulica", "adres_koresp_ulica")
    add_field_if_present("adres_koresp_budynek", "adres_koresp_budynek")
    add_field_if_present("adres_koresp_lokal", "adres_koresp_lokal")

    # daty, pkd, zawieszenia
    add_field_if_present("data_rozpoczecia_dg", "data_rozpoczecia_dg")
    add_field_if_present("data_rozpoczecia_sprzedazy", "data_rozpoczecia_sprzedazy")
    add_field_if_present("data_koniec_zus_start", "data_koniec_zus_start")
    add_field_if_present("data_koniec_zus_pref", "data_koniec_zus_pref")
    add_field_if_present("data_koniec_maly_zus", "data_koniec_maly_zus")
    add_field_if_present("data_koniec_a1", "data_koniec_a1")
    add_field_if_present("data_rozpoczecia_obslugi", "data_rozpoczecia_obslugi")
    add_field_if_present("data_zakonczenia_obslugi", "data_zakonczenia_obslugi")
    add_field_if_present("zawieszenia_dzialalnosci", "zawieszenia_dzialalnosci")
    add_field_if_present("kody_pkd", "kody_pkd")

    # cenniki
    add_field_if_present("cennik_ksiegowy", "cennik_ksiegowy")
    add_field_if_present("cennik_kadrowy", "cennik_kadrowy")
    add_field_if_present("cennik_czasowy", "cennik_czasowy")
    add_field_if_present("limit_dokumentow", "limit_dokumentow")
    add_field_if_present("zdrowotna", "zdrowotna")

    # typ rozliczenia, VAT
    add_field_if_present("typ_rozliczenia", "typ_rozliczenia")
    if payload.get("vat_czynny") is not None:
        val_vat = 1 if str(payload.get("vat_czynny")).strip().lower() in (
            "tak",
            "true",
            "1",
        ) else 0
        fields_update.append("vat_czynny = ?")
        values_update.append(val_vat)

    if company_id:
        if fields_update:
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
                payload.get("typ_rozliczenia") or "Ryczałt",
                1
                if str(payload.get("vat_czynny", "")).strip().lower()
                in ("tak", "true", "1")
                else 0,
            ),
        )


# ============================================================
# KLASYCZNY IMPORTER (GUI go importuje)
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
                # heurystyka: jeśli był UPDATE/INSERT
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
# SZYBKI IMPORTER (używany tam, gdzie chcesz wydajność)
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
            time.sleep(0)

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
# IMPORT CENNIKA KSIĘGOWEGO
# ============================================================
def import_cennik_from_excel(filepath: str, db_path: str = DB_NAME) -> Dict[str, Any]:
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
    except Exception as e:
        return {"imported": 0, "updated": 0, "errors": [f"Błąd wczytywania pliku: {e}"]}

    if not header:
        return {"imported": 0, "updated": 0, "errors": ["Brak nagłówków w pliku."]}

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
            return {
                "imported": 0,
                "updated": 0,
                "errors": [f"Brakuje kolumny '{col}'."],
            }

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    imported = 0
    updated = 0
    errors: List[str] = []

    def get_cell(row, key):
        i = idx.get(key)
        if i is None:
            return None
        try:
            return row[i]
        except Exception:
            return None

    for rownum, row in enumerate(rows, start=2):
        try:
            klucz_raw = get_cell(row, "klucz")
            if klucz_raw is None or str(klucz_raw).strip() == "":
                continue

            klucz = str(klucz_raw).strip()

            cena_netto_ksiegowosc = parse_num(get_cell(row, "cena netto 2024"))
            cena_za_dokument_ponad_limit = parse_num(
                get_cell(row, "za przekroczenie limitu netto")
            )
            zdrowotna = parse_num(get_cell(row, "zdrowotna"))
            netto = parse_num(get_cell(row, "netto"))
            brutto = parse_num(get_cell(row, "brutto"))

            if cena_netto_ksiegowosc is None:
                cena_netto_ksiegowosc = 0.0
            if cena_za_dokument_ponad_limit is None:
                cena_za_dokument_ponad_limit = 0.0
            if zdrowotna is None:
                zdrowotna = 0.0
            if netto is None:
                netto = 0.0
            if brutto is None:
                brutto = 0.0

            cena_brutto_ksiegowosc = brutto

            now = time.strftime("%Y-%m-%d %H:%M:%S")

            cur.execute("SELECT id FROM cenniki WHERE klucz = ?", (klucz,))
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    """
                    UPDATE cenniki SET
                        cena_netto_ksiegowosc = ?,
                        cena_brutto_ksiegowosc = ?,
                        cena_za_dokument_ponad_limit = ?,
                        zdrowotna = ?,
                        netto = ?,
                        brutto = ?,
                        updated_at = ?
                    WHERE klucz = ?
                """,
                    (
                        cena_netto_ksiegowosc,
                        cena_brutto_ksiegowosc,
                        cena_za_dokument_ponad_limit,
                        zdrowotna,
                        netto,
                        brutto,
                        now,
                        klucz,
                    ),
                )
                updated += 1
            else:
                cur.execute(
                    """
                    INSERT INTO cenniki (
                        klucz, cena_netto_ksiegowosc, cena_brutto_ksiegowosc,
                        cena_za_dokument_ponad_limit, zdrowotna, netto, brutto, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        klucz,
                        cena_netto_ksiegowosc,
                        cena_brutto_ksiegowosc,
                        cena_za_dokument_ponad_limit,
                        zdrowotna,
                        netto,
                        brutto,
                        now,
                    ),
                )
                imported += 1

        except Exception as e:
            errors.append(f"Wiersz {rownum}: {e}")

    conn.commit()
    conn.close()

    return {"imported": imported, "updated": updated, "errors": errors}


# ============================================================
# IMPORT CEN ZA DOKUMENT
# ============================================================
def import_ceny_za_dokument_from_excel(
    filepath: str, db_path: str = DB_NAME
) -> Dict[str, Any]:
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
    except Exception as e:
        return {"imported": 0, "errors": [f"Błąd wczytywania pliku: {e}"]}

    if not header:
        return {"imported": 0, "errors": ["Brak nagłówków w pliku."]}

    headers = [str(h).strip().lower() if h is not None else "" for h in header]
    idx = {h: i for i, h in enumerate(headers)}

    if "klucz" not in idx or "cena" not in idx:
        return {"imported": 0, "errors": ["Brakuje kolumn 'Klucz' lub 'Cena'."]}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    imported = 0
    errors: List[str] = []

    for rownum, row in enumerate(rows, start=2):
        if not any(c not in (None, "") for c in row):
            continue

        try:
            klucz = str(row[idx["klucz"]]).strip()
            cena = parse_num(row[idx["cena"]])

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS globalne_ceny_za_dokument (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    typ TEXT,
                    vat INTEGER,
                    cena REAL
                )
            """
            )

            typ = klucz
            vat = 0
            if "_" in klucz:
                parts = klucz.split("_")
                typ = parts[0]
                try:
                    vat = int(parts[-1]) if parts[-1].isdigit() else 0
                except Exception:
                    vat = 0

            cur.execute(
                """
                INSERT OR REPLACE INTO globalne_ceny_za_dokument (typ, vat, cena)
                VALUES (?, ?, ?)
            """,
                (typ, vat, cena),
            )
            imported += 1

        except Exception as e:
            errors.append(f"Wiersz {rownum}: {str(e)}")

    conn.commit()
    conn.close()

    return {"imported": imported, "errors": errors}
