import csv
import hashlib
import os
import re
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

import openpyxl

from app.config import get_database
from app.db.dao_audit import record_import
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

    text = str(line).strip()

    match = re.match(r"(.+?)\s+(\d+)\s*/\s*(\d+)", text)
    if match:
        return match.group(1).strip(), match.group(2).strip(), match.group(3).strip()

    match = re.match(r"(.+?)\s+(\d+)$", text)
    if match:
        return match.group(1).strip(), match.group(2).strip(), ""

    match = re.match(r"(\d+)\s*/\s*(\d+)", text)
    if match:
        return "", match.group(1).strip(), match.group(2).strip()

    match = re.match(r"(\d+)$", text)
    if match:
        return "", match.group(1).strip(), ""

    return text, "", ""


def parse_num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


PRICE_PATTERN = r"(\d+(?:[.,]\d{1,2})?)"


def _normalize_pricing_text(text: Any) -> str:
    normalized = str(text or "")
    normalized = normalized.replace("\xa0", " ")
    normalized = normalized.replace("\r", " ")
    normalized = normalized.replace("\n", " ")
    return re.sub(r"\s+", " ", normalized).strip()


def _price_to_float(value: str) -> float:
    return float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))


def _find_price(pattern: str, text: str) -> Optional[float]:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        return None
    return _price_to_float(match.group(1))


