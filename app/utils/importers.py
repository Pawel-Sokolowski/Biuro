# app/utils/importers.py
import csv
import openpyxl
import sqlite3
import time
import re
from typing import Dict, Any, List, Optional
import os

from app.utils.parsing import normalize_identifier, resolve_employee_ids
from app.companies.helpers import _get_cell  # jeśli masz helpery, inaczej usuń
from app.config import DB_NAME


# ============================================================
# PARSER CENNIKÓW TEKSTOWYCH (tylko dla cenników księgowych)
# ============================================================
def parse_cennik_text(text: str) -> Dict[str, Any]:
    """
    Parsuje tekstowy cennik z Excela/komentarza firmy.
    Obsługuje tylko pola związane z księgowością:
      - limit dokumentów
      - cena za dokument
      - ryczałt (księgowy)
      - zdrowotna (księgowa)
    Zwraca słownik z możliwymi kluczami:
      {"limit_dokumentow": int, "cena_za_dokument": float, "ryczalt": float, "zdrowotna": float}
    """
    if not text:
        return {}

    out: Dict[str, Any] = {}
    t = str(text).lower()

    # Limit dokumentów
    m = re.search(r"limit\s+(\d+)", t)
    if m:
        try:
            out["limit_dokumentow"] = int(m.group(1))
        except Exception:
            pass

    # Cena za dokument (pierwsze wystąpienie liczby z "zł")
    m = re.search(r"(\d+[,.]\d+)\s*zł", t)
    if m:
        try:
            out["cena_za_dokument"] = float(m.group(1).replace(",", "."))
        except Exception:
            pass

    # Ryczałt (księgowy)
    m = re.search(r"ryczałt\s+(\d+[,.]\d+)", t)
    if m:
        try:
            out["ryczalt"] = float(m.group(1).replace(",", "."))
        except Exception:
            pass

    # Zdrowotna (księgowa)
    m = re.search(r"zdrowotna\s+(\d+[,.]\d+)", t)
    if m:
        try:
            out["zdrowotna"] = float(m.group(1).replace(",", "."))
        except Exception:
            pass

    return out


# ============================================================
# IMPORT FIRM Z EXCEL/CSV
# ============================================================
def import_companies_from_excel_file(filepath: str, db_path: str = DB_NAME) -> Dict[str, Any]:
    """
    Import firm z pliku Excel (.xlsx/.xls) lub CSV (.csv).
    Odczytuje kolumny zgodne z mapowaniem w kodzie.
    Zwraca dict: {"created": int, "updated": int, "warnings": [str, ...]}
    """
    try:
        workbook = openpyxl.load_workbook(filepath, data_only=True)
        worksheet = workbook.active
        header_cells = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
    except Exception:
        # Spróbuj CSV
        header_cells = None

    if not header_cells:
        # Spróbkuj CSV
        try:
            with open(filepath, newline='', encoding='utf-8') as f:
                sample = f.read(4096)
                f.seek(0)
                delimiter = ',' if sample.count(',') >= sample.count(';') else ';'
                reader = csv.reader(f, delimiter=delimiter)
                header = next(reader, None)
                header_cells = header
                rows_iter = list(reader)
        except Exception as e:
            return {"created": 0, "updated": 0, "warnings": [f"Plik nie mógł zostać wczytany: {e}"]}

        headers = [str(h).strip() if h is not None else "" for h in header_cells]
        header_index = {header: idx for idx, header in enumerate(headers)}
        rows = rows_iter
        is_csv = True
    else:
        headers = [str(h).strip() if h is not None else "" for h in header_cells]
        header_index = {header: idx for idx, header in enumerate(headers)}
        rows = [tuple(cell for cell in row) for row in worksheet.iter_rows(min_row=2, values_only=True)]
        is_csv = False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    created_count = 0
    updated_count = 0
    warnings: List[str] = []

    try:
        for row_number, row_values in enumerate(rows, start=2):
            # normalize row_values to sequence
            if is_csv:
                row = row_values
            else:
                row = row_values

            if not any(v not in (None, "") for v in row):
                continue

            payload: Dict[str, Any] = {
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
                "Cenniki czasowe": "cennik_czasowy",
                "Limit dokumentów": "limit_dokumentow",
                "Typ rozliczenia": "typ_rozliczenia",
                "VAT czynny": "vat_czynny",
                "Przypisani pracownicy": "assigned_raw"
            }

            for src, dst in mapping.items():
                if src in header_index:
                    try:
                        payload[dst] = row[header_index[src]]
                    except Exception:
                        payload[dst] = None

            # Resolve assigned employees if present
            assigned_raw = payload.get("assigned_raw")
            if assigned_raw is not None:
                try:
                    assigned_ids, unresolved = resolve_employee_ids(cursor, assigned_raw)
                    payload["assigned_employee_ids"] = assigned_ids
                    if unresolved:
                        warnings.append(f"Wiersz {row_number}: nie znaleziono pracowników {', '.join(unresolved)}.")
                except Exception:
                    payload["assigned_employee_ids"] = []
            else:
                payload["assigned_employee_ids"] = []

            company_name = str(payload.get("nazwa", "") or "").strip()
            if not company_name:
                warnings.append(f"Wiersz {row_number}: pominięto pustą nazwę firmy.")
                continue

            # Parsowanie cenników księgowych i czasowych (tylko księgowość tutaj)
            cennik_ksiegowy_text = str(payload.get("cennik_ksiegowy", "") or "")
            cennik_czasowy_text = str(payload.get("cennik_czasowy", "") or "")

            cennik_ksiegowy = parse_cennik_text(cennik_ksiegowy_text)
            cennik_czasowy = parse_cennik_text(cennik_czasowy_text)

            payload.update({
                "limit_dokumentow": cennik_ksiegowy.get("limit_dokumentow", payload.get("limit_dokumentow")),
                "cena_za_dokument": cennik_ksiegowy.get("cena_za_dokument"),
                "ryczalt_ksiegowy": cennik_ksiegowy.get("ryczalt"),
                "ryczalt_czasowy": cennik_czasowy.get("ryczalt")
            })

            # Normalize NIP
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


