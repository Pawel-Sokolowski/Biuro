import csv

import openpyxl
import sqlite3
import time
import re
from typing import Dict, Any
import os
from app.utils.parsing import normalize_identifier, resolve_employee_ids
from app.companies.helpers import _get_cell, parse_num
from app.config import DB_NAME

#app/utils/importers.py
# ============================================================
#  PARSER CENNIKÓW TEKSTOWYCH (dla opisów w plikach firm)
# ============================================================

def parse_cennik_text(text: str) -> Dict[str, Any]:
    """
    Parsuje tekstowy cennik z Excela, np:
    'Cena ponad limit 20 szt. 5,5 zł, Ryczałt 412,50 netto'
    'Ryczałt 362,00 netto'
    'Zdrowotna 70,00, Usługa - UoP 100,00, Usługa - UZ 80,00'
    """

    if not text:
        return {}

    out = {}
    t = text.lower()

    # Limit dokumentów
    m = re.search(r"limit\s+(\d+)", t)
    if m:
        out["limit_dokumentow"] = int(m.group(1))

    # Cena za dokument
    m = re.search(r"(\d+[,.]\d+)\s*zł", t)
    if m:
        out["cena_za_dokument"] = float(m.group(1).replace(",", "."))

    # Ryczałt
    m = re.search(r"ryczałt\s+(\d+[,.]\d+)", t)
    if m:
        out["ryczalt"] = float(m.group(1).replace(",", "."))

    # Zdrowotna
    m = re.search(r"zdrowotna\s+(\d+[,.]\d+)", t)
    if m:
        out["zdrowotna"] = float(m.group(1).replace(",", "."))

    # Usługa UoP
    m = re.search(r"uop\s+(\d+[,.]\d+)", t)
    if m:
        out["usluga_uop"] = float(m.group(1).replace(",", "."))

    # Usługa UZ
    m = re.search(r"uz\s+(\d+[,.]\d+)", t)
    if m:
        out["usluga_uz"] = float(m.group(1).replace(",", "."))

    return out


# ============================================================
#  IMPORT FIRM
# ============================================================

