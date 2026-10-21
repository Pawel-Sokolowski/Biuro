from __future__ import annotations

import psycopg
from typing import Any, Dict, Optional, Set

from app.db.dao_conn import get_conn


def get_permissions(pracownik_id: Optional[int]) -> Dict[str, bool]:
    if not pracownik_id:
        return {}
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT
                MAX(COALESCE(r.can_edit_companies, 0)) AS can_edit_companies,
                MAX(COALESCE(r.can_view_invoices, 0)) AS can_view_invoices,
                MAX(COALESCE(r.can_view_finances, 0)) AS can_view_finances,
                MAX(COALESCE(r.can_export_data, 0)) AS can_export_data,
                MAX(COALESCE(r.can_access_admin_settings, 0)) AS can_access_admin_settings
            FROM pracownicy p
            JOIN role r ON r.id = p.rola_id
            WHERE p.id = %s
            """,
            (pracownik_id,),
        ).fetchone()
        return {key: bool(row[key]) for key in row.keys()} if row else {}
    finally:
        conn.close()


def is_administrator(pracownik_id: Optional[int]) -> bool:
    if not pracownik_id:
        return False
    conn = get_conn()
    try:
        # Rola należy do pracownika i nie zależy od przypisań do firm.
        row = conn.execute(
            """
            SELECT 1 FROM pracownicy p
            LEFT JOIN role r ON r.id = p.rola_id
            WHERE p.id = %s
              AND (COALESCE(p.is_admin, 0) = 1 OR LOWER(COALESCE(r.nazwa, '')) = 'admin')
            """,
            (pracownik_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def can(pracownik_id: Optional[int], permission: str) -> bool:
    return is_administrator(pracownik_id) or get_permissions(pracownik_id).get(permission, False)


TABS = [
    "Pulpit", "Widok miesięczny", "Klienci", "E-mail", "Czat",
    "Faktury", "Raporty", "Zadania", "Kalendarz", "Ustawienia",
]


def default_tabs(can_admin_settings: bool) -> list:
    return [t for t in TABS if t != "Ustawienia" or can_admin_settings]


def parse_tabs(raw: Optional[str], can_admin_settings: bool = False) -> list:
    """Lista zakładek roli; brak zapisu (NULL) oznacza ustawienia domyślne."""
    if raw is None:
        return default_tabs(can_admin_settings)
    chosen = {part.strip() for part in raw.split("|") if part.strip()}
    return [t for t in TABS if t in chosen]


def allowed_tabs(pracownik_id: Optional[int]) -> list:
    """Zakładki dostępne dla pracownika (administrator widzi wszystkie)."""
    if not pracownik_id:
        return []
    if is_administrator(pracownik_id):
        return list(TABS)
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT r.zakladki, COALESCE(r.can_access_admin_settings, 0) AS adm
            FROM pracownicy p JOIN role r ON r.id = p.rola_id
            WHERE p.id = %s
            """,
            (pracownik_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return default_tabs(False)
    return parse_tabs(row["zakladki"], bool(row["adm"]))


def assigned_company_ids(pracownik_id: Optional[int]) -> Set[int]:
    """Każdy pracownik ma dostęp do wszystkich firm; przypisanie księgowa/kadrowa służy tylko do filtrów."""
    if not pracownik_id:
        return set()
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id FROM firmy").fetchall()
        return {int(row[0]) for row in rows}
    finally:
        conn.close()
