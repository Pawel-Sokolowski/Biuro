"""Usuwanie danych firm (klienci, cenniki, faktury, dokumenty, statusy, importy raportów).

Zostają: pracownicy, role, konta e-mail i wiadomości, ustawienia, czat, zadania, kalendarz, urzędy skarbowe, logi.
"""
from typing import Dict

from app.db.dao_conn import get_conn

# kolejność: tabele zależne przed firmami
TABLES = [
    "import_czas_pracy",
    "import_faktury_pozycje",
    "import_history",
    "kadry_umowy_miesieczne",
    "statusy_miesieczne",
    "czasomierze",
    "dokumenty_attachments",
    "dokumenty",
    "faktury_platnosci",
    "bank_transakcje",
    "faktury_pozycje",
    "faktury",
    "faktury_archiwum",
    "uslugi_dodatkowe_firma",
    "uslugi_dodatkowe",
    "rozliczenia_saldeo",
    "company_notes",
    "company_contacts",
    "pelnomocnictwa",
    "udzialowcy",
    "pracownik_firma",
    "firma_cennik_preference",
    "kontrahenci",
    "firmy",
    "cenniki",
    "cenniki_kadry",
]


def _existing_tables(conn):
    existing = {
        r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
    }
    return [t for t in TABLES if t in existing]


def count_company_data() -> Dict[str, int]:
    conn = get_conn()
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in _existing_tables(conn)}
    finally:
        conn.close()


def delete_company_data() -> None:
    conn = get_conn()
    try:
        for t in _existing_tables(conn):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
