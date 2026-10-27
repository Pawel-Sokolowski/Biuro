from __future__ import annotations

import email.policy
import imaplib
import time
import logging
import re
import threading

from contextlib import contextmanager
from email.message import Message
from email.parser import BytesHeaderParser
from typing import Any, Dict, List, Optional, Tuple

from datetime import datetime, timedelta

from app.config import EMAIL_HISTORY_DAYS
from app.db.dao_conn import get_conn
from app.email import email_cache
from app.email.email_connection import open_imap
from app.email.email_helpers import (
    decode_mailbox_utf7,
    encode_mailbox_utf7,
    format_mail_date,
    parse_list_response,
    parse_message,
    quote_mailbox,
    safe_header,
)
from app.email.email_storage import (
    account_has_headers,
    apply_server_state,
    get_account_info as _get_account_info,
    get_folder_id,
    get_folders_for_account,
    get_cached_messages,
    prune_email_storage,
    get_known_uids,
    save_message_headers,
    get_uids_missing_refs,
    save_refs,
    get_message_by_uid,
    mark_deleted,
    rename_folder_local,
    save_folder,
    save_message_header,
    update_message_read_flag, delete_missing_folders,
    get_message_row_id,
    load_raw_message,
    save_raw_message,
)
from app.utils.crypto import decrypt_password

logger = logging.getLogger(__name__)

HEADER_BATCH = 100
INCREMENTAL_PREFETCH_LIMIT = 20

# Synchronizacje tego samego konta nie mogą pobierać równocześnie tych samych wiadomości.
_ACCOUNT_SYNC_LOCKS = {}
_ACCOUNT_SYNC_LOCKS_MUTEX = threading.Lock()


def _account_sync_lock(konto_id: int) -> threading.Lock:
    with _ACCOUNT_SYNC_LOCKS_MUTEX:
        return _ACCOUNT_SYNC_LOCKS.setdefault(konto_id, threading.Lock())

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


class _Session:
    """Współdzielone połączenie IMAP konta (unika logowania przy każdej akcji)."""

    def __init__(self, conn: imaplib.IMAP4) -> None:
        self.conn = conn
        self.selected: Optional[Tuple[str, bool]] = None

    def select(self, folder: str, readonly: bool) -> bool:
        if self.selected and self.selected[0] == folder and (self.selected[1] == readonly or not self.selected[1]):
            return True
        if _select_folder(self.conn, folder, readonly=readonly):
            self.selected = (folder, readonly)
            return True
        self.selected = None
        return False

    def close(self) -> None:
        try:
            self.conn.logout()
        except Exception:
            logger.debug("Zamknięcie sesji IMAP", exc_info=True)


_sessions: Dict[int, _Session] = {}
_session_locks: Dict[int, threading.Lock] = {}
_session_guard = threading.Lock()


@contextmanager
def _shared_session(konto_id: int, timeout: int = 30):
    """Daje sesję IMAP konta (albo None gdy brak połączenia); jednocześnie używa jej jeden wątek."""
    with _session_guard:
        lock = _session_locks.setdefault(konto_id, threading.Lock())
    with lock:
        session = _sessions.get(konto_id)
        if session is not None:
            try:
                session.conn.noop()
            except Exception:
                session.close()
                session = None
                _sessions.pop(konto_id, None)
        if session is None:
            account = _get_account_info(konto_id)
            conn = _connect_imap(account, timeout) if account else None
            if conn is not None:
                session = _Session(conn)
                _sessions[konto_id] = session
        try:
            yield session
        except Exception:
            if session is not None:
                session.close()
                _sessions.pop(konto_id, None)
            raise


def close_sessions() -> None:
    with _session_guard:
        sessions = list(_sessions.values())
        _sessions.clear()
    for session in sessions:
        session.close()

