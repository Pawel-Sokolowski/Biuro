from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional, Set

from app.db.dao_conn import get_conn


def get_permissions(pracownik_id: Optional[int]) -> Dict[str, bool]:
    if not pracownik_id:
        return {}
    conn = get_conn()
    conn.row_factory = sqlite3.Row
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
            WHERE p.id = ?
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
    conn.row_factory = sqlite3.Row
    try:
        # Rola należy do pracownika i nie zależy od przypisań do firm.
        row = conn.execute(
            """
            SELECT 1 FROM pracownicy p
            LEFT JOIN role r ON r.id = p.rola_id
            WHERE p.id = ?
              AND (COALESCE(p.is_admin, 0) = 1 OR LOWER(COALESCE(r.nazwa, '')) = 'admin')
            """,
            (pracownik_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def can(pracownik_id: Optional[int], permission: str) -> bool:
    return is_administrator(pracownik_id) or get_permissions(pracownik_id).get(permission, False)


def assigned_company_ids(pracownik_id: Optional[int]) -> Set[int]:
    if not pracownik_id:
        return set()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT firma_id FROM pracownik_firma WHERE pracownik_id = ?",
            (pracownik_id,),
        ).fetchall()
        return {int(row[0]) for row in rows if row[0] is not None}
    finally:
        conn.close()
