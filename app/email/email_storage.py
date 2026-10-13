from __future__ import annotations

import sqlite3

from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn
from app.config import EMAIL_LOCAL_LIMIT


def get_account_info(konto_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM konta_email
            WHERE id = ?
            """,
            (konto_id,),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    finally:
        conn.close()

def get_email_accounts_for_employee(
    pracownik_id: int,
) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT ke.*
            FROM konta_email ke
            JOIN pracownik_konto_email pke
                ON pke.konto_id = ke.id
            WHERE pke.pracownik_id = ?
              AND COALESCE(ke.aktywne, 1) = 1
            ORDER BY ke.email
            """,
            (pracownik_id,),
        )

        return [
            dict(row)
            for row in cur.fetchall()
        ]

    finally:
        conn.close()

def save_folder(
    konto_id: int,
    server_name: str,
    display_name: Optional[str] = None,
    delimiter: Optional[str] = None,
    attributes: Optional[str] = None,
) -> int:
    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT OR IGNORE INTO foldery_email
            (
                konto_id,
                server_name,
                display_name,
                delimiter,
                attributes
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                konto_id,
                server_name,
                display_name,
                delimiter,
                attributes,
            ),
        )

        cur.execute(
            """
            SELECT id
            FROM foldery_email
            WHERE konto_id = ?
              AND server_name = ?
            """,
            (
                konto_id,
                server_name,
            ),
        )

        row = cur.fetchone()

        conn.commit()

        return int(row[0])

    finally:
        conn.close()


def get_folder_id(
    konto_id: int,
    server_name: str,
) -> Optional[int]:
    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id
            FROM foldery_email
            WHERE konto_id = ?
              AND server_name = ?
            """,
            (
                konto_id,
                server_name,
            ),
        )

        row = cur.fetchone()

        return row[0] if row else None

    finally:
        conn.close()


def get_folders_for_account(
    konto_id: int,
) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM foldery_email
            WHERE konto_id = ?
            ORDER BY display_name,
                     server_name
            """,
            (konto_id,),
        )

        return [dict(r) for r in cur.fetchall()]

    finally:
        conn.close()


def save_message_header(
    konto_id: int,
    folder_id: int,
    uid: str,
    message_id: Optional[str],
    sender: str,
    recipient: str,
    subject: str,
    date_value: str,
    is_seen: bool,
    has_attachments: bool,
) -> None:
    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO wiadomosci_email
            (
                konto_id,
                folder_id,
                uid,
                message_id,

                nadawca,
                odbiorca,
                temat,
                data,

                przeczytana,
                has_attachments
            )
            VALUES
            (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )

            ON CONFLICT(konto_id, folder_id, uid)
            DO UPDATE SET

                message_id      = excluded.message_id,
                nadawca         = excluded.nadawca,
                odbiorca        = excluded.odbiorca,
                temat           = excluded.temat,
                data            = excluded.data,

                przeczytana     = excluded.przeczytana,
                has_attachments = excluded.has_attachments,

                cached_at       = CURRENT_TIMESTAMP
            """,
            (
                konto_id,
                folder_id,
                uid,
                message_id,

                sender,
                recipient,
                subject,
                date_value,

                1 if is_seen else 0,
                1 if has_attachments else 0,
            ),
        )

        conn.commit()

    finally:
        conn.close()


def get_cached_messages(
    konto_id: int,
    folder_id: int,
) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM wiadomosci_email
            WHERE konto_id = ?
              AND folder_id = ?
              AND usunieta = 0
            ORDER BY data DESC
            """,
            (
                konto_id,
                folder_id,
            ),
        )

        return [dict(r) for r in cur.fetchall()]

    finally:
        conn.close()