def import_companies_from_excel_file(filepath: str, db_path: str) -> Dict[str, Any]:
    workbook = openpyxl.load_workbook(filepath, data_only=True)
    worksheet = workbook.active
    header_cells = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), None)

    if not header_cells:
        return {"created": 0, "updated": 0, "warnings": ["Plik nie zawiera nagłówków."]}

    headers = [str(h).strip() if h is not None else "" for h in header_cells]
    header_index = {header: idx for idx, header in enumerate(headers)}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    created_count = 0
    updated_count = 0
    warnings = []

    try:
        for row_number, row_values in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):

            if not any(v not in (None, "") for v in row_values):
                continue

            payload = {
                "uop": 0,
                "uz": 0,
                "uod": 0,
                "glowny_cennik_manualny": 0
            }

            mapping = {
                "NIP": "nip", "REGON": "regon", "KRS": "krs", "PESEL": "pesel",
                "Nazwa Firmy": "nazwa", "Nazwa skrócona": "nazwa_skrocona",
                "Forma działalności": "forma_dzialalnosci", "Forma opodatkowania": "forma_opodatkowania",
                "Analityka FK": "analityka_fk", "Osoba kontaktowa": "osoba_kontaktowa",
                "Adres email": "adres_email", "Nr telefonu": "nr_telefonu",
                "Miasto": "miasto", "Kod pocztowy": "kod_pocztowy", "Ulica": "ulica",
                "Adres zamieszkania - miasto": "adres_zam_miasto",
                "Adres zamieszkania - kod pocztowy": "adres_zam_kod_pocztowy",
                "Adres zamieszkania - ulica": "adres_zam_ulica",
                "Adres korespondencyjny - miasto": "adres_koresp_miasto",
                "Adres korespondencyjny - kod pocztowy": "adres_koresp_kod_pocztowy",
                "Adres korespondencyjny - ulica": "adres_koresp_ulica",
                "Cenniki usług księgowych": "cennik_ksiegowy",
                "Cenniki usług kadrowych": "cennik_kadrowy",
                "Cenniki czasowe": "cennik_czasowy",
                "Limit dokumentów": "limit_dokumentow",
                "Typ rozliczenia": "typ_rozliczenia",
                "VAT czynny": "vat_czynny",
            }

            for src, dst in mapping.items():
                if src in header_index:
                    payload[dst] = row_values[header_index[src]]

            assigned_raw = row_values[header_index["Przypisani pracownicy"]] if "Przypisani pracownicy" in header_index else None
            payload["assigned_employee_ids"], unresolved = resolve_employee_ids(cursor, assigned_raw)

            if unresolved:
                warnings.append(f"Wiersz {row_number}: nie znaleziono pracowników {', '.join(unresolved)}.")

            company_name = str(payload.get("nazwa", "")).strip()
            if not company_name:
                warnings.append(f"Wiersz {row_number}: pominięto pustą nazwę firmy.")
                continue

            cennik_ksiegowy = parse_cennik_text(str(payload.get("cennik_ksiegowy", "")))
            cennik_kadrowy = parse_cennik_text(str(payload.get("cennik_kadrowy", "")))
            cennik_czasowy = parse_cennik_text(str(payload.get("cennik_czasowy", "")))

            payload.update({
                "limit_dokumentow": cennik_ksiegowy.get("limit_dokumentow", payload.get("limit_dokumentow")),
                "cena_za_dokument": cennik_ksiegowy.get("cena_za_dokument"),
                "ryczalt_ksiegowy": cennik_ksiegowy.get("ryczalt"),

                "ryczalt_kadrowy": cennik_kadrowy.get("ryczalt"),
                "zdrowotna": cennik_kadrowy.get("zdrowotna"),
                "usluga_uop": cennik_kadrowy.get("usluga_uop"),
                "usluga_uz": cennik_kadrowy.get("usluga_uz"),

                "ryczalt_czasowy": cennik_czasowy.get("ryczalt")
            })

            normalized_nip = normalize_identifier(payload.get("nip"), {10})
            company_id = None

            if normalized_nip:
                cursor.execute("SELECT id FROM firmy WHERE nip = ?", (normalized_nip,))
                row = cursor.fetchone()
                company_id = row[0] if row else None

            if company_id:
                cursor.execute("""
                    UPDATE firmy SET
                        nazwa = ?, glowny_cennik = ?, limit_dokumentow = ?, typ_rozliczenia = ?, vat_czynny = ?
                    WHERE id = ?
                """, (
                    company_name,
                    payload.get("cennik_ksiegowy", ""),
                    payload.get("limit_dokumentow"),
                    payload.get("typ_rozliczenia"),
                    1 if str(payload.get("vat_czynny", "")).strip().lower() in ("tak", "true", "1") else 0,
                    company_id
                ))
                updated_count += 1

            else:
                cursor.execute("""
                    INSERT INTO firmy (nazwa, nip, glowny_cennik, limit_dokumentow, typ_rozliczenia, vat_czynny)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    company_name,
                    normalized_nip,
                    payload.get("cennik_ksiegowy", ""),
                    payload.get("limit_dokumentow"),
                    payload.get("typ_rozliczenia"),
                    1 if str(payload.get("vat_czynny", "")).strip().lower() in ("tak", "true", "1") else 0,
                ))
                created_count += 1

        conn.commit()
        return {"created": created_count, "updated": updated_count, "warnings": warnings}

    finally:
        conn.close()

def parse_num(val):
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "."))
    except:
        return None

def import_cennik_from_excel(filepath: str, db_path: str = DB_NAME) -> Dict[str, Any]:
    """
    Import cennika z pliku Excel (.xlsx/.xls) lub CSV (.csv).
    Zwraca dict: {"imported": int, "updated": int, "errors": [str, ...]}
    """
    ext = os.path.splitext(filepath)[1].lower()

    # Wczytanie nagłówka i wierszy w zależności od formatu
    try:
        if ext == ".csv":
            # autodetekcja separatora: najczęściej przecinek lub średnik; próbujemy wykryć
            with open(filepath, newline='', encoding='utf-8') as f:
                sample = f.read(4096)
                f.seek(0)
                # prosty detektor separatora
                delimiter = ',' if sample.count(',') >= sample.count(';') else ';'
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
        "cena za dokument",
        "za przekroczenie limitu netto",
        "zdrowotna",
        "netto",
        "brutto"
    ]

    for col in required:
        if col not in idx:
            return {"imported": 0, "updated": 0, "errors": [f"Brakuje kolumny '{col}'."]}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    imported = 0
    updated = 0
    errors = []

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

            # Parsowanie wartości numerycznych bezpiecznie
            cena_netto_ksiegowosc = parse_num(get_cell(row, "cena netto 2024"))
            cena_za_dokument_ponad_limit = parse_num(get_cell(row, "za przekroczenie limitu netto"))
            zdrowotna = parse_num(get_cell(row, "zdrowotna"))
            netto = parse_num(get_cell(row, "netto"))
            brutto = parse_num(get_cell(row, "brutto"))

            # Ustaw wartości domyślne dla pól NOT NULL w schemacie
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

            cena_brutto_ksiegowosc = brutto  # zgodnie z tabelą

            now = time.strftime("%Y-%m-%d %H:%M:%S")

            cur.execute("SELECT id FROM cenniki WHERE klucz = ?", (klucz,))
            existing = cur.fetchone()

            if existing:
                cur.execute("""
                    UPDATE cenniki SET
                        cena_netto_ksiegowosc = ?,
                        cena_brutto_ksiegowosc = ?,
                        cena_za_dokument_ponad_limit = ?,
                        zdrowotna = ?,
                        netto = ?,
                        brutto = ?,
                        updated_at = ?
                    WHERE klucz = ?
                """, (
                    cena_netto_ksiegowosc,
                    cena_brutto_ksiegowosc,
                    cena_za_dokument_ponad_limit,
                    zdrowotna,
                    netto,
                    brutto,
                    now,
                    klucz
                ))
                updated += 1
            else:
                cur.execute("""
                    INSERT INTO cenniki (
                        klucz, cena_netto_ksiegowosc, cena_brutto_ksiegowosc,
                        cena_za_dokument_ponad_limit, zdrowotna, netto, brutto, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    klucz,
                    cena_netto_ksiegowosc,
                    cena_brutto_ksiegowosc,
                    cena_za_dokument_ponad_limit,
                    zdrowotna,
                    netto,
                    brutto,
                    now
                ))
                imported += 1

        except Exception as e:
            errors.append(f"Wiersz {rownum}: {e}")

    conn.commit()
    conn.close()

    return {"imported": imported, "updated": updated, "errors": errors}

def import_ceny_za_dokument_from_excel(filepath: str, db_path: str) -> Dict[str, Any]:
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active

    header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if not header:
        return {"imported": 0, "errors": ["Brak nagłówków w pliku."]}

    headers = [str(h).strip().lower() if h else "" for h in header]
    idx = {h: i for i, h in enumerate(headers)}

    if "klucz" not in idx or "cena" not in idx:
        return {"imported": 0, "errors": ["Brakuje kolumn 'Klucz' lub 'Cena'."]}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    imported = 0
    errors = []

    for rownum, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(c not in (None, "") for c in row):
            continue

        try:
            klucz = str(row[idx["klucz"]]).strip()
            cena = parse_num(row[idx["cena"]])

            typ, vat_suffix = klucz.split("_")
            vat = 0 if vat_suffix == "N" else 1

            cur.execute("""
                INSERT OR REPLACE INTO globalne_ceny_za_dokument (typ, vat, cena)
                VALUES (?, ?, ?)
            """, (typ, vat, cena))
            imported += 1

        except Exception as e:
            errors.append(f"Wiersz {rownum}: {str(e)}")

    conn.commit()
    conn.close()

    return {"imported": imported, "errors": errors}
