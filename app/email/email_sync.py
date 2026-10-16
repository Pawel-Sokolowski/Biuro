from __future__ import annotations

import email.policy
import imaplib
import logging
import re

from email.message import Message
from email.parser import BytesHeaderParser
from typing import Any, Dict, List, Optional

from datetime import datetime, timedelta

from app.config import EMAIL_HISTORY_DAYS
from app.email import email_cache
from app.email.email_connection import open_imap
from app.email.email_helpers import (
    decode_mailbox_utf7,
    format_mail_date,
    parse_list_response,
    parse_message,
    quote_mailbox,
    safe_header,
)
from app.email.email_storage import (
    account_has_headers,
    get_account_info as _get_account_info,
    get_folder_id,
    get_folders_for_account,
    get_cached_messages,
    prune_email_storage,
    get_known_uids,
    save_message_headers,
    get_message_by_uid,
    mark_deleted,
    save_folder,
    save_message_header,
    update_message_read_flag,
)
from app.utils.crypto import decrypt_password

logger = logging.getLogger(__name__)

HEADER_BATCH = 100
_IMAP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


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
    for candidate in (quote_mailbox(folder), folder):
        try:
            typ, _ = imap_conn.select(candidate, readonly=readonly)
            if typ == "OK":
                return True
        except Exception:
            logger.warning("Nie można wybrać folderu %s", folder, exc_info=True)
    return False


def _supports_move(imap_conn: imaplib.IMAP4) -> bool:
    return "MOVE" in getattr(imap_conn, "capabilities", ())


def list_folder_infos(
    konto_id: int,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """Foldery z serwera: surowa nazwa, separator, flagi i nazwa do wyświetlenia."""
    account = _get_account_info(konto_id)

    if not account:
        return []

    imap_conn = _connect_imap(account, timeout)

    if not imap_conn:
        return []

    try:
        typ, folders = imap_conn.list()

        if typ != "OK" or not folders:
            return []

        result: Dict[str, Dict[str, Any]] = {}

        for entry in folders:
            try:
                info = parse_list_response(entry)
            except Exception:
                logger.exception("Failed parsing mailbox")
                continue
            if info:
                result[info["name"]] = info

        return sorted(result.values(), key=lambda i: i["name"])

    finally:
        try:
            imap_conn.logout()
        except Exception:
            logger.warning("Zignorowany błąd", exc_info=True)


def list_folders(
    konto_id: int,
    timeout: int = 30,
) -> List[str]:
    return [i["name"] for i in list_folder_infos(konto_id, timeout)]


def _raw_from_fetch(data: Any) -> Optional[bytes]:
    for item in data or []:
        if (
            isinstance(item, tuple)
            and len(item) > 1
            and isinstance(item[1], (bytes, bytearray))
        ):
            return bytes(item[1])
    return None


def fetch_message_from_imap(
    konto_id: int,
    folder: str,
    uid: str,
    timeout: int = 30,
) -> Optional[Message]:
    cached = email_cache.read_message(konto_id, folder, str(uid))
    if cached:
        return parse_message(cached)

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
            "(BODY.PEEK[])",
        )

        if typ != "OK":
            return None

        raw_bytes = _raw_from_fetch(data)

        if not raw_bytes:
            return None

        email_cache.write_message(konto_id, folder, str(uid), raw_bytes)
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


