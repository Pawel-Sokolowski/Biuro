from __future__ import annotations

import json
from typing import Any, Optional

from app.db.dao_conn import get_conn


def record_audit(
    table: str,
    action: str,
    record_id: Optional[int] = None,
    user_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO audit_log (tabela, rekord_id, akcja, uzytkownik_id, szczegoly)
            VALUES (?, ?, ?, ?, ?)
            """,
            (table, record_id, action, user_id, json.dumps(details or {}, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def list_audit(limit: int = 500):
    conn = get_conn()
    try:
        return conn.execute(
            """
            SELECT a.*, COALESCE(p.imie || ' ' || p.nazwisko, 'System') AS uzytkownik
            FROM audit_log a
            LEFT JOIN pracownicy p ON p.id = a.uzytkownik_id
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def record_import(
    import_type: str,
    source_path: str,
    result: dict[str, Any],
    user_id: Optional[int] = None,
) -> None:
    conn = get_conn()
    try:
        warnings = result.get("warnings") or []
        conn.execute(
            """
            INSERT INTO import_history
                (import_type, source_path, created_count, updated_count,
                 warning_count, user_id, completed_at, status, details)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
            """,
            (
                import_type,
                source_path,
                int(result.get("created", 0)),
                int(result.get("updated", 0)),
                len(warnings),
                user_id,
                "completed",
                json.dumps(result, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_import_history(limit: int = 200):
    conn = get_conn()
    try:
        return conn.execute(
            """
            SELECT h.*, COALESCE(p.imie || ' ' || p.nazwisko, 'System') AS uzytkownik
            FROM import_history h
            LEFT JOIN pracownicy p ON p.id = h.user_id
            ORDER BY h.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