def parse_cennik_ksiegowy(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "limit_dokumentow": None,
        "cena_za_dokument": None,
        "ryczalt_ksiegowy": None,
        "zdrowotna": None,
    }

    if not text:
        return out

    normalized = _normalize_pricing_text(text)

    match = re.search(
        r"\b(?:limit|do)\s+(\d+)\s*"
        r"(?:szt\.?|dok\.?|dokument(?:ów|y)?)?",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is not None:
        out["limit_dokumentow"] = int(match.group(1))

    out["cena_za_dokument"] = _find_price(
        rf"\b(?:ponad\s+limit|powyżej\s+limitu)\b.*?{PRICE_PATTERN}\s*(?:zł|pln)?",
        normalized,
    )
    out["ryczalt_ksiegowy"] = _find_price(
        rf"\bryczałt\b(?!\s+do\s+\d+).*?{PRICE_PATTERN}\s*(?:zł|pln)?",
        normalized,
    )
    out["zdrowotna"] = _find_price(
        rf"\bzdrowotna\b.*?{PRICE_PATTERN}\s*(?:zł|pln)?",
        normalized,
    )
    return out


def _find_service_price(text: str, service_pattern: str) -> Optional[float]:
    pattern = (
        rf"(?:usług(?:a|i)?\s*[-:]?\s*)?"
        rf"\b{service_pattern}\b"
        rf"\s*[-:]?\s*"
        rf"{PRICE_PATTERN}"
    )
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        return None
    return _price_to_float(match.group(1))


def parse_cennik_kadrowy(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
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

    normalized = _normalize_pricing_text(text)
    zdrowotna = _find_service_price(normalized, r"zdrowotna")
    uop = _find_service_price(normalized, r"u\s*o\s*p")
    uz = _find_service_price(normalized, r"u\s*z")
    uod = _find_service_price(normalized, r"u\s*o\s*d")

    if zdrowotna is not None:
        out["zdrowotna"] = zdrowotna
    if uop is not None:
        out["uop_ilosc"] = 1
        out["uop_cennik"] = uop
    if uz is not None:
        out["uz_ilosc"] = 1
        out["uz_cennik"] = uz
    if uod is not None:
        out["uod_ilosc"] = 1
        out["uod_cennik"] = uod

    return out


def parse_cennik_czasowy(text: str) -> Dict[str, Any]:
    out = {"ryczalt_czasowy": None}
    if not text:
        return out

    normalized = _normalize_pricing_text(text)
    match = re.search(rf"ryczałt.*?{PRICE_PATTERN}", normalized, flags=re.IGNORECASE)
    if match:
        out["ryczalt_czasowy"] = _price_to_float(match.group(1))
    return out


# ============================================================
# WCZYTANIE PLIKU CSV/XLSX
# ============================================================
def _load_rows_from_file(filepath: str) -> Tuple[List[str], List[Any]]:
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".csv":
        with open(filepath, newline="", encoding="utf-8-sig") as file:
            sample = file.read(4096)
            file.seek(0)
            delimiter = "," if sample.count(",") >= sample.count(";") else ";"
            reader = csv.reader(file, delimiter=delimiter)
            header = next(reader, None)
            rows = list(reader)
    else:
        workbook = openpyxl.load_workbook(filepath, data_only=True)
        worksheet = workbook.active
        header = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        rows = list(worksheet.iter_rows(min_row=2, values_only=True))

    if not header:
        raise ValueError("Brak nagłówków w pliku.")

    headers = [str(value).strip() if value is not None else "" for value in header]
    return headers, rows


def import_tax_offices_from_excel(filepath: str) -> int:
    """Importuje lub aktualizuje katalog urzędów skarbowych z XLSX/CSV."""
    headers, rows = _load_rows_from_file(filepath)

    def normalize_header(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().upper()

    index = {normalize_header(header): pos for pos, header in enumerate(headers)}

    def value(row: Any, *names: str) -> str:
        for name in names:
            pos = index.get(normalize_header(name))
            if pos is not None and pos < len(row):
                return str(row[pos] or "").strip()
        return ""

    records = []
    for row in rows:
        name = value(row, "NAZWA URZĘDU")
        if name:
            records.append((
                value(row, "WOJEWÓDZTWO"), value(row, "TYP"), name,
                value(row, "KOD"), value(row, "MIASTO"), value(row, "ULICA"),
                value(row, "NR BUDYNKU / LOKALU"), value(row, "POCZTA"),
                value(row, "NR TELEFONU wraz z nr kierunkowym", "NR TELEFONU"),
                value(row, "NR FAXU"), value(row, "ADRES E-MAIL URZĘDU"),
                value(row, "KOD JEDNOSTKI"), value(row, "ADRESY STRON BIP JEDNOSTEK KAS"),
            ))

    if not records:
        raise ValueError("Nie znaleziono wierszy urzędów skarbowych.")

    conn = sqlite3.connect(get_database(), timeout=30)
    try:
        conn.executemany(
            """
            INSERT INTO urzedy_skarbowe (
                wojewodztwo, typ, nazwa, kod, miasto, ulica, nr_budynku_lokalu,
                poczta, nr_telefonu, nr_faxu, email, kod_jednostki, adresy_bip
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kod_jednostki) DO UPDATE SET
                wojewodztwo = excluded.wojewodztwo,
                typ = excluded.typ,
                nazwa = excluded.nazwa,
                kod = excluded.kod,
                miasto = excluded.miasto,
                ulica = excluded.ulica,
                nr_budynku_lokalu = excluded.nr_budynku_lokalu,
                poczta = excluded.poczta,
                nr_telefonu = excluded.nr_telefonu,
                nr_faxu = excluded.nr_faxu,
                email = excluded.email,
                adresy_bip = excluded.adresy_bip
            """,
            records,
        )
        conn.commit()
        return len(records)
    finally:
        conn.close()


# ============================================================
# BUDOWANIE PAYLOADU Z WIERSZA
# ============================================================
def _build_payload_from_row(
    headers: List[str],
    header_index: Dict[str, int],
    row: Any,
) -> Dict[str, Any]:
    del headers

    def get(col: str) -> Any:
        idx = header_index.get(col)
        return row[idx] if idx is not None and idx < len(row) else None

    payload: Dict[str, Any] = {}
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

    # VAT - zachowujemy wartość z Excela, jeżeli kolumna istnieje.
    payload["vat_czynny"] = (
        get("VAT czynny")
        if "VAT czynny" in header_index
        else get("VAT")
    )

    payload["miasto"] = get("Miasto")
    payload["kod_pocztowy"] = get("Kod pocztowy")
    payload["ulica"], payload["adres_budynek"], payload["adres_lokal"] = split_address_line(get("Ulica"))

    payload["adres_zam_miasto"] = get("Adres zamieszkania - miasto")
    payload["adres_zam_kod_pocztowy"] = get("Adres zamieszkania - kod pocztowy")
    payload["adres_zam_ulica"], payload["adres_zam_budynek"], payload["adres_zam_lokal"] = split_address_line(
        get("Adres zamieszkania - ulica")
    )

    payload["adres_koresp_miasto"] = get("Adres korespondencyjny - miasto") or get("Adres korespondpondencyjny - miasto")
    payload["adres_koresp_kod_pocztowy"] = get("Adres korespondencyjny - kod pocztowy")
    payload["adres_koresp_ulica"], payload["adres_koresp_budynek"], payload["adres_koresp_lokal"] = split_address_line(
        get("Adres korespondencyjny - ulica")
    )

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

    cennik_ksiegowy_text = str(get("Cenniki usług księgowych") or "")
    cks = parse_cennik_ksiegowy(cennik_ksiegowy_text)
    payload["cennik_ksiegowy"] = cennik_ksiegowy_text
    payload["cennik_ksiegowy_parsed"] = cks
    payload["limit_dokumentow"] = cks["limit_dokumentow"]
    payload["zdrowotna"] = cks["zdrowotna"]

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

    cennik_czasowy_text = str(get("Cenniki czasowe") or "")
    cczas = parse_cennik_czasowy(cennik_czasowy_text)
    payload["cennik_czasowy"] = cennik_czasowy_text
    payload["cennik_czasowy_parsed"] = cczas
    payload["ryczalt_czasowy"] = cczas["ryczalt_czasowy"]
    payload["assigned_raw"] = get("Przypisani pracownicy")
    return payload


# ============================================================
# IMPORT DO PAYLOADÓW (DLA WIZARDU)
# ============================================================
def import_companies_from_excel_file_payloads(filepath: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    try:
        headers, rows = _load_rows_from_file(filepath)
    except Exception as exc:
        return [], [f"Plik nie mógł zostać wczytany: {exc}"]

    header_index = {header: idx for idx, header in enumerate(headers)}
    payloads: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for row_number, row in enumerate(rows, start=2):
        if not any(value not in (None, "") for value in row):
            continue

        payload = _build_payload_from_row(headers, header_index, row)
        assigned_raw = payload.get("assigned_raw")
        if assigned_raw:
            unresolved = [
                part.strip()
                for part in str(assigned_raw).split(",")
                if part.strip() and not re.match(r"^[A-Za-zĄąĆćĘęŁłŃńÓóŚśŹźŻż ]+$", part.strip())
            ]
            if unresolved:
                warnings.append(f"Wiersz {row_number}: nie znaleziono pracowników {', '.join(unresolved)}.")

        payloads.append(payload)

    return payloads, warnings


# ============================================================
# ZAPIS DO DB
# ============================================================
def _pricing_key(prefix: str, values: Dict[str, Any]) -> str:
    raw = "|".join(f"{key}={values.get(key)!r}" for key in sorted(values))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()
    return f"IMPORT_{prefix}_{digest}"


def _fold_pl(value: Any) -> str:
    text = str(value or "").strip().lower().replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.translate(str.maketrans({
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
        "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    }))


# Prefiks klucza cennika: z formy opodatkowania, nie z tekstu "Ryczałt 1300"
ACCOUNTING_CENNIK_PREFIXES = {
    "KPIR": ("KPIR",),
    "RYCZALT": ("Ryczałt", "RYCZALT", "Ryczalt"),
    "KH": ("KH", "KR"),
}


def resolve_accounting_cennik_typ(
    forma_opodatkowania: Any = None,
    forma_dzialalnosci: Any = None,
) -> str:
    tax = _fold_pl(forma_opodatkowania)
    activity = _fold_pl(forma_dzialalnosci)

    if tax:
        if "ryczalt" in tax:
            return "RYCZALT"
        if "liniow" in tax or "skala" in tax or "kpir" in tax:
            return "KPIR"
        if "cit" in tax or "ksieg" in tax or tax in {"kh", "kr"}:
            return "KH"

    if "kpir" in activity or "osoba fizyczna" in activity:
        return "KPIR"
    if "cywiln" in activity:
        return "KPIR"
    if activity:
        return "KH"
    return "KPIR"


def _accounting_cennik_key_candidates(
    typ: str,
    vat_flag: str,
    limit: int,
    price: Any = None,
) -> List[str]:
    prefixes = ACCOUNTING_CENNIK_PREFIXES.get(typ, (typ,))

    if price is not None:
        price_key = _format_price_key(price)
        return [
            f"{prefix}_{vat_flag}_{limit}_{price_key}"
            for prefix in prefixes
        ]

    return [
        f"{prefix}_{vat_flag}_{limit}"
        for prefix in prefixes
    ]


def _ensure_accounting_cennik(cursor: sqlite3.Cursor, payload: Dict[str, Any]) -> Optional[str]:
    parsed = payload.get("cennik_ksiegowy_parsed") or {}
    text = str(payload.get("cennik_ksiegowy") or "").strip()
    if not text:
        return None

    limit_price = parsed.get("cena_za_dokument")
    base_price = parsed.get("ryczalt_ksiegowy")
    zdrowotna = parsed.get("zdrowotna") or 0
    if limit_price is None and base_price is None:
        return None

    typ = resolve_accounting_cennik_typ(
        payload.get("forma_opodatkowania"),
        payload.get("forma_dzialalnosci"),
    )

    # Poprawione ustalanie VAT.
    vat_value = payload.get("vat_czynny")
    vat_normalized = _fold_pl(vat_value)

    vat_active = vat_value in (1, True, "1", "T", "t") or vat_normalized in {
        "tak",
        "true",
        "yes",
        "czynny",
        "vat czynny",
    }

    vat_flag = "T" if vat_active else "N"

    limit = parsed.get("limit_dokumentow") or 0

    # Cena podstawowa jest częścią klucza.
    # Przykład:
    # Ryczałt + T + 5 + 330
    # -> Ryczałt_T_5_330
    if base_price is not None:
        candidates = _accounting_cennik_key_candidates(
            typ,
            vat_flag,
            limit,
            base_price,
        )
    else:
        candidates = _accounting_cennik_key_candidates(
            typ,
            vat_flag,
            limit,
        )

    placeholders = ", ".join("?" for _ in candidates)

    cursor.execute(
        f"SELECT klucz FROM cenniki WHERE klucz IN ({placeholders}) LIMIT 1",
        candidates,
    )
    row = cursor.fetchone()
    if row:
        return row[0]

    generated_key = candidates[0]

    cursor.execute(
        """
        INSERT INTO cenniki (
            klucz, typ, cena_netto_ksiegowosc, cena_brutto_ksiegowosc,
            cena_za_dokument_ponad_limit, zdrowotna, netto, brutto
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generated_key,
            typ,
            base_price or 0,
            round((base_price or 0) * 1.23, 2),
            limit_price or 0,
            zdrowotna,
            base_price or 0,
            round((base_price or 0) * 1.23, 2),
        ),
    )

    return generated_key


def _decimal_price(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value

    normalized = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Nieprawidłowa cena cennika kadrowego: {value!r}") from exc


def _format_price_key(value: Any) -> str:
    price = _decimal_price(value).normalize()
    if price == price.to_integral():
        return str(int(price))
    return format(price, "f").rstrip("0").rstrip(".").replace(".", "_")


def _ensure_employee_cenniki(cursor: sqlite3.Cursor, payload: Dict[str, Any]) -> Dict[str, str]:
    text = str(payload.get("cennik_kadrowy") or "").strip()
    if not text:
        return {}

    parsed = payload.get("cennik_kadrowy_parsed")
    if not isinstance(parsed, dict):
        parsed = {}

    parsed_from_text = parse_cennik_kadrowy(text)
    for field, value in parsed_from_text.items():
        if value is not None:
            parsed[field] = value

    service_definitions = (
        ("ZDROWOTNA", "Zdrowotna", parsed.get("zdrowotna")),
        ("UOP", "UoP", parsed.get("uop_cennik")),
        ("UZ", "UZ", parsed.get("uz_cennik")),
        ("UOD", "UoD", parsed.get("uod_cennik")),
    )

    keys: Dict[str, str] = {}
    for service_code, key_prefix, raw_price in service_definitions:
        if raw_price is None:
            continue

        price_netto = _decimal_price(raw_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        price_brutto = (price_netto * Decimal("1.23")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        key = f"{key_prefix}_{_format_price_key(price_netto)}"

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
            (key, float(price_netto), float(price_brutto), f"Import z Excela: {service_code}"),
        )
        keys[service_code] = key

    return keys


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
            "INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id) VALUES (?, ?, NULL)",
            (employee_id, firma_id),
        )
    if unresolved:
        warnings.append(f"Wiersz {row_number}: nie znaleziono pracowników {', '.join(unresolved)}.")


# ============================================================
# ZAPIS DO DB
# ============================================================
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
    employee_keys = _ensure_employee_cenniki(cursor, payload)

    if "UOP" in employee_keys:
        payload["uop_cennik"] = employee_keys["UOP"]
    if "UZ" in employee_keys:
        payload["uz_cennik"] = employee_keys["UZ"]
    if "UOD" in employee_keys:
        payload["uod_cennik"] = employee_keys["UOD"]

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

    fields = []
    values = []

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
# IMPORTERY FIRM
# ============================================================
def import_companies_from_excel_file(filepath: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    try:
        headers, rows = _load_rows_from_file(filepath)
    except Exception as exc:
        return {"created": 0, "updated": 0, "warnings": [f"Plik nie mógł zostać wczytany: {exc}"]}

    header_index = {header: idx for idx, header in enumerate(headers)}
    conn = sqlite3.connect(db_path or get_database(), timeout=30)
    cursor = conn.cursor()
    created_count = 0
    updated_count = 0
    warnings: List[str] = []

    try:
        for row_number, row in enumerate(rows, start=2):
            if not any(value not in (None, "") for value in row):
                continue
            payload = _build_payload_from_row(headers, header_index, row)
            if _apply_payload_to_db(cursor, payload, row_number, warnings):
                created_count += 1
            else:
                updated_count += 1
            if row_number % 20 == 0:
                conn.commit()

        conn.commit()
        result = {"created": created_count, "updated": updated_count, "warnings": warnings}
        record_import("firmy", filepath, result)
        return result
    finally:
        conn.close()


def save_imported_payload(
    payload: Dict[str, Any],
    db_path: Optional[str] = None,
    row_number: int = 2,
) -> Dict[str, Any]:
    """Zapisuje jeden zatwierdzony payload z kreatora importu."""
    conn = sqlite3.connect(db_path or get_database(), timeout=30)
    cursor = conn.cursor()
    warnings: List[str] = []
    try:
        created = _apply_payload_to_db(cursor, payload, row_number, warnings)
        conn.commit()
        return {"created": int(created), "updated": int(not created), "warnings": warnings}
    finally:
        conn.close()


def import_companies_fast_from_excel_file(filepath: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    try:
        headers, rows = _load_rows_from_file(filepath)
    except Exception as exc:
        return {"created": 0, "updated": 0, "warnings": [f"Plik nie mógł zostać wczytany: {exc}"]}

    header_index = {header: idx for idx, header in enumerate(headers)}
    conn = sqlite3.connect(db_path or get_database(), timeout=30)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    created = 0
    updated = 0
    warnings: List[str] = []

    try:
        for row_number, row in enumerate(rows, start=2):
            if not any(value not in (None, "") for value in row):
                continue
            payload = _build_payload_from_row(headers, header_index, row)
            if _apply_payload_to_db(cursor, payload, row_number, warnings):
                created += 1
            else:
                updated += 1

        conn.commit()
        result = {"created": created, "updated": updated, "warnings": warnings}
        record_import("firmy_fast", filepath, result)
        return result
    finally:
        conn.close()


# ============================================================
# IMPORT CENNIKA KSIĘGOWEGO
# ============================================================
def import_cennik_from_excel(filepath: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    del db_path
    try:
        headers, rows = _load_rows_from_file(filepath)
    except Exception:
        return []

    normalized_headers = [header.lower() for header in headers]
    idx = {header: pos for pos, header in enumerate(normalized_headers)}

    required = [
        "klucz",
        "cena netto 2024",
        "wzrost ilości dokumentów",
        "cena za dokument",
        "zdrowotna",
        "netto",
        "brutto",
    ]

    if any(column not in idx for column in required):
        return []

    def get_cell(row: Any, key: str) -> Any:
        position = idx.get(key)
        return row[position] if position is not None and position < len(row) else None

    payloads = []

    for row in rows:
        klucz_raw = get_cell(row, "klucz")

        if klucz_raw is None or not str(klucz_raw).strip():
            continue

        # NOWE:
        # Klucz jest automatycznie rozszerzany o wartość z kolumny Netto.
        #
        # Ryczałt_N_5 + 270  -> Ryczałt_N_5_270
        # Ryczałt_N_10 + 295 -> Ryczałt_N_10_295

        base_key = str(klucz_raw).strip()

        netto = parse_num(get_cell(row, "netto")) or 0.0

        generated_key = f"{base_key}_{_format_price_key(netto)}"

        payloads.append({
            "klucz": generated_key,
            "cena_netto_ksiegowosc": parse_num(get_cell(row, "cena netto 2024")) or 0.0,
            "cena_za_dokument_ponad_limit": parse_num(get_cell(row, "cena za dokument")) or 0.0,
            "zdrowotna": parse_num(get_cell(row, "zdrowotna")) or 0.0,
            "netto": netto,
            "brutto": parse_num(get_cell(row, "brutto")) or 0.0,
            "wzrost_ilosci_dokumentow": parse_num(get_cell(row, "wzrost ilości dokumentów")) or 0.0,
        })

    return payloads


# ============================================================
# IMPORT CEN ZA DOKUMENT
# ============================================================
def import_ceny_za_dokument_from_excel(filepath: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    del db_path
    try:
        headers, rows = _load_rows_from_file(filepath)
    except Exception:
        return []

    normalized_headers = [header.lower() for header in headers]
    idx = {header: pos for pos, header in enumerate(normalized_headers)}
    if "klucz" not in idx or "cena" not in idx:
        return []

    payloads = []
    for row in rows:
        if not any(cell not in (None, "") for cell in row):
            continue
        klucz = str(row[idx["klucz"]] or "").strip()
        if not klucz:
            continue

        cena = parse_num(row[idx["cena"]])
        typ = klucz
        vat = 0
        if "_" in klucz:
            parts = klucz.split("_")
            typ = parts[0]
            vat = int(parts[-1]) if parts[-1].isdigit() else 0

        payloads.append({"typ": typ, "vat": vat, "cena": cena})
    return payloads