def prefetch_messages(
    konto_id: int,
    folder: str,
    uids: List[str],
    timeout: int = 30,
) -> int:
    """Pobiera treści wiadomości do cache jednym połączeniem; zwraca liczbę pobranych."""
    wanted = [str(u) for u in uids if not email_cache.has_message(konto_id, folder, str(u))]
    if not wanted:
        return 0

    account = _get_account_info(konto_id)
    imap_conn = _connect_imap(account, timeout) if account else None
    if not imap_conn:
        return 0

    fetched = 0
    try:
        if not _select_folder(imap_conn, folder, readonly=True):
            return 0
        for uid in wanted:
            try:
                typ, data = imap_conn.uid("FETCH", uid, "(BODY.PEEK[])")
            except Exception:
                logger.warning("Prefetch przerwany dla uid=%s", uid, exc_info=True)
                break
            raw = _raw_from_fetch(data) if typ == "OK" else None
            if raw:
                email_cache.write_message(konto_id, folder, uid, raw)
                fetched += 1
        return fetched
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

        since = datetime.now() - timedelta(days=EMAIL_HISTORY_DAYS)
        since_str = f"{since.day:02d}-{_IMAP_MONTHS[since.month - 1]}-{since.year}"
        typ, data = imap_conn.uid("SEARCH", None, "SINCE", since_str)

        if typ != "OK":
            return []

        if not data or not data[0]:
            return []

        result = []

        # nagłówki z ostatniego roku, których jeszcze nie mamy lokalnie (najnowsze najpierw)
        uids = [u.decode() if isinstance(u, bytes) else str(u) for u in data[0].split()]
        uids = [u for u in reversed(uids) if u not in (known_uids or set())]

        parser = BytesHeaderParser(policy=email.policy.default)
        for start in range(0, len(uids), HEADER_BATCH):
            chunk = uids[start:start + HEADER_BATCH]
            try:
                typ_h, hdr_data = imap_conn.uid(
                    "FETCH",
                    ",".join(chunk),
                    "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID CONTENT-TYPE)])",
                )
            except Exception:
                logger.warning("Błąd pobierania nagłówków", exc_info=True)
                break
            if typ_h != "OK":
                continue

            records: List[List[Any]] = []
            for part in hdr_data or []:
                if isinstance(part, tuple):
                    records.append([part[0] if isinstance(part[0], bytes) else b"", part[1]])
                elif isinstance(part, bytes) and records:
                    # UID/FLAGS mogą przyjść po literale
                    records[-1][0] += b" " + part
            for meta, raw in records:
                m_uid = re.search(rb"UID (\d+)", meta)
                if not m_uid or not raw:
                    continue
                msg = parser.parsebytes(raw)
                flags = imaplib.ParseFlags(meta)
                result.append(
                    {
                        "uid": m_uid.group(1).decode(),
                        "message_id": str(msg["Message-ID"]) if msg["Message-ID"] else None,
                        "sender": safe_header(msg.get("From", "")),
                        "recipient": safe_header(msg.get("To", "")),
                        "subject": safe_header(msg.get("Subject", "")),
                        "date": format_mail_date(msg.get("Date", "")),
                        "is_seen": b"\\Seen" in flags,
                        "has_attachments": msg.get_content_type() == "multipart/mixed",
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
                quote_mailbox(dest_folder),
            )

            if typ == "OK":
                return True

        typ, _ = imap_conn.uid(
            "COPY",
            uid,
            quote_mailbox(dest_folder),
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
def _store_folders(konto_id: int, infos: List[Dict[str, Any]]) -> None:
    for info in infos:
        save_folder(
            konto_id,
            info["name"],
            info["display"],
            info["delimiter"],
            " ".join(info["flags"]),
        )


def sync_account(
    konto_id: int,
) -> None:

    infos = list_folder_infos(konto_id)
    _store_folders(konto_id, infos)

    for info in infos:
        if "\\noselect" in [f.lower() for f in info["flags"]]:
            continue
        sync_folder(
            konto_id,
            info["name"],
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
) -> int:

    folder_id = get_folder_id(
        konto_id,
        folder,
    )

    if not folder_id:
        folder_id = save_folder(
            konto_id,
            folder,
            decode_mailbox_utf7(folder),
        )

    headers = fetch_headers(
        konto_id,
        folder,
        known_uids=get_known_uids(konto_id, folder_id),
    )

    save_message_headers(
        konto_id,
        folder_id,
        headers,
    )
    return len(headers)


def selectable_folder_names(konto_id: int) -> List[str]:
    """Nazwy folderów z bazy, do których można wejść (bez \\Noselect)."""
    names = []
    for f in get_folders_for_account(konto_id):
        flags = (f.get("attributes") or "").lower().split()
        if "\\noselect" not in flags and f.get("server_name"):
            names.append(f["server_name"])
    return names


def needs_initial_sync(konto_id: int) -> bool:
    """Pierwsze otwarcie konta: brak folderów lub nagłówków w bazie (gotowa baza = już zsynchronizowane)."""
    return not account_has_headers(konto_id)


def sync_and_prefetch(
    konto_id: int,
    folders: List[str],
    first_run: bool = False,
    on_headers=None,
    on_progress=None,
    all_folders: bool = False,
) -> int:
    """Odświeża listę folderów i nagłówki (ok. rok wstecz). Treści trafiają do cache po kliknięciu.

    all_folders=True (pierwsza synchronizacja konta) pobiera nagłówki ze wszystkich folderów.
    on_progress(zrobione_foldery, wszystkie_foldery, nazwa_folderu, pobrane_nagłówki) jest
    wywoływane przed i po każdym folderze.
    """
    if first_run or all_folders or not get_folders_for_account(konto_id):
        list_folders_from_imap_and_store(konto_id)

    if all_folders:
        wanted = list(folders)
        for name in selectable_folder_names(konto_id):
            if name not in wanted:
                wanted.append(name)
        folders = wanted

    total = len(folders)
    fetched = 0
    for index, folder in enumerate(folders):
        if on_progress:
            on_progress(index, total, folder, fetched)
        fetched += sync_folder(konto_id, folder) or 0
        if on_headers:
            on_headers()
    if on_progress:
        on_progress(total, total, "", fetched)
    return fetched

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

        update_message_read_flag(konto_id, str(uid), seen, get_folder_id(konto_id, folder))
        return True

    finally:
        try:
            imap_conn.logout()
        except Exception:
            logger.warning("Zignorowany błąd", exc_info=True)


def mark_read_remote(konto_id: int, folder: str, uid: str, timeout: int = 30) -> bool:
    return _set_seen_remote(konto_id, folder, uid, True, timeout)


def list_folders_from_imap_and_store(konto_id: int) -> bool:
    infos = list_folder_infos(konto_id)
    _store_folders(konto_id, infos)
    return bool(infos)


def fetch_headers_from_imap_and_store(konto_id: int, folder: str) -> None:
    sync_folder(konto_id, folder)


def fetch_message_from_imap_and_store(konto_id: int, folder: str, uid: str) -> bool:
    return fetch_message_from_imap(konto_id, folder, uid) is not None


def delete_email_remote(konto_id: int, folder: str, uid: str) -> bool:
    ok = delete_message(konto_id, uid, folder)

    if ok:
        mark_deleted(konto_id, str(uid), get_folder_id(konto_id, folder))
        email_cache.remove_message(konto_id, folder, str(uid))


def move_email_and_get_sender(
    konto_id: int,
    source_folder: str,
    uid: str,
    dest_folder: str,
) -> str:
    folder_id = get_folder_id(konto_id, source_folder)
    cached = get_message_by_uid(konto_id, str(uid), folder_id) or {}

    if not move_message(konto_id, uid, source_folder, dest_folder):
        raise RuntimeError("Serwer odrzucił przeniesienie wiadomości.")

    mark_deleted(konto_id, str(uid), folder_id)
    email_cache.remove_message(konto_id, source_folder, str(uid))
    return cached.get("nadawca") or ""


__all__ = [
    "list_folder_infos",
    "prefetch_messages",
    "sync_and_prefetch",
    "list_folders",
    "fetch_headers",
    "fetch_message_from_imap",
    "move_message",
    "delete_message",
    "archive_message",
    "sync_account",
    "sync_folder",
    "selectable_folder_names",
    "needs_initial_sync",
    "mark_unread_remote",
    "mark_read_remote",
    "list_folders_from_imap_and_store",
    "fetch_headers_from_imap_and_store",
    "fetch_message_from_imap_and_store",
    "delete_email_remote",
    "move_email_and_get_sender",
]
