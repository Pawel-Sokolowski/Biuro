# app/companies/companies_model.py
import sqlite3
from app.config import DB_NAME
from app.companies.constants import COMPANY_FIELD_ORDER, COMPANY_EXTRA_COLUMNS
from app.utils.parsing import (
    normalize_identifier,
    normalize_pkd_codes,
    parse_date_value,
    parse_int_value
)

def ensure_columns(cursor, table_name, columns):
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing = {r[1] for r in cursor.fetchall()}
    for col, definition in columns.items():
        if col not in existing:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col} {definition}")

def serialize_company_payload(payload):
    """
    Zwraca słownik danych zgodny z COMPANY_FIELD_ORDER.
    Obsługuje:
      - identyfikatory
      - daty
      - PKD
      - limity
      - cenniki
      - zatrudnienie
      - wszystkie pola z COMPANY_EXTRA_COLUMNS
    """

    data = {}

    for field in COMPANY_FIELD_ORDER:
        raw = payload.get(field, "")

        # identyfikatory
        if field in {"nip", "regon", "krs", "pesel"}:
            data[field] = normalize_identifier(raw, {
                "nip": {10},
                "regon": {9, 14},
                "krs": {10},
                "pesel": {11}
            }.get(field))

        # daty
        elif field in {
            "data_rozpoczecia_dg", "data_rozpoczecia_sprzedazy",
            "data_koniec_zus_start", "data_koniec_zus_pref",
            "data_koniec_maly_zus", "data_koniec_a1",
            "data_rozpoczecia_obslugi", "data_zakonczenia_obslugi"
        }:
            data[field] = parse_date_value(raw)

        # PKD
        elif field == "kody_pkd":
            data[field] = normalize_pkd_codes(raw)

        # liczby
        elif field in {
            "limit_dokumentow", "uop", "uz", "uod",
            "glowny_cennik_manualny", "liczba_pracownikow"
        }:
            data[field] = parse_int_value(raw, 0)

        # tekst
        else:
            data[field] = str(raw).strip() if raw else ""

    return data

def get_role_id(cursor, role_name):
    cursor.execute("SELECT id FROM role WHERE nazwa=?", (role_name,))
    row = cursor.fetchone()
    return row[0] if row else None


def get_company_assigned_employee_ids(cursor, company_id):
    cursor.execute("SELECT pracownik_id FROM pracownik_firma WHERE firma_id=?", (company_id,))
    return [r[0] for r in cursor.fetchall()]


def update_company_employee_assignments(cursor, company_id, employee_ids):
    role_id = get_role_id(cursor, "opiekun")
    if not role_id:
        return

    current = set(get_company_assigned_employee_ids(cursor, company_id))
    desired = set(employee_ids)

    # dodawanie
    for add_id in desired - current:
        cursor.execute("""
            INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id)
            VALUES (?, ?, ?)
        """, (add_id, company_id, role_id))

    # usuwanie
    for del_id in current - desired:
        cursor.execute("""
            DELETE FROM pracownik_firma
            WHERE pracownik_id=? AND firma_id=? AND rola_id=?
        """, (del_id, company_id, role_id))

def apply_default_main_pricing(cursor, company_id, document_count):
    cursor.execute("""
        SELECT limit_dokumentow, cennik_ksiegowy, glowny_cennik, glowny_cennik_manualny
        FROM firmy WHERE id=?
    """, (company_id,))
    row = cursor.fetchone()
    if not row:
        return None

    limit, cennik_ksiegowy, glowny, manual = row
    limit = limit or 0

    if manual:
        return glowny

    if limit and document_count > limit and cennik_ksiegowy:
        cursor.execute("""
            UPDATE firmy SET glowny_cennik=?, glowny_cennik_manualny=0
            WHERE id=?
        """, (cennik_ksiegowy, company_id))
        return cennik_ksiegowy

    return glowny

def save_company(cursor, payload, company_id=None):
    data = serialize_company_payload(payload)
    assigned = payload.get("assigned_employee_ids", [])

    if company_id:
        assignments = ", ".join(f"{f}=?" for f in COMPANY_FIELD_ORDER)
        cursor.execute(
            f"UPDATE firmy SET {assignments} WHERE id=?",
            [data[f] for f in COMPANY_FIELD_ORDER] + [company_id]
        )
        final_id = company_id
    else:
        placeholders = ", ".join("?" for _ in COMPANY_FIELD_ORDER)
        cursor.execute(
            f"INSERT INTO firmy ({', '.join(COMPANY_FIELD_ORDER)}) VALUES ({placeholders})",
            [data[f] for f in COMPANY_FIELD_ORDER]
        )
        final_id = cursor.lastrowid

    update_company_employee_assignments(cursor, final_id, assigned)
    return final_id
