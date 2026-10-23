from __future__ import annotations

import psycopg

from typing import Any, Dict, List, Optional, Tuple

from app.db.dao_conn import get_conn
from app.config import EMAIL_LOCAL_LIMIT
from app.email.email_helpers import folder_role


def get_account_info(konto_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM konta_email
            WHERE id = %s
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

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT ke.*
            FROM konta_email ke
            JOIN pracownik_konto_email pke
                ON pke.konto_id = ke.id
            WHERE pke.pracownik_id = %s
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
    try:
        rows = conn.execute(
            """
            SELECT rf.*, ke.email AS konto_email
            FROM reguly_filtrow rf
            LEFT JOIN konta_email ke ON ke.id = rf.konto_id
            WHERE rf.firma_id = %s
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
            "SELECT pracownik_id FROM konta_email WHERE id = %s", (konto_id,)
        ).fetchone()
        pracownik_id = row[0] if row and row[0] is not None else None
        cur = conn.execute(
            """
            INSERT INTO reguly_filtrow (
                pracownik_id, konto_id, firma_id, od, "do", nadawca_zawiera,
                temat_zawiera, tresc_zawiera, folder_docelowy,
                oznacz_jako_przeczytane, usun, aktywna
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                pracownik_id, konto_id, firma_id, od, do, nadawca_zawiera,
                temat_zawiera, tresc_zawiera, folder_docelowy,
                int(oznacz_jako_przeczytane), int(usun), int(aktywna),
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        conn.close()

def delete_missing_folders(
    konto_id: int,
    valid_names: set[str],
) -> None:
    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT server_name
            FROM foldery_email
            WHERE konto_id = %s
            """,
            (konto_id,),
        )

        existing = {row[0] for row in cur.fetchall()}
        missing = existing - valid_names

        if missing:
            cur.execute(
                """
                DELETE FROM foldery_email
                WHERE konto_id = %s
                  AND server_name = ANY(%s)
                """,
                (
                    konto_id,
                    list(missing),
                ),
            )

        conn.commit()

    finally:
        conn.close()

def delete_email_filter_for_company(filter_id: int, firma_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM reguly_filtrow WHERE id = %s AND firma_id = %s",
            (filter_id, firma_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_known_sender_addresses(konto_id: Optional[int] = None) -> List[str]:
    """Unikalne adresy nadawców z wiadomości w skrzynce (opcjonalnie jednego konta)."""
    from email.utils import parseaddr

    conn = get_conn()
    try:
        sql = "SELECT DISTINCT nadawca FROM wiadomosci_email WHERE nadawca IS NOT NULL AND nadawca <> ''"
        params: tuple = ()
        if konto_id is not None:
            sql += " AND konto_id = %s"
            params = (int(konto_id),)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    addresses = {
        parseaddr(r[0])[1].strip().lower()
        for r in rows
        if "@" in parseaddr(r[0])[1]
    }
    return sorted(addresses)


def ensure_contact_email_filters(firma_id: int, folder_name: str) -> None:
    """Create a filter for each company contact e-mail that has none yet."""
    conn = get_conn()
    try:
        contacts = conn.execute(
            "SELECT email FROM company_contacts WHERE firma_id = %s", (firma_id,)
        ).fetchall()
        account = conn.execute(
            "SELECT id FROM konta_email WHERE COALESCE(aktywne, 1) = 1 AND COALESCE(systemowe, 0) = 0 ORDER BY id LIMIT 1"
        ).fetchone()
        if not account:
            return
        existing = {
            (r[0] or "").strip().lower()
            for r in conn.execute(
                "SELECT od FROM reguly_filtrow WHERE firma_id = %s", (firma_id,)
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
            INSERT INTO foldery_email
            (
                konto_id,
                server_name,
                display_name,
                delimiter,
                attributes
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(konto_id, server_name) DO UPDATE SET
                display_name = COALESCE(excluded.display_name, foldery_email.display_name),
                delimiter    = COALESCE(excluded.delimiter, foldery_email.delimiter),
                attributes   = COALESCE(excluded.attributes, foldery_email.attributes)
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
            WHERE konto_id = %s
              AND server_name = %s
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
            WHERE konto_id = %s
              AND server_name = %s
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

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM foldery_email
            WHERE konto_id = %s
            ORDER BY display_name,
                     server_name
            """,
            (konto_id,),
        )

        return [dict(r) for r in cur.fetchall()]

    finally:
        conn.close()


def account_has_headers(konto_id: int) -> bool:
    """Czy konto ma już zapisane foldery i nagłówki (np. z gotowej, wypełnionej bazy)."""
    conn = get_conn()
    try:
        has_folders = conn.execute(
            "SELECT 1 FROM foldery_email WHERE konto_id = %s LIMIT 1", (konto_id,)
        ).fetchone()
        if not has_folders:
            return False
        return conn.execute(
            "SELECT 1 FROM wiadomosci_email WHERE konto_id = %s LIMIT 1", (konto_id,)
        ).fetchone() is not None
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
    refs: Optional[str] = None,
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
                has_attachments,
                refs
            )
            VALUES
            (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s
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
                refs            = COALESCE(excluded.refs, wiadomosci_email.refs),

                cached_at       = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
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
                refs,
            ),
        )

        conn.commit()

    finally:
        conn.close()