# ============================================================
# HELPERS
# ============================================================
def parse_num(val) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "."))
    except Exception:
        return None


# ============================================================
# IMPORT CENNIKA KSIĘGOWEGO (cenniki główne)
# ============================================================
def import_cennik_from_excel(filepath: str, db_path: str = DB_NAME) -> Dict[str, Any]:
    """
    Import cennika księgowego z pliku Excel (.xlsx/.xls) lub CSV (.csv).
    Oczekiwane kolumny (przykładowe): klucz, cena netto 2024, za przekroczenie limitu netto, zdrowotna, netto, brutto
    Zwraca: {"imported": int, "updated": int, "errors": [str, ...]}
    """
    ext = os.path.splitext(filepath)[1].lower()

    try:
        if ext == ".csv":
            with open(filepath, newline='', encoding='utf-8') as f:
                sample = f.read(4096)
                f.seek(0)
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
            cena_za_dokument_ponad_limit = parse_num(get_cell(row, "za przekroczenie limitu netto"))
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


# ============================================================
# IMPORT CEN ZA DOKUMENT (prosty format klucz/cena)
# ============================================================
def import_ceny_za_dokument_from_excel(filepath: str, db_path: str = DB_NAME) -> Dict[str, Any]:
    """
    Import prostych cen za dokument z pliku Excel (klucz, cena) lub CSV.
    Zapisuje do tabeli pomocniczej globalne_ceny_za_dokument (jeśli istnieje).
    """
    ext = os.path.splitext(filepath)[1].lower()

    try:
        if ext == ".csv":
            with open(filepath, newline='', encoding='utf-8') as f:
                sample = f.read(4096)
                f.seek(0)
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

            # Jeśli tabela globalne_ceny_za_dokument nie istnieje, utwórz ją
            cur.execute("""
                CREATE TABLE IF NOT EXISTS globalne_ceny_za_dokument (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    typ TEXT,
                    vat INTEGER,
                    cena REAL
                )
            """)
            # Prosty zapis: rozbij klucz na typ_vat jeśli format "TYP_V"
            typ = klucz
            vat = 0
            if "_" in klucz:
                parts = klucz.split("_")
                typ = parts[0]
                try:
                    vat = int(parts[-1]) if parts[-1].isdigit() else 0
                except Exception:
                    vat = 0

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
