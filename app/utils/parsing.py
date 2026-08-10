# Auto-generated parsing helpers extracted from main.py

import datetime

def normalize_identifier(value, expected_lengths=None):
    if value is None:
        return ""
    normalized = "".join(ch for ch in str(value).strip() if ch.isdigit())
    if not normalized:
        return ""
    if expected_lengths and len(normalized) not in expected_lengths:
        return normalized
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
    for date_format in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(raw_value, date_format).date().isoformat()
        except ValueError:
            continue
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
        full_name = f"{first_name} {last_name}".strip().lower()
        lookup[str(employee_id)] = employee_id
        lookup[email_address.lower()] = employee_id
        lookup[full_name] = employee_id

    for token in split_multi_value(raw_value):
        employee_id = lookup.get(token.lower())
        if employee_id and employee_id not in resolved_ids:
            resolved_ids.append(employee_id)
        elif not employee_id:
            unresolved.append(token)
    return resolved_ids, unresolved