def save_message_headers(
    konto_id: int,
    folder_id: int,
    headers: List[Dict[str, Any]],
) -> None:
    """Zapisuje wiele nagłówków w jednej transakcji (znacznie szybciej niż pojedynczo)."""
    if not headers:
        return
    conn = get_conn()
    try:
        conn.cursor().executemany(
            """
            INSERT INTO wiadomosci_email
                (konto_id, folder_id, uid, message_id, nadawca, odbiorca,
                 temat, data, przeczytana, has_attachments, refs)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(konto_id, folder_id, uid) DO UPDATE SET
                message_id      = excluded.message_id,
                nadawca         = excluded.nadawca,
                odbiorca        = excluded.odbiorca,
                temat           = excluded.temat,
                data            = excluded.data,
                przeczytana     = excluded.przeczytana,
                has_attachments = excluded.has_attachments,
                refs            = COALESCE(excluded.refs, wiadomosci_email.refs),
                cached_at       = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
            """,
            [
                (
                    konto_id,
                    folder_id,
                    h["uid"],
                    h.get("message_id"),
                    h.get("sender"),
                    h.get("recipient"),
                    h.get("subject"),
                    h.get("date"),
                    1 if h.get("is_seen") else 0,
                    1 if h.get("has_attachments") else 0,
                    h.get("refs"),
                )
                for h in headers
            ],
        )
        conn.commit()
    finally:
        conn.close()


def get_uids_missing_refs(konto_id: int, folder_id: int, limit: int = 400) -> List[str]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT uid FROM wiadomosci_email WHERE konto_id = %s AND folder_id = %s "
            "AND refs IS NULL AND usunieta = 0 ORDER BY data DESC LIMIT %s",
            (konto_id, folder_id, limit),
        ).fetchall()
        return [str(r["uid"] if hasattr(r, "keys") else r[0]) for r in rows]
    finally:
        conn.close()


def save_refs(konto_id: int, folder_id: int, updates: List[Tuple[str, str]]) -> None:
    if not updates:
        return
    conn = get_conn()
    try:
        conn.cursor().executemany(
            "UPDATE wiadomosci_email SET refs = %s WHERE konto_id = %s AND folder_id = %s AND uid = %s",
            [(refs, konto_id, folder_id, uid) for uid, refs in updates],
        )
        conn.commit()
    finally:
        conn.close()


def get_known_uids(konto_id: int, folder_id: int) -> set:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT uid FROM wiadomosci_email WHERE konto_id = %s AND folder_id = %s",
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

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM wiadomosci_email
            WHERE konto_id = %s
              AND folder_id = %s
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


