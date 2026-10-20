from __future__ import annotations

import datetime
import psycopg
from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn
from app.security.authorization import assigned_company_ids, is_administrator


TASK_STATUSES = ("Nowe", "W toku", "Zakończone", "Anulowane")


def list_tasks_for_timer(
    pracownik_id: Optional[int],
    firma_id: Optional[int],
) -> List[Dict[str, Any]]:
    """Return open tasks available for a timer started from a company row."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT z.*, f.nazwa AS firma_nazwa,
                   p.imie || ' ' || p.nazwisko AS pracownik_nazwa
            FROM zadania z
            LEFT JOIN firmy f ON f.id = z.firma_id
            LEFT JOIN pracownicy p ON p.id = z.pracownik_id
            WHERE z.status IN ('Nowe', 'W toku')
              AND (
                    (%s::integer IS NOT NULL AND z.firma_id = %s)
                    OR (%s::integer IS NULL AND %s::integer IS NOT NULL AND z.pracownik_id = %s)
                  )
            ORDER BY
                CASE WHEN z.status = 'W toku' THEN 0 ELSE 1 END,
                CASE WHEN z.termin IS NULL OR z.termin = '' THEN 1 ELSE 0 END,
                z.termin ASC,
                z.updated_at DESC,
                z.id DESC
            """,
            (firma_id, firma_id, firma_id, pracownik_id, pracownik_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_tasks(
    pracownik_id: Optional[int] = None,
    firma_id: Optional[int] = None,
    status: Optional[str] = None,
    viewer_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        filters = []
        params: List[Any] = []
        if pracownik_id:
            filters.append("z.pracownik_id = %s")
            params.append(pracownik_id)
        if firma_id:
            filters.append("z.firma_id = %s")
            params.append(firma_id)
        if status:
            filters.append("z.status = %s")
            params.append(status)
        if viewer_id and not is_administrator(viewer_id):
            filters.append(
                """
                (
                    z.pracownik_id = %s
                    OR z.created_by = %s
                    OR EXISTS (
                        SELECT 1 FROM pracownik_firma pf_view
                        WHERE pf_view.pracownik_id = %s AND pf_view.firma_id = z.firma_id
                    )
                )
                """
            )
            params.extend([viewer_id, viewer_id, viewer_id])

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
    actor_id: Optional[int] = None,
) -> int:
    if not actor_id:
        raise PermissionError("Identyfikator użytkownika jest wymagany.")
    if not tytul.strip():
        raise ValueError("Tytuł zadania nie może być pusty.")
    if status not in TASK_STATUSES:
        raise ValueError(f"Nieznany status zadania: {status}")

    conn = get_conn()
    try:
        if task_id:
            if actor_id and not is_administrator(actor_id):
                if pracownik_id not in (None, actor_id):
                    raise PermissionError(
                        "Tylko administrator może przypisać zadanie innemu pracownikowi."
                    )
                if firma_id is not None and firma_id not in assigned_company_ids(actor_id):
                    raise PermissionError(
                        "Nie można przypisać zadania do nieprzypisanej firmy."
                    )
                allowed = conn.execute(
                    """
                    SELECT 1
                    FROM zadania z
                    WHERE z.id = %s
                      AND (
                        z.pracownik_id = %s
                        OR z.created_by = %s
                        OR EXISTS (
                            SELECT 1 FROM pracownik_firma pf
                            WHERE pf.pracownik_id = %s AND pf.firma_id = z.firma_id
                        )
                      )
                    """,
                    (task_id, actor_id, actor_id, actor_id),
                ).fetchone()
                if not allowed:
                    raise PermissionError("Brak uprawnień do edycji tego zadania.")
            conn.execute(
                """
                UPDATE zadania
                SET tytul = %s, opis = %s, status = %s, termin = %s,
                    firma_id = %s, pracownik_id = %s, updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
                WHERE id = %s
                """,
                (tytul.strip(), opis.strip(), status, termin, firma_id, pracownik_id, task_id),
            )
            result = task_id
        else:
            if actor_id and not is_administrator(actor_id):
                if pracownik_id not in (None, actor_id):
                    raise PermissionError(
                        "Tylko administrator może przypisać zadanie innemu pracownikowi."
                    )
                if firma_id is not None and firma_id not in assigned_company_ids(actor_id):
                    raise PermissionError(
                        "Nie można przypisać zadania do nieprzypisanej firmy."
                    )
            cur = conn.execute(
                """
                INSERT INTO zadania (
                    tytul, opis, status, termin, firma_id, pracownik_id, created_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (tytul.strip(), opis.strip(), status, termin, firma_id, pracownik_id, created_by),
            )
            result = int(cur.fetchone()[0])
        conn.commit()
        return result
    finally:
        conn.close()


def notify_company_event(
    firma_id: int, tytul: str, opis: str, everyone: bool = False
) -> List[int]:
    """Tworzy zadanie-powiadomienie o firmie. Odbiorcy: wszyscy aktywni pracownicy
    (everyone=True) albo opiekunowie firmy, a gdy ich brak - administratorzy.
    Tytuł dostaje sufiks z nazwą firmy. Zwraca id utworzonych zadań."""
    conn = get_conn()
    try:
        firma = conn.execute("SELECT nazwa FROM firmy WHERE id = %s", (firma_id,)).fetchone()
        if not firma:
            return []
        if everyone:
            rows = conn.execute(
                "SELECT id FROM pracownicy WHERE COALESCE(aktywny, 1) = 1"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT p.id FROM pracownik_firma pf
                JOIN pracownicy p ON p.id = pf.pracownik_id
                WHERE pf.firma_id = %s AND COALESCE(p.aktywny, 1) = 1
                """,
                (firma_id,),
            ).fetchall()
        if not rows and not everyone:
            rows = conn.execute(
                """
                SELECT p.id FROM pracownicy p
                LEFT JOIN role r ON r.id = p.rola_id
                WHERE COALESCE(p.aktywny, 1) = 1
                  AND (COALESCE(p.is_admin, 0) = 1 OR LOWER(COALESCE(r.nazwa, '')) = 'admin')
                """
            ).fetchall()
        today = datetime.date.today().isoformat()
        created: List[int] = []
        for row in rows:
            cur = conn.execute(
                """
                INSERT INTO zadania (tytul, opis, status, termin, firma_id, pracownik_id)
                VALUES (%s, %s, 'Nowe', %s, %s, %s) RETURNING id
                """,
                (f"{tytul}: {firma[0]}", f"{opis} ({today}).", today, firma_id, row[0]),
            )
            created.append(int(cur.fetchone()[0]))
        conn.commit()
        return created
    finally:
        conn.close()


def notify_company_resumed(firma_id: int) -> List[int]:
    return notify_company_event(
        firma_id,
        "Klient odwiesił działalność gospodarczą",
        "CEIDG pokazuje, że działalność firmy została odwieszona",
    )


def notify_company_suspended(firma_id: int) -> List[int]:
    return notify_company_event(
        firma_id,
        "Klient zawiesił działalność gospodarczą",
        "CEIDG pokazuje, że działalność firmy została zawieszona",
    )


def notify_services_changed(firma_id: int, suspended: bool) -> List[int]:
    if suspended:
        return notify_company_event(
            firma_id, "ZAWIESZONE USŁUGI", "Biuro zawiesiło wykonywanie usług dla firmy", everyone=True
        )
    return notify_company_event(
        firma_id, "Wznowione usługi", "Biuro wznowiło wykonywanie usług dla firmy", everyone=True
    )


def delete_task(task_id: int, actor_id: Optional[int] = None) -> None:
    if not actor_id:
        raise PermissionError("Identyfikator użytkownika jest wymagany.")
    conn = get_conn()
    try:
        if actor_id and not is_administrator(actor_id):
            allowed = conn.execute(
                """
                SELECT 1
                FROM zadania z
                WHERE z.id = %s
                  AND (
                    z.pracownik_id = %s
                    OR z.created_by = %s
                    OR EXISTS (
                        SELECT 1 FROM pracownik_firma pf
                        WHERE pf.pracownik_id = %s AND pf.firma_id = z.firma_id
                    )
                  )
                """,
                (task_id, actor_id, actor_id, actor_id),
            ).fetchone()
            if not allowed:
                raise PermissionError("Brak uprawnień do usunięcia tego zadania.")
        conn.execute("DELETE FROM zadania WHERE id = %s", (task_id,))
        conn.commit()
    finally:
        conn.close()