def get_message_by_uid(
    konto_id: int,
    uid: str,
) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM wiadomosci_email
            WHERE konto_id = ?
              AND uid = ?
            LIMIT 1
            """,
            (
                konto_id,
                uid,
            ),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    finally:
        conn.close()


def update_message_read_flag(
    konto_id: int,
    uid: str,
    read: bool,
) -> None:
    conn = get_conn()

    try:
        conn.execute(
            """
            UPDATE wiadomosci_email
            SET przeczytana = ?
            WHERE konto_id = ?
              AND uid = ?
            """,
            (
                1 if read else 0,
                konto_id,
                uid,
            ),
        )

        conn.commit()

    finally:
        conn.close()


def mark_deleted(
    konto_id: int,
    uid: str,
) -> None:
    conn = get_conn()

    try:
        conn.execute(
            """
            UPDATE wiadomosci_email
            SET usunieta = 1
            WHERE konto_id = ?
              AND uid = ?
            """,
            (
                konto_id,
                uid,
            ),
        )

        conn.commit()

    finally:
        conn.close()


def move_local_message(
    konto_id: int,
    uid: str,
    target_folder_id: int,
) -> None:
    conn = get_conn()

    try:
        conn.execute(
            """
            UPDATE wiadomosci_email
            SET folder_id = ?
            WHERE konto_id = ?
              AND uid = ?
            """,
            (
                target_folder_id,
                konto_id,
                uid,
            ),
        )

        conn.commit()

    finally:
        conn.close()


def search_messages(
    konto_id: int,
    phrase: str,
) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        q = f"%{phrase.lower()}%"

        cur.execute(
            """
            SELECT
                we.*,
                fe.server_name,
                fe.display_name

            FROM wiadomosci_email we

            LEFT JOIN foldery_email fe
                ON fe.id = we.folder_id

            WHERE we.konto_id = ?

              AND
              (
                    lower(coalesce(we.temat,'')) LIKE ?
                 OR lower(coalesce(we.nadawca,'')) LIKE ?
                 OR lower(coalesce(we.odbiorca,'')) LIKE ?
              )

            ORDER BY we.data DESC
            """,
            (
                konto_id,
                q,
                q,
                q,
            ),
        )

        return [dict(r) for r in cur.fetchall()]

    finally:
        conn.close()


def prune_email_storage(
    konto_id: Optional[int] = None,
    limit: int = EMAIL_LOCAL_LIMIT,
) -> int:
    if limit <= 0:
        return 0

    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        if konto_id is None:
            cur.execute(
                """
                SELECT *
                FROM wiadomosci_email
                ORDER BY folder_id,
                         data DESC,
                         id DESC
                """
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM wiadomosci_email
                WHERE konto_id = ?
                ORDER BY folder_id,
                         data DESC,
                         id DESC
                """,
                (konto_id,),
            )

        rows = cur.fetchall()

        counters = {}
        to_delete = []

        for row in rows:
            key = (
                row["konto_id"],
                row["folder_id"],
            )

            counters.setdefault(key, 0)

            counters[key] += 1

            if counters[key] > limit:
                to_delete.append(
                    (row["id"],)
                )

        if to_delete:
            cur.executemany(
                """
                DELETE FROM wiadomosci_email
                WHERE id = ?
                """,
                to_delete,
            )

            conn.commit()

        return len(to_delete)

    finally:
        conn.close()
def get_cached_messages_by_folder_name(
    konto_id: int,
    server_name: str,
) -> List[Dict[str, Any]]:

    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT we.*
            FROM wiadomosci_email we
            JOIN foldery_email fe
                ON fe.id = we.folder_id
            WHERE we.konto_id = ?
              AND fe.server_name = ?
              AND we.usunieta = 0
            ORDER BY we.data DESC
            """,
            (
                konto_id,
                server_name,
            ),
        )

        return [
            dict(r)
            for r in cur.fetchall()
        ]

    finally:
        conn.close()

__all__ = [
    "get_account_info",
    "save_folder",
    "get_folder_id",
    "get_folders_for_account",
    "save_message_header",
    "get_cached_messages",
    "get_message_by_uid",
    "update_message_read_flag",
    "mark_deleted",
    "move_local_message",
    "search_messages",
    "prune_email_storage",
    "get_email_accounts_for_employee",
    "get_cached_messages_by_folder_name",
]