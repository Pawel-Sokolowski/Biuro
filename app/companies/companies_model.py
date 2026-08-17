# Auto-generated company model functions extracted from main.py

import sqlite3

from app.companies.constants import COMPANY_FIELD_ORDER
from app.utils.parsing import normalize_identifier, normalize_pkd_codes, parse_date_value, parse_int_value
from app.config import DB_NAME


def ensure_columns(cursor, table_name, columns):
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {row[1] for row in cursor.fetchall()}
    for column_name, column_definition in columns.items():
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

def ensure_cenniki_table_and_columns():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    # tabela cenniki
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cenniki (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klucz TEXT UNIQUE,
            cena_netto_2024 REAL,
            wzrost_ilosci_dokumentow REAL,
            cena_za_dokument REAL,
            za_przekroczenie_limitu_netto REAL,
            zdrowotna REAL,
            netto REAL,
            brutto REAL,
            opis TEXT,
            updated_at TEXT
        )
    """)

    # Dodaj kolumny vat i deklaracja do tabeli firmy, jeśli nie istnieją
    cur.execute("PRAGMA table_info(firmy)")
    existing = {r[1] for r in cur.fetchall()}
    if "vat" not in existing:
        cur.execute("ALTER TABLE firmy ADD COLUMN vat INTEGER DEFAULT 0")
    if "deklaracja" not in existing:
        cur.execute("ALTER TABLE firmy ADD COLUMN deklaracja TEXT DEFAULT ''")

    conn.commit()
    conn.close()

def get_role_id(cursor, role_name):
    cursor.execute("SELECT id FROM role WHERE nazwa = ?", (role_name,))
    result = cursor.fetchone()
    return result[0] if result else None

def get_company_assigned_employee_ids(cursor, company_id):
    cursor.execute("""
        SELECT DISTINCT pracownik_id
        FROM pracownik_firma
        WHERE firma_id = ?
    """, (company_id,))
    return [row[0] for row in cursor.fetchall()]

def update_company_employee_assignments(cursor, company_id, employee_ids):
    role_id = get_role_id(cursor, "opiekun")
    if role_id is None:
        return

    current_ids = set(get_company_assigned_employee_ids(cursor, company_id))
    desired_ids = set(employee_ids)

    for employee_id in desired_ids - current_ids:
        cursor.execute("""
            INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id)
            VALUES (?, ?, ?)
        """, (employee_id, company_id, role_id))

    for employee_id in current_ids - desired_ids:
        cursor.execute("""
            DELETE FROM pracownik_firma
            WHERE pracownik_id = ? AND firma_id = ? AND rola_id = ?
        """, (employee_id, company_id, role_id))

def serialize_company_payload(payload):
    company_data = {}
    for field_name in COMPANY_FIELD_ORDER:
        raw_value = payload.get(field_name, "")
        if field_name in {"nip", "regon", "krs", "pesel"}:
            expected_lengths = {
                "nip": {10},
                "regon": {9, 14},
                "krs": {10},
                "pesel": {11}
            }.get(field_name)
            company_data[field_name] = normalize_identifier(raw_value, expected_lengths)
        elif field_name in {
            "data_rozpoczecia_dg", "data_rozpoczecia_sprzedazy", "data_koniec_zus_start", "data_koniec_zus_pref",
            "data_koniec_maly_zus", "data_koniec_a1", "data_rozpoczecia_obslugi", "data_zakonczenia_obslugi"
        }:
            company_data[field_name] = parse_date_value(raw_value)
        elif field_name in {"kody_pkd"}:
            company_data[field_name] = normalize_pkd_codes(raw_value)
        elif field_name in {"limit_dokumentow", "liczba_pracownikow", "uop", "uz", "uod", "glowny_cennik_manualny"}:
            company_data[field_name] = parse_int_value(raw_value, 0)
        else:
            company_data[field_name] = str(raw_value).strip() if raw_value not in (None, "") else ""
    return company_data

def apply_default_main_pricing(cursor, company_id, document_count):
    cursor.execute("""
        SELECT limit_dokumentow, cennik_ksiegowy, glowny_cennik, glowny_cennik_manualny
        FROM firmy
        WHERE id = ?
    """, (company_id,))
    company_row = cursor.fetchone()
    if not company_row:
        return None

    limit_dokumentow, cennik_ksiegowy, glowny_cennik, manual_override = company_row
    limit_dokumentow = limit_dokumentow or 0
    if manual_override:
        return glowny_cennik

    if limit_dokumentow and document_count > limit_dokumentow and cennik_ksiegowy:
        cursor.execute("""
            UPDATE firmy
            SET glowny_cennik = ?, glowny_cennik_manualny = 0
            WHERE id = ?
        """, (cennik_ksiegowy, company_id))
        return cennik_ksiegowy

    return glowny_cennik

def save_company(cursor, payload, company_id=None):
    company_data = serialize_company_payload(payload)
    assigned_employee_ids = payload.get("assigned_employee_ids", [])

    if company_id:
        assignments = ", ".join(f"{field_name} = ?" for field_name in COMPANY_FIELD_ORDER)
        cursor.execute(
            f"UPDATE firmy SET {assignments} WHERE id = ?",
            [company_data[field_name] for field_name in COMPANY_FIELD_ORDER] + [company_id]
        )
        final_company_id = company_id
    else:
        placeholders = ", ".join("?" for _ in COMPANY_FIELD_ORDER)
        cursor.execute(
            f"INSERT INTO firmy ({', '.join(COMPANY_FIELD_ORDER)}) VALUES ({placeholders})",
            [company_data[field_name] for field_name in COMPANY_FIELD_ORDER]
        )
        final_company_id = cursor.lastrowid

    update_company_employee_assignments(cursor, final_company_id, assigned_employee_ids)
    return final_company_id