def get_unread_counts(konto_id: int) -> Dict[int, int]:

    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                folder_id,
                COUNT(*)
            FROM wiadomosci_email
            WHERE konto_id = %s
              AND COALESCE(usunieta, 0) = 0
              AND COALESCE(przeczytana, 0) = 0
            GROUP BY folder_id
            """,
            (konto_id,),
        )

        rows = cur.fetchall()

        return {
            int(row["folder_id"]): int(row["count"])
            for row in rows
        }

    finally:
        conn.close()

def find_special_folder(konto_id: int, role: str) -> Optional[str]:
    """Zwraca surową nazwę serwerową folderu o danej roli (archive/junk/trash/sent/drafts)."""
    for folder in get_folders_for_account(konto_id):
        if "\\noselect" in (folder.get("attributes") or "").lower():
            continue
        if folder_role(
            folder["server_name"], folder.get("attributes"), folder.get("delimiter")
        ) == role:
            return folder["server_name"]
    return None


def get_message_by_uid(
    konto_id: int,
    uid: str,
    folder_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM wiadomosci_email
            WHERE konto_id = %s
              AND uid = %s
              AND (%s::integer IS NULL OR folder_id = %s)
            LIMIT 1
            """,
            (
                konto_id,
                uid,
                folder_id,
                folder_id,
            ),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    finally:
        conn.close()

def save_raw_message(
    message_row_id: int,
    raw_eml: bytes,
) -> None:

    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO email_raw_messages
            (
                wiadomosc_id,
                raw_eml,
                size_bytes
            )
            VALUES
            (
                %s,
                %s,
                %s
            )

            ON CONFLICT (wiadomosc_id)
            DO UPDATE SET
                raw_eml = EXCLUDED.raw_eml,
                size_bytes = EXCLUDED.size_bytes,
                downloaded_at = to_char(
                    now(),
                    'YYYY-MM-DD HH24:MI:SS'
                )
            """,
            (
                message_row_id,
                raw_eml,
                len(raw_eml),
            ),
        )

        conn.commit()

    finally:
        conn.close()

def load_raw_message(
    message_row_id: int,
) -> Optional[bytes]:

    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT raw_eml
            FROM email_raw_messages
            WHERE wiadomosc_id = %s
            """,
            (message_row_id,),
        )

        row = cur.fetchone()

        if not row:
            return None

        return row[0]

    finally:
        conn.close()

def get_message_row_id(
    konto_id: int,
    folder_id: int,
    uid: str,
) -> Optional[int]:

    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id
            FROM wiadomosci_email
            WHERE konto_id = %s
              AND folder_id = %s
              AND uid = %s
            LIMIT 1
            """,
            (
                konto_id,
                folder_id,
                uid,
            ),
        )

        row = cur.fetchone()

        return int(row[0]) if row else None

    finally:
        conn.close()

def update_message_read_flag(
    konto_id: int,
    uid: str,
    read: bool,
    folder_id: Optional[int] = None,
) -> None:
    conn = get_conn()

    try:
        conn.execute(
            """
            UPDATE wiadomosci_email
            SET przeczytana = %s
            WHERE konto_id = %s
              AND uid = %s
              AND (%s::integer IS NULL OR folder_id = %s)
            """,
            (
                1 if read else 0,
                konto_id,
                uid,
                folder_id,
                folder_id,
            ),
        )

        conn.commit()

    finally:
        conn.close()

def mark_deleted(
    konto_id: int,
    uid: str,
    folder_id: Optional[int] = None,
) -> None:
    conn = get_conn()

    try:
        conn.execute(
            """
            UPDATE wiadomosci_email
            SET usunieta = 1
            WHERE konto_id = %s
              AND uid = %s
              AND (%s::integer IS NULL OR folder_id = %s)
            """,
            (
                konto_id,
                uid,
                folder_id,
                folder_id,
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
            SET folder_id = %s
            WHERE konto_id = %s
              AND uid = %s
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

            WHERE we.konto_id = %s

              AND
              (
                    lower(coalesce(we.temat,'')) ILIKE %s
                 OR lower(coalesce(we.nadawca,'')) ILIKE %s
                 OR lower(coalesce(we.odbiorca,'')) ILIKE %s
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
                WHERE konto_id = %s
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
                WHERE id = %s
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

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT we.*
            FROM wiadomosci_email we
            JOIN foldery_email fe
                ON fe.id = we.folder_id
            WHERE we.konto_id = %s
              AND fe.server_name = %s
              AND we.usunieta = 0
            ORDER BY we.data DESC
            """,
            (
                konto_id,
                server_name,
            ),
        )

        rows = cur.fetchall()

        print(
            "DEBUG MAILS:",
            konto_id,
            server_name,
            len(rows),
        )

        for row in rows:
            print("ROW:", dict(row))

        return [
            dict(r)
            for r in rows
        ]

    finally:
        conn.close()

