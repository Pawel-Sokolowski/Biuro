from __future__ import annotations

from typing import Optional

from app.db.dao_conn import get_conn


def assign_message(message_id: int, employee_id: Optional[int], company_id: Optional[int]) -> None:
    if employee_id is None and company_id is None:
        return
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO email_message_assignments
                (message_id, employee_id, company_id)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (message_id, employee_id, company_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_assigned_messages(
    konto_id: int,
    employee_id: Optional[int] = None,
    company_id: Optional[int] = None,
    limit: int = 300,
):
    conn = get_conn()
    try:
        return conn.execute(
            """
            SELECT w.id, w.uid, w.konto_id, w.nadawca, w.odbiorca, w.temat,
                   w.data, w.przeczytana, f.server_name AS folder
            FROM wiadomosci_email w
            JOIN email_message_assignments a ON a.message_id = w.id
            JOIN foldery_email f ON f.id = w.folder_id
            WHERE w.konto_id = %s
              AND w.usunieta = 0
              AND (%s::integer IS NULL OR a.employee_id = %s)
              AND (%s::integer IS NULL OR a.company_id = %s)
            ORDER BY w.data DESC, w.id DESC
            LIMIT %s
            """,
            (konto_id, employee_id, employee_id, company_id, company_id, limit),
        ).fetchall()
    finally:
        conn.close()
