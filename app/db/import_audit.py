"""Historia importów: jedno miejsce zapisu, wspólne dla wszystkich źródeł.

Każdy import (Enova, Płatnik, e-ZLA, pliki, raporty) zostawia jeden wpis z typem,
źródłem, licznikami i statusem, żeby w panelu audytu było widać, skąd wzięły się dane.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional


def _count(details, key):
    value = (details or {}).get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def log_import(conn, import_type: str, *, source: Optional[str] = None,
               status: str = 'completed', details: Any = None,
               created: int = 0, updated: int = 0, warnings: Iterable = (),
               user_id: Optional[int] = None) -> None:
    """Zapisuje wpis importu na przekazanym połączeniu (w tej samej transakcji)."""
    if isinstance(details, (dict, list)):
        payload = details
        created = created or _count(payload, 'created')
        updated = updated or _count(payload, 'updated')
    else:
        payload = {'message': str(details)} if details else {}
    warnings = list(warnings or payload.get('warnings') or ())
    conn.execute(
        'INSERT INTO import_history(import_type, source_path, created_count, updated_count, '
        'warning_count, user_id, completed_at, status, details) '
        "VALUES (%s, %s, %s, %s, %s, %s, to_char(now(), 'YYYY-MM-DD HH24:MI:SS'), %s, %s)",
        (import_type, source, int(created), int(updated), len(warnings), user_id, status,
         json.dumps(payload, ensure_ascii=False, default=str)),
    )


def log_import_safely(import_type: str, *, source: Optional[str] = None,
                      status: str = 'completed', details: Any = None,
                      created: int = 0, updated: int = 0, warnings: Iterable = (),
                      user_id: Optional[int] = None) -> None:
    """Wpis historii nie może przewrócić importu — błąd zapisu jest tylko pomijany."""
    from app.db.dao_conn import get_conn
    try:
        with get_conn() as conn:
            log_import(conn, import_type, source=source, status=status, details=details,
                       created=created, updated=updated, warnings=warnings, user_id=user_id)
    except Exception:
        pass


def summary(limit: int = 200) -> list[Dict[str, Any]]:
    """Ostatnie importy z nazwą użytkownika, do panelu audytu."""
    from app.db.dao_audit import list_import_history
    return list_import_history(limit)