_SAME_MESSAGE = (
    "(w2.id = we.id OR (we.message_id IS NOT NULL AND we.message_id <> '' "
    "AND w2.message_id = we.message_id))"
)


def apply_server_state(
    konto_id: int,
    folder_id: int,
    all_uids: set,
    unseen_uids: Optional[set],
    deleted_cutoff: str,
) -> None:
    """Przenosi stan serwera do bazy: flagi przeczytania oraz wiadomo?ci usuni?te/przeniesione poza programem."""
    conn = get_conn()
    try:
        base = "konto_id = %s AND folder_id = %s"
        if unseen_uids is not None:
            conn.execute(
                f"UPDATE wiadomosci_email SET przeczytana = 0 WHERE {base} "
                "AND uid = ANY(%s) AND COALESCE(przeczytana, 0) <> 0",
                (konto_id, folder_id, list(unseen_uids)),
            )
            conn.execute(
                f"UPDATE wiadomosci_email SET przeczytana = 1 WHERE {base} "
                "AND uid = ANY(%s) AND COALESCE(przeczytana, 0) <> 1",
                (konto_id, folder_id, list(all_uids - unseen_uids)),
            )
        conn.execute(
            f"UPDATE wiadomosci_email SET usunieta = 1 WHERE {base} "
            "AND usunieta = 0 AND data >= %s AND NOT (uid = ANY(%s))",
            (konto_id, folder_id, deleted_cutoff, list(all_uids)),
        )
        conn.commit()
    finally:
        conn.close()


def get_assignment_map(row_ids: List[int]) -> Dict[int, List[int]]:
    """{id wiadomo?ci: [id pracownik?w]}; przypisanie pod??a za wiadomo?ci? po Message-ID (np. po przeniesieniu)."""
    if not row_ids:
        return {}
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT we.id, a.employee_id
            FROM wiadomosci_email we
            JOIN wiadomosci_email w2 ON w2.konto_id = we.konto_id AND {_SAME_MESSAGE}
            JOIN email_message_assignments a ON a.message_id = w2.id
            WHERE we.id = ANY(%s) AND a.employee_id IS NOT NULL
            ORDER BY a.employee_id
            """,
            (list(row_ids),),
        ).fetchall()
        result: Dict[int, List[int]] = {}
        for rid, emp in rows:
            result.setdefault(int(rid), []).append(int(emp))
        return result
    finally:
        conn.close()


def set_employee_assignment(row_ids: List[int], employee_id: int, assigned: bool) -> None:
    if not row_ids:
        return
    conn = get_conn()
    try:
        if assigned:
            conn.execute(
                f"""
                INSERT INTO email_message_assignments (message_id, employee_id)
                SELECT we.id, %s FROM wiadomosci_email we
                WHERE we.id = ANY(%s) AND NOT EXISTS (
                    SELECT 1 FROM wiadomosci_email w2
                    JOIN email_message_assignments a ON a.message_id = w2.id AND a.employee_id = %s
                    WHERE w2.konto_id = we.konto_id AND {_SAME_MESSAGE})
                """,
                (employee_id, list(row_ids), employee_id),
            )
        else:
            conn.execute(
                f"""
                DELETE FROM email_message_assignments a
                USING wiadomosci_email w2, wiadomosci_email we
                WHERE a.message_id = w2.id AND a.employee_id = %s
                  AND we.id = ANY(%s) AND w2.konto_id = we.konto_id AND {_SAME_MESSAGE}
                """,
                (employee_id, list(row_ids)),
            )
        conn.commit()
    finally:
        conn.close()


def get_completed_map(row_ids: List[int]) -> set[int]:
    if not row_ids:
        return set()
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT we.id
            FROM wiadomosci_email we
            JOIN wiadomosci_email w2 ON w2.konto_id = we.konto_id AND {_SAME_MESSAGE}
            WHERE we.id = ANY(%s) AND COALESCE(w2.email_done, 0) = 1
            """,
            (list(row_ids),),
        ).fetchall()
        return {int(row[0]) for row in rows}
    finally:
        conn.close()


def set_messages_completed(row_ids: List[int], completed: bool) -> None:
    if not row_ids:
        return
    conn = get_conn()
    try:
        conn.execute(
            f"""
            UPDATE wiadomosci_email w2
            SET email_done = %s
            FROM wiadomosci_email we
            WHERE we.id = ANY(%s) AND w2.konto_id = we.konto_id
              AND {_SAME_MESSAGE}
            """,
            (int(completed), list(row_ids)),
        )
        conn.commit()
    finally:
        conn.close()


