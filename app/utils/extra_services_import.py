"""Import katalogu usług dodatkowych z plików CSV, XLSX i XLS.

Importuje cennik usług (nazwa, cena JEDNOSTKOWA netto, VAT, kategoria).
Nie tworzy usług przypisanych do firmy ani pozycji faktur.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import openpyxl

from app.db.dao_conn import get_conn


def _header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").replace("ł", "l").replace("Ł", "L"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.casefold())).strip()


COLUMNS = {
    "name": ("nazwa uslugi", "nazwa", "nazwa pozycji", "usluga", "opis uslugi"),
    "net": ("kwota jedn netto", "kwota jednostkowa netto", "cena jedn netto",
            "cena jednostkowa netto", "cena netto", "netto", "cena_netto"),
    "gross": ("kwota jedn brutto", "kwota jednostkowa brutto", "cena jedn brutto",
              "cena jednostkowa brutto", "cena brutto", "brutto"),
    "vat": ("stawka vat", "vat", "stawka podatku vat"),
    "category": ("kategoria",),
    "currency": ("waluta",),
}
ALIASES = {alias: key for key, values in COLUMNS.items() for alias in values}
SUPPORTED_VAT = {"23%", "8%", "5%", "0%", "zw", "np", "np UE oo", "0 WDT", "0 EX"}


def _money(value: Any) -> Optional[Decimal]:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        raise ValueError("wartość nie może być logiczna")
    raw = str(value).strip().replace("\xa0", "").replace("\u202f", "").replace(" ", "")
    raw = re.sub(r"(?i)(PLN|zł)$", "", raw).strip()
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"nieprawidłowa kwota: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("cena musi być skończoną liczbą nieujemną")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _vat(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("brak stawki VAT")
    folded = raw.casefold().replace(".", "").replace(" ", "")
    specials = {
        "zw": "zw", "zwolniony": "zw", "np": "np",
        "npueoo": "np UE oo", "0wdt": "0 WDT", "0ex": "0 EX",
    }
    if folded in specials:
        return specials[folded]
    number = raw.strip("%").replace(",", ".").strip()
    try:
        rate = Decimal(number)
    except InvalidOperation as exc:
        raise ValueError(f"nieobsługiwana stawka VAT: {value!r}") from exc
    if rate == Decimal("0.23") or rate == Decimal("0.08") or rate == Decimal("0.05"):
        rate *= 100
    result = f"{int(rate)}%" if rate == rate.to_integral_value() else ""
    if result not in SUPPORTED_VAT:
        raise ValueError(f"nieobsługiwana stawka VAT: {value!r}")
    return result


def _identify_headers(values: Iterable[Any]) -> Optional[Dict[str, int]]:
    columns: Dict[str, int] = {}
    for index, cell in enumerate(values):
        key = ALIASES.get(_header(cell))
        if key and key not in columns:
            columns[key] = index
    if "name" in columns and ("net" in columns or "gross" in columns) and "vat" in columns:
        return columns
    return None


def _csv_rows(path: Path) -> List[List[Any]]:
    raw = path.read_bytes()
    decoded = None
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("Nie udało się odczytać kodowania pliku CSV.")
    sample = decoded[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    return list(csv.reader(io.StringIO(decoded), delimiter=delimiter))


def _sheet_rows(path: Path) -> List[Tuple[str, List[List[Any]]]]:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        return [("CSV", _csv_rows(path))]
    if suffix == ".xlsx":
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            return [(sheet.title, list(sheet.values)) for sheet in wb.worksheets]
        finally:
            wb.close()
    if suffix == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise ValueError("Do odczytu .xls zainstaluj xlrd albo zapisz plik jako .xlsx.") from exc
        workbook = xlrd.open_workbook(path)
        return [
            (sheet.name, [sheet.row_values(i) for i in range(sheet.nrows)])
            for sheet in workbook.sheets()
        ]
    raise ValueError("Dozwolone formaty: .csv, .xlsx i .xls.")


def parse_extra_service_file(path: str | Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Wczytuje usługę z każdego wiersza, bez danych ilościowych/fakturowych."""
    filepath = Path(path)
    if not filepath.is_file():
        raise FileNotFoundError(filepath)

    parsed: List[Dict[str, Any]] = []
    warnings: List[str] = []
    found_table = False
    seen: set[str] = set()

    for sheet_name, rows in _sheet_rows(filepath):
        header_index = -1
        mapping = None
        for index, row in enumerate(rows[:30]):
            mapping = _identify_headers(row)
            if mapping is not None:
                header_index = index
                found_table = True
                break
        if mapping is None:
            continue

        for index, row in enumerate(rows[header_index + 1:], header_index + 2):
            if not any(str(cell or "").strip() for cell in row):
                continue
            def cell(field: str) -> Any:
                column = mapping.get(field)
                return row[column] if column is not None and column < len(row) else None

            name = " ".join(str(cell("name") or "").split())
            if not name:
                warnings.append(f"{sheet_name}, wiersz {index}: brak nazwy usługi.")
                continue
            key = name.casefold()
            if key in seen:
                warnings.append(f"{sheet_name}, wiersz {index}: powtórzona nazwa {name!r}; pominięto.")
                continue
            try:
                currency = str(cell("currency") or "PLN").strip().upper()
                if currency not in ("PLN", "ZŁ", "ZL"):
                    raise ValueError(f"waluta {currency!r} — katalog obsługuje wyłącznie PLN")
                vat = _vat(cell("vat"))
                net = _money(cell("net"))
                if net is None:
                    gross = _money(cell("gross"))
                    if gross is None:
                        raise ValueError("brak ceny jednostkowej netto lub brutto")
                    if not vat.endswith("%"):
                        raise ValueError("cena brutto przy stawce zw/np nie pozwala obliczyć netto")
                    net = (gross / (Decimal("1") + Decimal(vat[:-1]) / 100)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                category = " ".join(str(cell("category") or "").split())
            except (ValueError, InvalidOperation) as exc:
                warnings.append(f"{sheet_name}, wiersz {index} ({name}): {exc}")
                continue
            seen.add(key)
            parsed.append({
                "nazwa": name,
                "cena_netto": net,
                "stawka_vat": vat,
                "kategoria": category,
                "has_category": "category" in mapping,
            })

    if not found_table:
        raise ValueError(
            "Nie znaleziono tabeli usług. Wymagane kolumny: "
            "'Nazwa usługi', 'Kwota jedn. netto' (lub brutto), 'Stawka VAT'."
        )
    if not parsed:
        raise ValueError(
            "Nie znaleziono poprawnych pozycji do importu. "
            + " ".join(warnings[:5])
        )
    return parsed, warnings


def import_extra_services_from_file(path: str | Path) -> Dict[str, Any]:
    """Dodaje usługi lub aktualizuje netto/VAT po nazwie. Operacja atomowa."""
    entries, warnings = parse_extra_service_file(path)
    conn = get_conn()
    try:
        existing: Dict[str, List[Dict[str, Any]]] = {}
        for item in conn.execute(
            "SELECT id, nazwa, cena_netto, stawka_vat, kategoria FROM uslugi_dodatkowe ORDER BY id"
        ).fetchall():
            record = dict(item)
            key = " ".join(str(record["nazwa"]).split()).casefold()
            existing.setdefault(key, []).append(record)

        result: Dict[str, Any] = {
            "created": 0, "updated": 0, "unchanged": 0, "skipped": len(warnings),
            "warnings": list(warnings),
        }
        for entry in entries:
            key = entry["nazwa"].casefold()
            choices = existing.get(key, [])
            if len(choices) > 1:
                result["skipped"] += 1
                result["warnings"].append(
                    f"{entry['nazwa']}: wiele istniejących usług o tej samej nazwie — "
                    "pominięto, aby nie zmienić niewłaściwej."
                )
                continue

            if not choices:
                item = conn.execute(
                    """INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat, kategoria)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (entry["nazwa"], float(entry["cena_netto"]), entry["stawka_vat"], entry["kategoria"]),
                ).fetchone()
                existing[key] = [{
                    "id": item[0], "nazwa": entry["nazwa"],
                    "cena_netto": float(entry["cena_netto"]),
                    "stawka_vat": entry["stawka_vat"], "kategoria": entry["kategoria"],
                }]
                result["created"] += 1
                continue

            saved = choices[0]
            saved_net = Decimal(str(saved["cena_netto"])).quantize(Decimal("0.01"))
            new_category = entry["kategoria"] if entry["has_category"] else (saved["kategoria"] or "")
            if (
                saved_net == entry["cena_netto"]
                and str(saved["stawka_vat"]) == entry["stawka_vat"]
                and str(saved["kategoria"] or "") == new_category
            ):
                result["unchanged"] += 1
                continue
            conn.execute(
                """UPDATE uslugi_dodatkowe
                   SET cena_netto = %s, stawka_vat = %s, kategoria = %s
                   WHERE id = %s""",
                (float(entry["cena_netto"]), entry["stawka_vat"], new_category, saved["id"]),
            )
            result["updated"] += 1
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
