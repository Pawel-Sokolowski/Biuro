# Auto-generated parsing helpers extracted from main.py

import datetime
import re

from dateutil import parser as dateutil_parser

def normalize_identifier(value, expected_lengths=None):
    if value is None:
        return ""
    normalized = "".join(ch for ch in str(value).strip() if ch.isdigit())
    if not normalized:
        return ""
    if expected_lengths and len(normalized) not in expected_lengths:
        # Excel drops leading zeros of numeric cells; restore them
        longer = [n for n in sorted(expected_lengths) if n > len(normalized)]
        if longer:
            return normalized.zfill(longer[0])
    return normalized

def normalize_pkd_codes(value):
    if value is None:
        return ""
    tokens = []
    for raw_token in str(value).replace("\n", ",").replace(";", ",").split(","):
        token = raw_token.strip().upper()
        if token and token not in tokens:
            tokens.append(token)
    return ", ".join(tokens)

def parse_date_value(value):
    if value in (None, ""):
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    raw_value = str(value).strip()
    if not raw_value:
        return ""
    if not re.fullmatch(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}", raw_value):
        return raw_value
    try:
        # Rok na początku = ISO (rrrr-mm-dd), w pozostałych przypadkach polski zapis dd.mm.rrrr
        return dateutil_parser.parse(raw_value, dayfirst=not re.match(r"\d{4}", raw_value)).date().isoformat()
    except (ValueError, OverflowError):
        return raw_value

def parse_int_value(value, default=0):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default

def split_multi_value(raw_value):
    if raw_value is None:
        return []
    return [chunk.strip() for chunk in str(raw_value).replace("\n", ",").replace(";", ",").split(",") if chunk.strip()]

def resolve_employee_ids(cursor, raw_value):
    resolved_ids = []
    unresolved = []
    if not raw_value:
        return resolved_ids, unresolved

    cursor.execute("SELECT id, email, imie, nazwisko FROM pracownicy WHERE aktywny = 1")
    employees = cursor.fetchall()
    lookup = {}
    for employee_id, email_address, first_name, last_name in employees:
        first = (first_name or "").strip().lower()
        last = (last_name or "").strip().lower()
        lookup[str(employee_id)] = employee_id
        if email_address:
            lookup[email_address.strip().lower()] = employee_id
        lookup[f"{first} {last}".strip()] = employee_id
        lookup[f"{last} {first}".strip()] = employee_id
        lookup.setdefault(f"{first[:1]}{last[:1]}", employee_id)

    for token in split_multi_value(raw_value):
        employee_id = lookup.get(token.lower())
        if employee_id and employee_id not in resolved_ids:
            resolved_ids.append(employee_id)
        elif not employee_id:
            unresolved.append(token)
    return resolved_ids, unresolved


def to_float(value, default=0.0):
    """Liczba z przecinkiem lub kropką; przy błędzie zwraca default."""
    try:
        if value is None or value == "":
            return float(default)
        return float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return float(default)