def count_completed_messages(konto_id: int) -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM wiadomosci_email we
            WHERE we.konto_id = %s AND we.usunieta = 0 AND EXISTS (
                SELECT 1
                FROM wiadomosci_email w2
                WHERE w2.konto_id = we.konto_id AND {_SAME_MESSAGE}
                  AND COALESCE(w2.email_done, 0) = 1
            )
            """,
            (konto_id,),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def list_completed_rows(konto_id: int, limit: int = 500) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT we.*, fe.server_name
            FROM wiadomosci_email we
            JOIN foldery_email fe ON fe.id = we.folder_id
            WHERE we.konto_id = %s AND we.usunieta = 0 AND EXISTS (
                SELECT 1
                FROM wiadomosci_email w2
                WHERE w2.konto_id = we.konto_id AND {_SAME_MESSAGE}
                  AND COALESCE(w2.email_done, 0) = 1
            )
            ORDER BY we.data DESC
            LIMIT %s
            """,
            (konto_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_assigned_by_employee(konto_id: int) -> Dict[int, int]:
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT a.employee_id, COUNT(DISTINCT we.id)
            FROM wiadomosci_email we
            JOIN wiadomosci_email w2 ON w2.konto_id = we.konto_id AND {_SAME_MESSAGE}
            JOIN email_message_assignments a ON a.message_id = w2.id
            WHERE we.konto_id = %s AND we.usunieta = 0 AND a.employee_id IS NOT NULL
            GROUP BY a.employee_id
            """,
            (konto_id,),
        ).fetchall()
        return {int(r[0]): int(r[1]) for r in rows}
    finally:
        conn.close()


def list_assigned_rows(konto_id: int, employee_id: int, limit: int = 500) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT we.*, fe.server_name
            FROM wiadomosci_email we
            JOIN foldery_email fe ON fe.id = we.folder_id
            WHERE we.konto_id = %s AND we.usunieta = 0 AND EXISTS (
                SELECT 1 FROM wiadomosci_email w2
                JOIN email_message_assignments a ON a.message_id = w2.id AND a.employee_id = %s
                WHERE w2.konto_id = we.konto_id AND {_SAME_MESSAGE})
            ORDER BY we.data DESC
            LIMIT %s
            """,
            (konto_id, employee_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def refresh_email_list(
    konto_id: int,
    server_folder: str,
) -> List[Dict[str, Any]]:

    rows = get_cached_messages_by_folder_name(
        konto_id,
        server_folder,
    )

    print(
        "REFRESH_EMAIL_LIST",
        konto_id,
        server_folder,
        len(rows),
    )

    return rows

__all__ = [
    "apply_server_state",
    "get_assignment_map",
    "set_employee_assignment",
    "get_completed_map",
    "set_messages_completed",
    "count_completed_messages",
    "list_completed_rows",
    "count_assigned_by_employee",
    "list_assigned_rows",
    "get_unread_counts",
    "save_message_headers",
    "find_special_folder",
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


def rename_folder_local(konto_id: int, old: str, new: str) -> int:
    """Zmienia nazw? folderu (i podfolder?w) w bazie po zmianie na serwerze; wiadomo?ci zostaj? przy folderze."""
    from app.email.email_helpers import decode_mailbox_utf7

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, server_name, delimiter FROM foldery_email WHERE konto_id = %s",
            (konto_id,),
        ).fetchall()
        changed = 0
        for row in rows:
            name, delim = row["server_name"], row["delimiter"] or ""
            if name == old:
                target = new
            elif delim and name.startswith(old + delim):
                target = new + name[len(old):]
            else:
                continue
            conn.execute(
                "UPDATE foldery_email SET server_name = %s, display_name = %s WHERE id = %s",
                (target, decode_mailbox_utf7(target), row["id"]),
            )
            conn.execute(
                "UPDATE reguly_filtrow SET folder_docelowy = %s WHERE konto_id = %s AND folder_docelowy = %s",
                (target, konto_id, name),
            )
            changed += 1
        conn.commit()
        return changed
    finally:
        conn.close()
