from __future__ import annotations

import sqlite3

from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn
from app.config import EMAIL_LOCAL_LIMIT
from app.email.email_helpers import denormalize_folder_name


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
              AND COALESCE(ke.systemowe, 0) = 0
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

def get_all_email_accounts() -> List[Dict[str, Any]]:
    from app.db.dao_employees import get_all_email_accounts as _get_all

    return _get_all()


def get_email_filters_for_company(firma_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT rf.*, ke.email AS konto_email
            FROM reguly_filtrow rf
            LEFT JOIN konta_email ke ON ke.id = rf.konto_id
            WHERE rf.firma_id = ?
            ORDER BY rf.id
            """,
            (firma_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_email_filter(
    konto_id: int,
    firma_id: Optional[int],
    od: Optional[str],
    do: Optional[str],
    nadawca_zawiera: Optional[str],
    temat_zawiera: Optional[str],
    tresc_zawiera: Optional[str],
    folder_docelowy: str,
    oznacz_jako_przeczytane: int = 0,
    usun: int = 0,
    aktywna: int = 1,
) -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT pracownik_id FROM konta_email WHERE id = ?", (konto_id,)
        ).fetchone()
        pracownik_id = row[0] if row and row[0] is not None else 0
        cur = conn.execute(
            """
            INSERT INTO reguly_filtrow (
                pracownik_id, konto_id, firma_id, od, do, nadawca_zawiera,
                temat_zawiera, tresc_zawiera, folder_docelowy,
                oznacz_jako_przeczytane, usun, aktywna
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pracownik_id, konto_id, firma_id, od, do, nadawca_zawiera,
                temat_zawiera, tresc_zawiera, folder_docelowy,
                int(oznacz_jako_przeczytane), int(usun), int(aktywna),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def delete_email_filter_for_company(filter_id: int, firma_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM reguly_filtrow WHERE id = ? AND firma_id = ?",
            (filter_id, firma_id),
        )
        conn.commit()
    finally:
        conn.close()


def ensure_contact_email_filters(firma_id: int, folder_name: str) -> None:
    """Create a filter for each company contact e-mail that has none yet."""
    conn = get_conn()
    try:
        contacts = conn.execute(
            "SELECT email FROM company_contacts WHERE firma_id = ?", (firma_id,)
        ).fetchall()
        account = conn.execute(
            "SELECT id FROM konta_email WHERE COALESCE(aktywne, 1) = 1 AND COALESCE(systemowe, 0) = 0 ORDER BY id LIMIT 1"
        ).fetchone()
        if not account:
            return
        existing = {
            (r[0] or "").strip().lower()
            for r in conn.execute(
                "SELECT od FROM reguly_filtrow WHERE firma_id = ?", (firma_id,)
            )
        }
    finally:
        conn.close()

    for (email,) in contacts:
        email = (email or "").strip()
        if "@" in email and email.lower() not in existing:
            add_email_filter(
                account[0], firma_id, email, None, None, None, None, folder_name
            )
            existing.add(email.lower())


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


def get_known_uids(konto_id: int, folder_id: int) -> set:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT uid FROM wiadomosci_email WHERE konto_id = ? AND folder_id = ?",
            (konto_id, folder_id),
        ).fetchall()
        return {str(r[0]) for r in rows}
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

def refresh_email_list(konto_id: int, local_folder: str) -> List[Dict[str, Any]]:
    return get_cached_messages_by_folder_name(
        konto_id, denormalize_folder_name(local_folder)
    )


__all__ = [
    "refresh_email_list",
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