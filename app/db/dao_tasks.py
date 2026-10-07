from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn


TASK_STATUSES = ("Nowe", "W toku", "Zakończone", "Anulowane")


def list_tasks(
    pracownik_id: Optional[int] = None,
    firma_id: Optional[int] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        filters = []
        params: List[Any] = []
        if pracownik_id:
            filters.append("z.pracownik_id = ?")
            params.append(pracownik_id)
        if firma_id:
            filters.append("z.firma_id = ?")
            params.append(firma_id)
        if status:
            filters.append("z.status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = conn.execute(
            f"""
            SELECT z.*, f.nazwa AS firma_nazwa,
                   p.imie || ' ' || p.nazwisko AS pracownik_nazwa
            FROM zadania z
            LEFT JOIN firmy f ON f.id = z.firma_id
            LEFT JOIN pracownicy p ON p.id = z.pracownik_id
            {where}
            ORDER BY
                CASE WHEN z.status = 'Zakończone' THEN 1 ELSE 0 END,
                CASE WHEN z.termin IS NULL OR z.termin = '' THEN 1 ELSE 0 END,
                z.termin ASC,
                z.updated_at DESC,
                z.id DESC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def save_task(
    tytul: str,
    opis: str = "",
    status: str = "Nowe",
    termin: Optional[str] = None,
    firma_id: Optional[int] = None,
    pracownik_id: Optional[int] = None,
    created_by: Optional[int] = None,
    task_id: Optional[int] = None,
) -> int:
    if not tytul.strip():
        raise ValueError("Tytuł zadania nie może być pusty.")
    if status not in TASK_STATUSES:
        raise ValueError(f"Nieznany status zadania: {status}")

    conn = get_conn()
    try:
        if task_id:
            conn.execute(
                """
                UPDATE zadania
                SET tytul = ?, opis = ?, status = ?, termin = ?,
                    firma_id = ?, pracownik_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (tytul.strip(), opis.strip(), status, termin, firma_id, pracownik_id, task_id),
            )
            result = task_id
        else:
            cur = conn.execute(
                """
                INSERT INTO zadania (
                    tytul, opis, status, termin, firma_id, pracownik_id, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tytul.strip(), opis.strip(), status, termin, firma_id, pracownik_id, created_by),
            )
            result = int(cur.lastrowid)
        conn.commit()
        return result
    finally:
        conn.close()


def delete_task(task_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM zadania WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()
