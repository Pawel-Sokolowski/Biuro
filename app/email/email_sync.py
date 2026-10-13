from __future__ import annotations

import email
from app.email.email_helpers import safe_header, normalize_folder_name
from app.email.email_storage import (
    save_folder,
    save_message_header,
    get_folder_id,
    get_email_accounts_for_employee,
)
import imaplib
import logging
import socket
import sqlite3

from email.message import Message
from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn
from app.email.email_helpers import (
    parse_mailbox_name,
)

logger = logging.getLogger(__name__)


def _decrypt_password(haslo_enc: Optional[str]) -> str:
    return haslo_enc or ""


def _get_account_info(konto_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                id,
                email,
                serwer_imap,
                port_imap,
                ssl_imap,
                haslo_enc
            FROM konta_email
            WHERE id = ?
            """,
            (konto_id,),
        )

        row = cur.fetchone()

        return dict(row) if row else None

    finally:
        conn.close()


def _connect_imap(
    account: Dict[str, Any],
    timeout: int = 30,
) -> Optional[imaplib.IMAP4]:
    host = account.get("serwer_imap")
    port = account.get("port_imap")
    ssl_flag = account.get("ssl_imap")
    login = account.get("email")
    password = _decrypt_password(account.get("haslo_enc"))

    if not host or not login:
        return None

    socket.setdefaulttimeout(timeout)

    try:
        use_ssl = str(ssl_flag).lower() in (
            "1",
            "true",
            "yes",
            "ssl",
            "ssl/tls",
        )

        if use_ssl:
            imap_conn = (
                imaplib.IMAP4_SSL(host, int(port))
                if port
                else imaplib.IMAP4_SSL(host)
            )
        else:
            imap_conn = (
                imaplib.IMAP4(host, int(port))
                if port
                else imaplib.IMAP4(host)
            )

        imap_conn.login(login, password)

        return imap_conn

    except Exception:
        logger.exception(
            "IMAP login failed for %s",
            login,
        )
        return None


def _select_folder(
    imap_conn: imaplib.IMAP4,
    folder: str,
    readonly: bool = True,
) -> bool:
    try:
        typ, _ = imap_conn.select(
            folder,
            readonly=readonly,
        )

        if typ == "OK":
            return True

    except Exception:
        pass

    try:
        typ, _ = imap_conn.select(
            f'"{folder}"',
            readonly=readonly,
        )

        return typ == "OK"

    except Exception:
        return False


def _supports_move(
    imap_conn: imaplib.IMAP4,
) -> bool:
    try:
        typ, caps = imap_conn.capability()

        if typ != "OK":
            return False

        caps_str = (
            b" ".join(caps)
            .decode(errors="replace")
            .upper()
        )

        return "MOVE" in caps_str

    except Exception:
        return False


def list_folders(
    konto_id: int,
    timeout: int = 30,
) -> List[str]:
    account = _get_account_info(konto_id)

    if not account:
        return []

    imap_conn = _connect_imap(
        account,
        timeout,
    )

    if not imap_conn:
        return []

    try:
        typ, folders = imap_conn.list()

        if typ != "OK" or not folders:
            return []

        result: List[str] = []

        for folder in folders:
            try:
                name = parse_mailbox_name(folder)

                if name:
                    result.append(name)

            except Exception:
                logger.exception(
                    "Failed parsing mailbox"
                )

        return sorted(set(result))

    finally:
        try:
            imap_conn.logout()
        except Exception:
            pass


def fetch_message_from_imap(
    konto_id: int,
    folder: str,
    uid: str,
    timeout: int = 30,
) -> Optional[Message]:
    account = _get_account_info(konto_id)

    if not account:
        return None

    imap_conn = _connect_imap(
        account,
        timeout,
    )

    if not imap_conn:
        return None

    try:
        if not _select_folder(
            imap_conn,
            folder,
            readonly=True,
        ):
            return None

        typ, data = imap_conn.uid(
            "FETCH",
            str(uid),
            "(RFC822)",
        )

        if typ != "OK":
            return None

        raw_bytes = None

        for item in data:
            if (
                isinstance(item, tuple)
                and len(item) > 1
                and isinstance(
                    item[1],
                    (bytes, bytearray),
                )
            ):
                raw_bytes = item[1]
                break

        if not raw_bytes:
            return None

        return email.message_from_bytes(raw_bytes)

    except Exception:
        logger.exception(
            "FETCH failed for uid=%s",
            uid,
        )

        return None

    finally:
        try:
            imap_conn.logout()
        except Exception:
            pass


def fetch_headers(
    konto_id: int,
    folder: str,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    account = _get_account_info(konto_id)

    if not account:
        return []

    imap_conn = _connect_imap(
        account,
        timeout,
    )

    if not imap_conn:
        return []

    try:
        if not _select_folder(
            imap_conn,
            folder,
            readonly=True,
        ):
            return []

        typ, data = imap_conn.uid(
            "SEARCH",
            None,
            "ALL",
        )

        if typ != "OK":
            return []

        if not data or not data[0]:
            return []

        result = []

        for uid in data[0].split():

            uid_str = (
                uid.decode()
                if isinstance(uid, bytes)
                else str(uid)
            )

            typ_h, hdr_data = imap_conn.uid(
                "FETCH",
                uid_str,
                "(BODY.PEEK[HEADER] FLAGS)"
            )

            if typ_h != "OK":
                continue

            raw = None
            flags_text = ""

            for part in hdr_data:

                if (
                    isinstance(part, tuple)
                    and len(part) > 1
                    and isinstance(
                        part[1],
                        (bytes, bytearray),
                    )
                ):
                    raw = part[1]

                try:
                    flags_text = (
                        part[0]
                        .decode(errors="replace")
                    )
                except Exception:
                    pass

            if not raw:
                continue

            msg = email.message_from_bytes(raw)

            result.append(
                {
                    "uid": uid_str,
                    "message_id": msg.get("Message-ID"),
                    "sender": safe_header(
                        msg.get("From", "")
                    ),
                    "recipient": safe_header(
                        msg.get("To", "")
                    ),
                    "subject": safe_header(
                        msg.get("Subject", "")
                    ),
                    "date": safe_header(
                        msg.get("Date", "")
                    ),
                    "is_seen": "\\Seen" in flags_text,
                    "has_attachments": False,
                }
            )

        return result

    finally:
        try:
            imap_conn.logout()
        except Exception:
            pass


def move_message(
    konto_id: int,
    uid: str,
    source_folder: str,
    dest_folder: str,
    timeout: int = 30,
) -> bool:
    account = _get_account_info(konto_id)

    if not account:
        return False

    imap_conn = _connect_imap(
        account,
        timeout,
    )

    if not imap_conn:
        return False

    try:
        if not _select_folder(
            imap_conn,
            source_folder,
            readonly=False,
        ):
            return False

        if _supports_move(imap_conn):
            typ, _ = imap_conn.uid(
                "MOVE",
                uid,
                dest_folder,
            )

            if typ == "OK":
                return True

        typ, _ = imap_conn.uid(
            "COPY",
            uid,
            dest_folder,
        )

        if typ != "OK":
            return False

        imap_conn.uid(
            "STORE",
            uid,
            "+FLAGS.SILENT",
            r"(\Deleted)",
        )

        imap_conn.expunge()

        return True

    finally:
        try:
            imap_conn.logout()
        except Exception:
            pass


def delete_message(
    konto_id: int,
    uid: str,
    folder: str,
    timeout: int = 30,
) -> bool:
    account = _get_account_info(konto_id)

    if not account:
        return False

    imap_conn = _connect_imap(
        account,
        timeout,
    )

    if not imap_conn:
        return False

    try:
        if not _select_folder(
            imap_conn,
            folder,
            readonly=False,
        ):
            return False

        typ, _ = imap_conn.uid(
            "STORE",
            uid,
            "+FLAGS.SILENT",
            r"(\Deleted)",
        )

        if typ != "OK":
            return False

        imap_conn.expunge()

        return True

    finally:
        try:
            imap_conn.logout()
        except Exception:
            pass
def sync_account(
    konto_id: int,
) -> None:

    folders = list_folders(konto_id)

    for folder in folders:

        save_folder(
            konto_id,
            folder,
            normalize_folder_name(folder),
        )

        sync_folder(
            konto_id,
            folder,
        )
def mark_unread_remote(
    konto_id: int,
    folder: str,
    uid: str,
    timeout: int = 30,
) -> bool:

    account = _get_account_info(konto_id)

    if not account:
        return False

    imap_conn = _connect_imap(
        account,
        timeout,
    )

    if not imap_conn:
        return False

    try:
        if not _select_folder(
            imap_conn,
            folder,
            readonly=False,
        ):
            return False

        typ, _ = imap_conn.uid(
            "STORE",
            uid,
            "-FLAGS.SILENT",
            r"(\Seen)",
        )

        return typ == "OK"

    finally:
        try:
            imap_conn.logout()
        except Exception:
            pass

def archive_message(
    konto_id: int,
    uid: str,
    source_folder: str,
    archive_folder: str,
    timeout: int = 30,
) -> bool:
    return move_message(
        konto_id,
        uid,
        source_folder,
        archive_folder,
        timeout,
    )
def sync_folder(
    konto_id: int,
    folder: str,
) -> None:

    folder_id = get_folder_id(
        konto_id,
        folder,
    )

    if not folder_id:
        folder_id = save_folder(
            konto_id,
            folder,
            normalize_folder_name(folder),
        )

    headers = fetch_headers(
        konto_id,
        folder,
    )

    for hdr in headers:

        save_message_header(
            konto_id=konto_id,
            folder_id=folder_id,

            uid=hdr["uid"],

            message_id=hdr["message_id"],

            sender=hdr["sender"],

            recipient=hdr["recipient"],

            subject=hdr["subject"],

            date_value=hdr["date"],

            is_seen=hdr["is_seen"],

            has_attachments=hdr["has_attachments"],
        )

__all__ = [
    "list_folders",
    "fetch_headers",
    "fetch_message_from_imap",
    "move_message",
    "delete_message",
    "archive_message",
    "sync_account",
    "sync_folder",
    "mark_unread_remote",
]