def list_folder_infos(
    konto_id: int,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """
    Foldery z serwera: surowa nazwa, separator, flagi i nazwa do wyświetlenia.
    """

    account = _get_account_info(konto_id)

    if not account:
        logger.warning(
            "list_folder_infos: brak konfiguracji konta %s",
            konto_id,
        )
        return []

    imap_conn = _connect_imap(account, timeout)

    if not imap_conn:
        logger.warning(
            "list_folder_infos: brak połączenia IMAP dla konta %s",
            konto_id,
        )
        return []

    try:
        typ, folders = imap_conn.list()

        logger.info(
            "IMAP LIST konto=%s typ=%s folders=%s",
            konto_id,
            typ,
            len(folders or []),
        )

        if folders:
            for entry in folders:
                logger.info("IMAP RAW FOLDER: %r", entry)

        if typ != "OK" or not folders:
            return []

        result: Dict[str, Dict[str, Any]] = {}

        for entry in folders:
            try:
                info = parse_list_response(entry)

                logger.info(
                    "PARSED FOLDER: %s",
                    info,
                )

            except Exception:
                logger.exception(
                    "Failed parsing mailbox: %r",
                    entry,
                )
                continue

            if not info:
                continue

            if info["name"] in result:
                logger.warning(
                    "DUPLIKAT FOLDERU: %s",
                    info["name"],
                )

            result[info["name"]] = info

        logger.info(
            "Po parsowaniu znaleziono %s folderów",
            len(result),
        )

        return sorted(
            result.values(),
            key=lambda i: i["name"],
        )

    finally:
        try:
            imap_conn.logout()
        except Exception:
            logger.warning(
                "Zignorowany błąd logout",
                exc_info=True,
            )


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

    folder_id = get_folder_id(
        konto_id,
        folder,
    )

    if folder_id:

        row_id = get_message_row_id(
            konto_id,
            folder_id,
            str(uid),
        )

        if row_id:

            cached = load_raw_message(
                row_id,
            )

            if cached:
                return parse_message(
                    cached,
                )

    try:
        with _shared_session(
            konto_id,
            timeout,
        ) as session:

            if session is None:
                return None

            if not session.select(
                folder,
                True,
            ):
                return None

            typ, data = session.conn.uid(
                "FETCH",
                str(uid),
                "(BODY.PEEK[])",
            )

            raw_bytes = (
                _raw_from_fetch(data)
                if typ == "OK"
                else None
            )

            if not raw_bytes:
                return None

    except Exception:
        logger.exception(
            "FETCH failed for uid=%s",
            uid,
        )
        return None

    if folder_id:

        row_id = get_message_row_id(
            konto_id,
            folder_id,
            str(uid),
        )

        if row_id:
            save_raw_message(
                row_id,
                raw_bytes,
            )

    return parse_message(
        raw_bytes,
    )


def prefetch_messages(
    konto_id: int,
    folder: str,
    uids: List[str],
    timeout: int = 30,
    strict: bool = False,
) -> int:
    folder_id = get_folder_id(konto_id, folder)
    if not folder_id:
        logger.warning(
            "Brak folder_id: konto=%s, folder=%s",
            konto_id, folder,
        )
        if strict:
            raise RuntimeError(f"Brak folderu {folder} w bazie PostgreSQL")
        return 0

    # Usuń duplikaty UID-ów.
    unique_uids = list(dict.fromkeys(str(uid) for uid in uids))
    total = len(unique_uids)
    fetched = 0
    skipped = 0
    failed = 0
    session_error = None

    if total == 0:
        logger.info(
            "%s — brak wiadomości do pobrania",
            folder,
        )
        return 0

    logger.warning(
        "%s — rozpoczęto pobieranie pełnych wiadomości: %s",
        folder, total,
    )

    try:
        with _shared_session(konto_id, timeout) as session:
            if session is None or not session.select(folder, True):
                logger.error(
                    "%s — nie udało się wybrać folderu IMAP",
                    folder,
                )
                if strict:
                    raise RuntimeError(f"Nie można otworzyć folderu IMAP: {folder}")
                return 0

            for index, uid in enumerate(unique_uids, start=1):
                try:
                    row_id = get_message_row_id(
                        konto_id, folder_id, uid
                    )

                    if not row_id:
                        if strict:
                            failed += 1
                        else:
                            skipped += 1
                    elif load_raw_message(row_id):
                        # Pełna treść jest już w bazie.
                        skipped += 1
                    else:
                        typ, data = session.conn.uid(
                            "FETCH", uid, "(BODY.PEEK[])"
                        )

                        raw = (
                            _raw_from_fetch(data)
                            if typ == "OK"
                            else None
                        )

                        if not raw:
                            failed += 1
                            logger.warning(
                                "Brak treści uid=%s, folder=%s",
                                uid, folder,
                            )
                        else:
                            save_raw_message(row_id, raw)
                            fetched += 1

                except Exception:
                    failed += 1
                    logger.warning(
                        "Nie udało się pobrać uid=%s, folder=%s",
                        uid, folder,
                        exc_info=True,
                    )

                # Raport co 100 UID-ów oraz na końcu folderu.
                if index % 100 == 0 or index == total:
                    logger.warning(
                        "%s — sprawdzono %s/%s wiadomości; "
                        "zapisano nowych: %s; pominięto: %s; "
                        "błędy: %s",
                        folder,
                        index,
                        total,
                        fetched,
                        skipped,
                        failed,
                    )

    except Exception as exc:
        session_error = exc
        logger.exception(
            "Błąd sesji IMAP: konto=%s, folder=%s",
            konto_id, folder,
        )

    logger.warning(
        "%s — zakończono: zapisano %s, pominięto %s, błędy %s",
        folder, fetched, skipped, failed,
    )

    if strict and (failed or session_error):
        raise RuntimeError(
            f"Nie udało się pobrać wszystkich treści z folderu {folder}: "
            f"{failed} wiadomości z błędem"
        ) from session_error

    return fetched

def prefetch_recent(
    konto_id: int,
    folder: str,
    limit: int = 20,
) -> int:
    """Pobiera do cache treści najnowszych wiadomości folderu."""

    folder_id = get_folder_id(konto_id, folder)

    if not folder_id:
        return 0

    rows = get_cached_messages(
        konto_id,
        folder_id,
    )[:limit]

    return prefetch_messages(
        konto_id,
        folder,
        [r["uid"] for r in rows]
    )



def prefetch_all_messages(
    konto_id: int,
    folder: str,
    batch_size: int = 100,
) -> int:
    folder_id = get_folder_id(konto_id, folder)
    if not folder_id:
        print(f"{folder} — folder nie znaleziony w PostgreSQL")
        return 0

    rows = get_cached_messages(konto_id, folder_id)
    uids = [
        str(row["uid"])
        for row in rows
        if row.get("uid") is not None
    ]

    total = len(uids)
    downloaded = 0

    print(f"\n{folder} — start pobierania: {total} messages", flush=True)

    if total == 0:
        print(f"{folder} — brak wiadomości do pobrania", flush=True)
        return 0

    for start in range(0, total, batch_size):
        batch = uids[start:start + batch_size]

        downloaded += prefetch_messages(
            konto_id,
            folder,
            batch,
        )

        processed = min(start + len(batch), total)

        print(
            f"{folder} — downloaded {processed}/{total} messages "
            f"(newly saved: {downloaded})",
            flush=True,
        )

    print(
        f"{folder} — FINISHED: {downloaded} new messages saved "
        f"out of {total}",
        flush=True,
    )

    return downloaded

def fetch_headers(
    konto_id: int,
    folder: str,
    timeout: int = 30,
    known_uids: Optional[set] = None,
    flags_out: Optional[Dict[str, set]] = None,
    history_days: Optional[int] = EMAIL_HISTORY_DAYS,
) -> List[Dict[str, Any]]:
    """
    Pobiera nagłówki wiadomości.

    history_days:
        None -> całe konto (ALL)
        730  -> ostatnie 2 lata
        EMAIL_HISTORY_DAYS -> standardowy zakres
    """

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

        if history_days is None:

            typ, data = imap_conn.uid(
                "SEARCH",
                None,
                "ALL",
            )

            since_str = None

        else:

            since = datetime.now() - timedelta(days=history_days)

            since_str = (
                f"{since.day:02d}-"
                f"{_IMAP_MONTHS[since.month - 1]}-"
                f"{since.year}"
            )

            typ, data = imap_conn.uid(
                "SEARCH",
                None,
                "SINCE",
                since_str,
            )

        if typ != "OK":
            return []

        if not data or not data[0]:

            if flags_out is not None:
                flags_out["all"] = set()
                flags_out["unseen"] = set()

            return []

        result = []

        uids = [
            u.decode() if isinstance(u, bytes) else str(u)
            for u in data[0].split()
        ]

        if flags_out is not None:

            flags_out["all"] = set(uids)

            if since_str:

                typ_u, unseen = imap_conn.uid(
                    "SEARCH",
                    None,
                    "UNSEEN",
                    "SINCE",
                    since_str,
                )

            else:

                typ_u, unseen = imap_conn.uid(
                    "SEARCH",
                    None,
                    "UNSEEN",
                )

            if typ_u == "OK":

                flags_out["unseen"] = {
                    u.decode() if isinstance(u, bytes) else str(u)
                    for u in (
                        unseen[0].split()
                        if unseen and unseen[0]
                        else []
                    )
                }

        uids = [
            uid
            for uid in reversed(uids)
            if uid not in (known_uids or set())
        ]

        parser = BytesHeaderParser(
            policy=email.policy.default
        )

        for start in range(
                0,
                len(uids),
                HEADER_BATCH,
        ):

            chunk = uids[start:start + HEADER_BATCH]

            try:

                typ_h, hdr_data = imap_conn.uid(
                    "FETCH",
                    ",".join(chunk),
                    "(FLAGS BODY.PEEK[HEADER.FIELDS "
                    "(FROM TO SUBJECT DATE MESSAGE-ID "
                    "IN-REPLY-TO REFERENCES CONTENT-TYPE)])",
                )

            except Exception:

                logger.warning(
                    "Błąd pobierania nagłówków",
                    exc_info=True,
                )

                break

            if typ_h != "OK":
                continue

            records: List[List[Any]] = []

            for part in hdr_data or []:

                if isinstance(part, tuple):

                    records.append([
                        part[0]
                        if isinstance(part[0], bytes)
                        else b"",
                        part[1],
                    ])

                elif isinstance(part, bytes) and records:

                    records[-1][0] += b" " + part

            for meta, raw in records:

                m_uid = re.search(
                    rb"UID (\d+)",
                    meta,
                )

                if not m_uid or not raw:
                    continue

                msg = parser.parsebytes(raw)

                flags = imaplib.ParseFlags(meta)

                result.append(
                    {
                        "uid": m_uid.group(1).decode(),
                        "message_id": (
                            str(msg["Message-ID"])
                            if msg["Message-ID"]
                            else None
                        ),
                        "refs": thread_refs(msg),
                        "sender": safe_header(
                            msg.get("From", "")
                        ),
                        "recipient": safe_header(
                            msg.get("To", "")
                        ),
                        "subject": safe_header(
                            msg.get("Subject", "")
                        ),
                        "date": format_mail_date(
                            msg.get("Date", "")
                        ),
                        "is_seen": b"\\Seen" in flags,
                        "has_attachments": (
                            msg.get_content_type()
                            == "multipart/mixed"
                        ),
                    }
                )

        return result

    finally:

        try:
            imap_conn.logout()
        except Exception:
            logger.warning(
                "Zignorowany błąd",
                exc_info=True,
            )

def thread_refs(msg) -> str:
    """Message-ID-y z In-Reply-To i References (do budowania wątków); pusty tekst = brak."""
    found = []
    for name in ("In-Reply-To", "References"):
        found += re.findall(r"<[^<>\s]+>", str(msg.get(name) or ""))
    return " ".join(dict.fromkeys(found))


def backfill_refs(konto_id: int, folder: str, folder_id: int, limit: int = 400) -> int:
    """Dociąga nagłówki wątków dla wiadomości zapisanych przed dodaniem tej funkcji."""
    uids = get_uids_missing_refs(konto_id, folder_id, limit)
    if not uids:
        return 0
    parser = BytesHeaderParser(policy=email.policy.default)
    updates = []
    with _shared_session(konto_id, 60) as session:
        if session is None or not session.select(folder, True):
            return 0
        for start in range(0, len(uids), HEADER_BATCH):
            chunk = uids[start:start + HEADER_BATCH]
            typ, data = session.conn.uid(
                "FETCH", ",".join(chunk), "(BODY.PEEK[HEADER.FIELDS (IN-REPLY-TO REFERENCES)])"
            )
            if typ != "OK":
                continue
            seen = set()
            for part in data or []:
                if isinstance(part, tuple):
                    m_uid = re.search(rb"UID (\d+)", part[0])
                    if m_uid:
                        uid = m_uid.group(1).decode()
                        seen.add(uid)
                        updates.append((uid, thread_refs(parser.parsebytes(part[1]))))
            updates += [(u, "") for u in chunk if u not in seen]
    save_refs(konto_id, folder_id, updates)
    return len(updates)

def sync_folder_and_prefetch_all(
    konto_id: int,
    folder: str,
) -> int:
    # Najpierw pobierz wszystkie nagłówki z historii.
    sync_folder(konto_id, folder, full_sync=True)

    # Następnie pobierz pełną treść wszystkich wiadomości
    # zapisanych w bazie dla tego folderu.
    return prefetch_all_messages(konto_id, folder)

def append_message(konto_id: int, folder: str, raw: bytes, seen: bool = True, timeout: int = 60) -> bool:
    """Dokłada gotową wiadomość do folderu IMAP (jak kopia wysłanej w Thunderbirdzie)."""
    with _shared_session(konto_id, timeout) as session:
        if session is None:
            return False
        typ, _ = session.conn.append(
            quote_mailbox(folder), r"(\Seen)" if seen else None,
            imaplib.Time2Internaldate(time.time()), raw,
        )
        return typ == "OK"


def move_message(
    konto_id: int,
    uid: str,
    source_folder: str,
    dest_folder: str,
    timeout: int = 30,
) -> bool:
    """Przenosi wiadomo??; `uid` mo?e by? list? oddzielonych przecinkami UID-?w."""
    with _shared_session(konto_id, timeout) as session:
        if session is None or not session.select(source_folder, False):
            return False
        conn = session.conn
        if _supports_move(conn):
            typ, _ = conn.uid("MOVE", uid, quote_mailbox(dest_folder))
            if typ == "OK":
                return True
        typ, _ = conn.uid("COPY", uid, quote_mailbox(dest_folder))
        if typ != "OK":
            return False
        conn.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
        conn.expunge()
        return True


def delete_message(
    konto_id: int,
    uid: str,
    folder: str,
    timeout: int = 30,
) -> bool:
    with _shared_session(konto_id, timeout) as session:
        if session is None or not session.select(folder, False):
            return False
        typ, _ = session.conn.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
        if typ != "OK":
            return False
        session.conn.expunge()
        return True

def _store_folders(
    konto_id: int,
    infos: List[Dict[str, Any]],
) -> None:

    delete_missing_folders(
        konto_id,
        {
            info["name"]
            for info in infos
        },
    )

    for info in infos:
        save_folder(
            konto_id,
            info["name"],
            info["display"],
            info["delimiter"],
            " ".join(info["flags"]),
        )


def sync_account(konto_id: int) -> None:
    logger.warning(
        "SYNC_ACCOUNT START konto=%s",
        konto_id,
    )

    infos = list_folder_infos(konto_id)

    logger.warning(
        "SYNC_ACCOUNT: serwer zwrócił %s folderów: %s",
        len(infos),
        [info.get("name") for info in infos],
    )

    _store_folders(konto_id, infos)

    selectable_folders = []

    for info in infos:
        name = info.get("name")
        flags = [
            str(flag).lower()
            for flag in info.get("flags", [])
        ]

        if not name:
            continue

        if "\\noselect" in flags:
            logger.warning(
                "POMINIĘTO folder bez możliwości wyboru: %s",
                name,
            )
            continue

        selectable_folders.append(name)

    logger.warning(
        "SYNC_ACCOUNT: foldery do synchronizacji: %s",
        selectable_folders,
    )

    for index, folder_name in enumerate(
        selectable_folders,
        start=1,
    ):
        logger.warning(
            "SYNC_ACCOUNT: folder %s/%s START: %s",
            index,
            len(selectable_folders),
            folder_name,
        )

        try:
            count = sync_folder(
                konto_id,
                folder_name,
                full_sync=True,
            )

            logger.warning(
                "SYNC_ACCOUNT: folder %s/%s KONIEC: %s; "
                "pobrano nagłówków: %s",
                index,
                len(selectable_folders),
                folder_name,
                count,
            )

        except Exception:
            logger.exception(
                "SYNC_ACCOUNT: błąd folderu %s",
                folder_name,
            )

    logger.warning(
        "SYNC_ACCOUNT KONIEC konto=%s",
        konto_id,
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
    full_sync: bool = True,
    entire_mailbox: bool = False,
) -> int:
    history_days = None if entire_mailbox else (730 if full_sync else EMAIL_HISTORY_DAYS)

    logger.warning(
        "SYNC START konto=%s folder=%s full_sync=%s history_days=%s",
        konto_id,
        folder,
        full_sync,
        history_days,
    )

    folder_id = get_folder_id(konto_id, folder)

    if not folder_id:
        folder_id = save_folder(
            konto_id,
            folder,
            decode_mailbox_utf7(folder),
        )

    flags: Dict[str, set] = {}
    known = set()

    if not full_sync and not entire_mailbox:
        known = get_known_uids(konto_id, folder_id)

    logger.warning(
        "%s — rozpoczynam pobieranie nagłówków",
        folder,
    )

    headers = fetch_headers(
        konto_id,
        folder,
        known_uids=known,
        flags_out=flags,
        history_days=history_days,
    )

    logger.warning(
        "%s — pobrano %s nagłówków",
        folder,
        len(headers),
    )

    save_message_headers(
        konto_id,
        folder_id,
        headers,
    )

    logger.warning(
        "%s — zapisano nagłówki w PostgreSQL",
        folder,
    )

    try:
        backfill_refs(
            konto_id,
            folder,
            folder_id,
        )
    except Exception:
        logger.warning(
            "Nie udało się pobrać nagłówków wątków",
            exc_info=True,
        )

    if known and headers and folder.upper() == "INBOX":
        try:
            from app.email.email_rules import apply_rules

            apply_rules(
                konto_id,
                folder,
                headers,
            )
        except Exception:
            logger.warning(
                "Nie udało się zastosować filtrów",
                exc_info=True,
            )

    if "all" in flags:
        cutoff = (
            "0001-01-01"
            if history_days is None
            else (datetime.now() - timedelta(days=history_days - 7)).strftime("%Y-%m-%d")
        )

        apply_server_state(
            konto_id,
            folder_id,
            flags["all"],
            flags.get("unseen"),
            cutoff,
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

def _sync_and_prefetch_impl(
    konto_id: int,
    folders: List[str],
    first_run: bool = False,
    on_headers=None,
    on_progress=None,
    all_folders: bool = False,
    full_mailbox: bool = False,
) -> int:
    """
    Synchronizuje nagłówki oraz pełne treści wiadomości.

    Przy first_run lub all_folders:
    - synchronizuje wszystkie wybieralne foldery,
    - ogranicza wiadomości do ostatnich 730 dni.

    full_mailbox=True: wszystkie foldery, cała historia IMAP i wszystkie treści
    zapisywane do PostgreSQL (tylko na żądanie z ustawień).

    Zwykła synchronizacja: wybrane foldery, standardowy zakres nagłówków
    i tylko ostatnie INCREMENTAL_PREFETCH_LIMIT pełnych wiadomości.
    """

    if (
        first_run
        or all_folders
        or full_mailbox
        or not get_folders_for_account(konto_id)
    ):
        refreshed = list_folders_from_imap_and_store(konto_id)
        if full_mailbox and not refreshed:
            raise RuntimeError("Nie udało się odczytać listy folderów z serwera IMAP.")

    if first_run or all_folders or full_mailbox:
        wanted = list(folders)

        for name in selectable_folder_names(konto_id):
            if name not in wanted:
                wanted.append(name)

        folders = wanted

    # Usuń duplikaty, zachowując kolejność.
    folders = list(dict.fromkeys(folders))

    total = len(folders)
    total_headers = 0
    total_bodies = 0
    failed_folders = []

    logger.warning(
        "SYNC: konto=%s, foldery do przetworzenia=%s",
        konto_id,
        folders,
    )

    for index, folder in enumerate(folders):
        if on_progress:
            on_progress(index, total, folder, total_headers)

        logger.warning(
            "=== FOLDER %s/%s: %s ===",
            index + 1,
            total,
            folder,
        )

        try:
            # 1. Pobierz i zapisz nagłówki.
            count = sync_folder(
                konto_id,
                folder,
                full_sync=(first_run or all_folders or full_mailbox),
                entire_mailbox=full_mailbox,
            ) or 0

            total_headers += count

            if on_headers:
                on_headers()

            # 2. Pobierz wiadomości z tego folderu z PostgreSQL.
            folder_id = get_folder_id(konto_id, folder)

            if not folder_id:
                logger.warning(
                    "%s — brak folder_id, pomijam treści",
                    folder,
                )
                if full_mailbox:
                    raise RuntimeError(f"Brak folder_id dla {folder}")
                continue

            rows = get_cached_messages(
                konto_id,
                folder_id,
            )

            # get_cached_messages powinno zwracać rekordy
            # uporządkowane od najnowszych. Odfiltruj rekordy
            # starsze niż 730 dni podczas pełnej synchronizacji.
            if (first_run or all_folders) and not full_mailbox:
                cutoff = datetime.now() - timedelta(days=730)

                def within_last_two_years(row):
                    value = row.get("data")
                    if not value:
                        return False

                    if isinstance(value, str):
                        try:
                            value = datetime.fromisoformat(
                                value.strip().replace("Z", "+00:00")
                            )
                        except (ValueError, TypeError):
                            return False

                    if not isinstance(value, datetime):
                        return False

                    # Ujednolicenie stref czasowych przed porównaniem.
                    if value.tzinfo is not None:
                        value = value.replace(tzinfo=None)

                    return value >= cutoff

                rows = [row for row in rows if within_last_two_years(row)]
            elif not full_mailbox:
                rows = rows[:INCREMENTAL_PREFETCH_LIMIT]

            uids = [
                str(row["uid"])
                for row in rows
                if row.get("uid") is not None
            ]

            logger.warning(
                "%s — wiadomości do sprawdzenia pod kątem treści: %s",
                folder,
                len(uids),
            )

            # 3. Prefetch wiadomości w paczkach po 100 UID-ów.
            for start in range(0, len(uids), 100):
                batch = uids[start:start + 100]

                saved = prefetch_messages(
                    konto_id,
                    folder,
                    batch,
                    strict=full_mailbox,
                )

                total_bodies += saved

                logger.warning(
                    "%s — sprawdzono %s/%s wiadomości; "
                    "w tej paczce zapisano nowych: %s",
                    folder,
                    min(start + len(batch), len(uids)),
                    len(uids),
                    saved,
                )

            logger.warning(
                "=== KONIEC FOLDERU: %s ===",
                folder,
            )

        except Exception:
            logger.exception(
                "Błąd synchronizacji folderu %s",
                folder,
            )
            if full_mailbox:
                failed_folders.append(folder)

        if on_progress:
            on_progress(
                index + 1,
                total,
                folder,
                total_headers,
            )

    try:
        from app.utils.saldeo_status import apply_saldeo_emails
        apply_saldeo_emails()
    except Exception:
        logger.exception(
            "Nie udało się zaktualizować statusów z maili Saldeo"
        )

    logger.warning(
        "SYNC KONIEC konto=%s; nagłówki=%s; nowe pełne treści=%s",
        konto_id,
        total_headers,
        total_bodies,
    )

    if failed_folders:
        raise RuntimeError(
            "Błędy pełnej synchronizacji folderów: " + ", ".join(failed_folders[:15])
        )
    return total_headers


def sync_and_prefetch(
    konto_id: int,
    folders: List[str],
    first_run: bool = False,
    on_headers=None,
    on_progress=None,
    all_folders: bool = False,
    full_mailbox: bool = False,
) -> int:
    # Pełne i automatyczne synchronizacje jednego konta wykonują się kolejno.
    with _account_sync_lock(konto_id):
        return _sync_and_prefetch_impl(
            konto_id, folders, first_run=first_run, on_headers=on_headers,
            on_progress=on_progress, all_folders=all_folders,
            full_mailbox=full_mailbox,
        )


def _set_seen_remote(
    konto_id: int,
    folder: str,
    uid: str,
    seen: bool,
    timeout: int = 30,
) -> bool:
    with _shared_session(konto_id, timeout) as session:
        if session is None or not session.select(folder, False):
            return False
        typ, _ = session.conn.uid(
            "STORE",
            str(uid),
            "+FLAGS.SILENT" if seen else "-FLAGS.SILENT",
            r"(\Seen)",
        )
        if typ != "OK":
            return False
    update_message_read_flag(konto_id, str(uid), seen, get_folder_id(konto_id, folder))
    return True


def mark_read_remote(konto_id: int, folder: str, uid: str, timeout: int = 30) -> bool:
    return _set_seen_remote(konto_id, folder, uid, True, timeout)


def list_folders_from_imap_and_store(konto_id: int) -> bool:
    infos = list_folder_infos(konto_id)
    _store_folders(konto_id, infos)
    return bool(infos)


def fetch_headers_from_imap_and_store(
    konto_id: int,
    folder: str,
) -> None:
    sync_folder(
        konto_id,
        folder,
        full_sync=True,
    )
def fetch_message_from_imap_and_store(konto_id: int, folder: str, uid: str) -> bool:
    return fetch_message_from_imap(konto_id, folder, uid) is not None


def delete_email_remote(konto_id: int, folder: str, uid: str) -> bool:
    ok = delete_message(konto_id, uid, folder)

    if ok:
        mark_deleted(konto_id, str(uid), get_folder_id(konto_id, folder))
        email_cache.remove_message(konto_id, folder, str(uid))
    return ok


def move_email_and_get_sender(
    konto_id: int,
    source_folder: str,
    uid: str,
    dest_folder: str,
) -> str:
    folder_id = get_folder_id(konto_id, source_folder)
    cached = get_message_by_uid(konto_id, str(uid), folder_id) or {}

    if not move_message(konto_id, uid, source_folder, dest_folder):
        raise RuntimeError("Serwer odrzuci? przeniesienie wiadomo?ci.")

    mark_deleted(konto_id, str(uid), folder_id)
    email_cache.remove_message(konto_id, source_folder, str(uid))
    return cached.get("nadawca") or ""


def move_emails(
    konto_id: int,
    source_folder: str,
    uids: List[str],
    dest_folder: str,
) -> None:
    """Przenosi wiele wiadomo?ci jednym poleceniem IMAP i odnotowuje to w bazie lokalnej."""
    uids = [str(u) for u in uids]
    if not uids or source_folder == dest_folder:
        return
    if not move_message(konto_id, ",".join(uids), source_folder, dest_folder):
        raise RuntimeError("Serwer odrzuci? przeniesienie wiadomo?ci.")
    folder_id = get_folder_id(konto_id, source_folder)
    for uid in uids:
        mark_deleted(konto_id, uid, folder_id)
        email_cache.remove_message(konto_id, source_folder, uid)


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
    "move_emails",
    "prefetch_recent",
    "close_sessions",
]


def _mailbox_command(konto_id: int, action) -> None:
    with _shared_session(konto_id) as session:
        if session is None:
            raise RuntimeError("Brak po??czenia z serwerem poczty.")
        action(session)


def _check(result) -> None:
    typ, data = result
    if typ != "OK":
        detail = data[0].decode(errors="replace") if data and isinstance(data[0], bytes) else ""
        raise RuntimeError(detail or "Serwer odrzuci? polecenie.")


def create_folder(konto_id: int, name: str) -> str:
    """Tworzy folder na serwerze (`name` w postaci nieskodowanej, ze separatorem serwera) i zwraca jego nazw? IMAP."""
    server_name = encode_mailbox_utf7(name)

    def action(session: _Session) -> None:
        _check(session.conn.create(quote_mailbox(server_name)))
        try:
            session.conn.subscribe(quote_mailbox(server_name))
        except Exception:
            logger.debug("SUBSCRIBE nie powiod?o si?", exc_info=True)

    _mailbox_command(konto_id, action)
    save_folder(konto_id, server_name, decode_mailbox_utf7(server_name), _delimiter_of(konto_id, server_name), "")
    return server_name


def _delimiter_of(konto_id: int, server_name: str) -> Optional[str]:
    delimiters = [f.get("delimiter") for f in get_folders_for_account(konto_id) if f.get("delimiter")]
    return delimiters[0] if delimiters else None


def rename_folder(konto_id: int, old: str, new_name: str) -> str:
    """Zmienia nazw? lub przenosi folder (RENAME) na serwerze, potem w bazie lokalnej."""
    new_server = encode_mailbox_utf7(new_name)
    if new_server == old:
        return old

    def action(session: _Session) -> None:
        if session.selected:
            try:
                session.conn.close()
            except Exception:
                logger.debug("CLOSE przed zmian? nazwy", exc_info=True)
            session.selected = None
        _check(session.conn.rename(quote_mailbox(old), quote_mailbox(new_server)))
        for method, name in (("unsubscribe", old), ("subscribe", new_server)):
            try:
                getattr(session.conn, method)(quote_mailbox(name))
            except Exception:
                logger.debug("%s nie powiod?o si?", method, exc_info=True)

    _mailbox_command(konto_id, action)
    rename_folder_local(konto_id, old, new_server)
    return new_server
