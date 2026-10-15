from __future__ import annotations

import email.policy
import imaplib
import logging

from email.message import Message
from email.parser import BytesHeaderParser
from typing import Any, Dict, List, Optional

from app.config import EMAIL_LOCAL_LIMIT
from app.email.email_connection import open_imap
from app.email.email_helpers import (
    normalize_folder_name,
    parse_mailbox_name,
    parse_message,
    safe_header,
)
from app.email.email_storage import (
    get_account_info as _get_account_info,
    get_folder_id,
    get_known_uids,
    get_message_by_uid,
    mark_deleted,
    save_folder,
    save_message_header,
    update_message_read_flag,
)
from app.utils.crypto import decrypt_password

logger = logging.getLogger(__name__)


def _connect_imap(
    account: Dict[str, Any],
    timeout: int = 30,
) -> Optional[imaplib.IMAP4]:
    host = account.get("serwer_imap")
    login = account.get("email")

    if not host or not login:
        return None

    try:
        return open_imap(
            host,
            account.get("port_imap"),
            account.get("ssl_imap"),
            login,
            decrypt_password(account.get("haslo_enc")),
            timeout,
        )
    except Exception:
        logger.exception("IMAP login failed for %s", login)
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
        logger.warning("Zignorowany błąd", exc_info=True)

    try:
        typ, _ = imap_conn.select(
            f'"{folder}"',
            readonly=readonly,
        )

        return typ == "OK"

    except Exception:
        return False


def _supports_move(imap_conn: imaplib.IMAP4) -> bool:
    return "MOVE" in getattr(imap_conn, "capabilities", ())


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
            logger.warning("Zignorowany błąd", exc_info=True)


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

        return parse_message(raw_bytes)

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
            logger.warning("Zignorowany błąd", exc_info=True)


def fetch_headers(
    konto_id: int,
    folder: str,
    timeout: int = 30,
    known_uids: Optional[set] = None,
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

        # tylko najnowsze wiadomości, których jeszcze nie mamy lokalnie
        uids = [u.decode() if isinstance(u, bytes) else str(u) for u in data[0].split()]
        uids = [u for u in uids[-EMAIL_LOCAL_LIMIT:] if u not in (known_uids or set())]

        for uid_str in uids:

            typ_h, hdr_data = imap_conn.uid(
                "FETCH",
                uid_str,
                "(BODY.PEEK[HEADER] FLAGS)"
            )

            if typ_h != "OK":
                continue

            raw = None
            flags = ()

            for part in hdr_data:
                if isinstance(part, tuple):
                    raw = part[1]
                    meta = part[0]
                else:
                    meta = part

                if isinstance(meta, bytes):
                    flags = flags or imaplib.ParseFlags(meta)

            if not raw:
                continue

            msg = BytesHeaderParser(policy=email.policy.default).parsebytes(raw)

            result.append(
                {
                    "uid": uid_str,
                    "message_id": str(msg["Message-ID"]) if msg["Message-ID"] else None,
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
                    "is_seen": b"\\Seen" in flags,
                    "has_attachments": False,
                }
            )

        return result

    finally:
        try:
            imap_conn.logout()
        except Exception:
            logger.warning("Zignorowany błąd", exc_info=True)


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
            logger.warning("Zignorowany błąd", exc_info=True)


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
            logger.warning("Zignorowany błąd", exc_info=True)
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
    return _set_seen_remote(konto_id, folder, uid, False, timeout)

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
        known_uids=get_known_uids(konto_id, folder_id),
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

def _set_seen_remote(
    konto_id: int,
    folder: str,
    uid: str,
    seen: bool,
    timeout: int = 30,
) -> bool:
    account = _get_account_info(konto_id)
    imap_conn = _connect_imap(account, timeout) if account else None

    if not imap_conn:
        return False

    try:
        if not _select_folder(imap_conn, folder, readonly=False):
            return False

        typ, _ = imap_conn.uid(
            "STORE",
            str(uid),
            "+FLAGS.SILENT" if seen else "-FLAGS.SILENT",
            r"(\Seen)",
        )

        if typ != "OK":
            return False

        update_message_read_flag(konto_id, str(uid), seen)
        return True

    finally:
        try:
            imap_conn.logout()
        except Exception:
            logger.warning("Zignorowany błąd", exc_info=True)


def mark_read_remote(konto_id: int, folder: str, uid: str, timeout: int = 30) -> bool:
    return _set_seen_remote(konto_id, folder, uid, True, timeout)


def list_folders_from_imap_and_store(konto_id: int) -> None:
    for folder in list_folders(konto_id):
        save_folder(konto_id, folder, normalize_folder_name(folder))


def fetch_headers_from_imap_and_store(konto_id: int, folder: str) -> None:
    sync_folder(konto_id, folder)


def fetch_message_from_imap_and_store(konto_id: int, folder: str, uid: str) -> bool:
    return fetch_message_from_imap(konto_id, folder, uid) is not None


def delete_email_remote(konto_id: int, folder: str, uid: str) -> bool:
    ok = delete_message(konto_id, uid, folder)

    if ok:
        mark_deleted(konto_id, str(uid))

    return ok


def move_email_and_get_sender(
    konto_id: int,
    source_folder: str,
    uid: str,
    dest_folder: str,
) -> str:
    cached = get_message_by_uid(konto_id, str(uid)) or {}

    if not move_message(konto_id, uid, source_folder, dest_folder):
        raise RuntimeError("Serwer odrzucił przeniesienie wiadomości.")

    mark_deleted(konto_id, str(uid))
    return cached.get("nadawca") or ""


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
    "mark_read_remote",
    "list_folders_from_imap_and_store",
    "fetch_headers_from_imap_and_store",
    "fetch_message_from_imap_and_store",
    "delete_email_remote",
    "move_email_and_get_sender",
]
