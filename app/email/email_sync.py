from __future__ import annotations

import email.policy
import imaplib
import json
import time
import logging
import re
import threading

from contextlib import contextmanager
from email.message import Message
from email.parser import BytesHeaderParser
from typing import Any, Dict, List, Optional, Tuple

from datetime import datetime, timedelta

from app.config import EMAIL_HISTORY_DAYS, get_dsn, get_schema
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting, set_setting
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
    lock_account_revision,
    apply_server_state,
    get_account_info as _get_account_info,
    get_folder_id,
    get_mailbox_generation,
    MailboxGenerationChanged,
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
    get_message_cache_states,
    save_current_raw_message,
    record_mailbox_generation,
)
from app.utils.crypto import decrypt_password

logger = logging.getLogger(__name__)

HEADER_BATCH = 100
INCREMENTAL_PREFETCH_LIMIT = 20

# Synchronizacje tego samego konta nie mogą pobierać równocześnie tych samych wiadomości.
_ACCOUNT_SYNC_LOCKS = {}
_ACCOUNT_SYNC_LOCKS_MUTEX = threading.Lock()


_AUTH_FAILURES: Dict[Any, float] = {}
AUTH_RETRY_SECONDS = 900


def _account_sync_lock(konto_id: int) -> threading.Lock:
    with _ACCOUNT_SYNC_LOCKS_MUTEX:
        return _ACCOUNT_SYNC_LOCKS.setdefault((get_dsn(), get_schema(), konto_id), threading.RLock())

_IMAP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _connect_imap(
    account: Dict[str, Any],
    timeout: int = 30,
) -> Optional[imaplib.IMAP4]:
    host = account.get("serwer_imap")
    login = account.get("email")

    if not host or not login:
        return None

    key = (get_dsn(), get_schema(), host, account.get("port_imap"), account.get("ssl_imap"), login, account.get("haslo_enc"))
    failed_at = _AUTH_FAILURES.get(key)
    if failed_at is not None and time.monotonic() - failed_at < AUTH_RETRY_SECONDS:
        return None

    try:
        password = decrypt_password(account.get("haslo_enc"))
        if account.get("haslo_enc") and not password:
            _AUTH_FAILURES[key] = time.monotonic()
            return None
        return open_imap(
            host,
            account.get("port_imap"),
            account.get("ssl_imap"),
            login,
            password,
            timeout,
        )
    except imaplib.IMAP4.error as exc:
        if "AUTHENTICATIONFAILED" in str(exc).upper() or "LOGIN" in str(exc).upper():
            # Złe hasło ignorujemy; ponowna próba dopiero po zmianie hasła lub po czasie.
            _AUTH_FAILURES[key] = time.monotonic()
            logger.info("IMAP: nieprawidłowe dane logowania dla %s", login)
            return None
        logger.warning("IMAP login failed for %s: %s", login, exc)
        return None
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
    return 'MOVE' in _capabilities(imap_conn)


def _capabilities(conn):
    return {value.decode('ascii', 'replace').upper() if isinstance(value, bytes) else str(value).upper()
            for value in getattr(conn, 'capabilities', ())}


def _expunge_selected(conn, uids):
    # Without UIDPLUS, leave the selected messages flagged Deleted. A plain
    # EXPUNGE would also remove mail marked by another client in this folder.
    if 'UIDPLUS' in _capabilities(conn):
        typ, _ = conn.uid('EXPUNGE', uids)
        if typ != 'OK':
            raise RuntimeError('Nie udało się zatwierdzić usunięcia wybranych wiadomości.')


class _Session:
    """Współdzielone połączenie IMAP konta (unika logowania przy każdej akcji)."""

    def __init__(self, conn: imaplib.IMAP4, konto_id: Optional[int] = None, account=None) -> None:
        self.conn = conn
        self.fingerprint = _account_fingerprint(account or {})
        self.account_revision = (account or {}).get("connection_revision")
        self.konto_id = konto_id
        self.selected: Optional[Tuple[str, bool]] = None
        self.selected_generation: Optional[str] = None

    def select(self, folder: str, readonly: bool) -> bool:
        self.selected = None
        self.selected_generation = None
        observed = get_mailbox_generation(self.konto_id, folder) if self.konto_id is not None else None
        if _select_folder(self.conn, folder, readonly=readonly):
            generation = _uidvalidity(self.conn)
            if self.konto_id is not None and self.account_revision is not None:
                with get_conn() as db:
                    lock_account_revision(db, self.konto_id, self.account_revision)
            # IMAP4.response() consumes the response. Preserve the generation
            # from this SELECT for callers that later validate a body write.
            if self.konto_id is not None and generation:
                retired = record_mailbox_generation(self.konto_id, folder, generation, expected_generation=observed, **_revision_args(self.account_revision))
                if retired:
                    self.selected = None
                    self.selected_generation = None
                    # The caller's UID list belongs to the retired generation.
                    return False
            self.selected_generation = generation
            self.selected = (folder, readonly)
            return True
        self.selected = None
        self.selected_generation = None
        return False

    def close(self) -> None:
        try:
            self.conn.logout()
        except Exception:
            logger.debug("Zamknięcie sesji IMAP", exc_info=True)


_sessions: Dict[int, _Session] = {}
_session_locks: Dict[int, threading.Lock] = {}
_session_guard = threading.Lock()


def _account_fingerprint(account):
    return tuple(account.get(k) for k in ("serwer_imap", "port_imap", "ssl_imap", "email", "haslo_enc", "connection_revision"))


def _revision_args(revision):
    return {"expected_account_revision": revision} if revision is not None else {}


@contextmanager
def _shared_session(konto_id: int, timeout: int = 30):
    """Reuse only within the same database, schema and connection configuration."""
    key = (get_dsn(), get_schema(), konto_id)
    with _session_guard:
        lock = _session_locks.setdefault(key, threading.Lock())
    with lock:
        account = _get_account_info(konto_id)
        session = _sessions.get(key)
        if session is not None:
            try:
                if not account or session.fingerprint != _account_fingerprint(account):
                    raise ConnectionError("Zmieniono konfigurację konta IMAP.")
                session.conn.noop()
            except Exception:
                session.close()
                session = None
                _sessions.pop(key, None)
        if session is None:
            conn = _connect_imap(account, timeout) if account else None
            if conn is not None:
                session = _Session(conn, konto_id, account)
                _sessions[key] = session
        try:
            yield session
        except Exception:
            if session is not None:
                session.close()
                _sessions.pop(key, None)
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
        logger.debug(
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
    expected_row_id: Optional[int] = None,
) -> Optional[Message]:

    folder_id = get_folder_id(
        konto_id,
        folder,
    )
    if expected_row_id is not None and not folder_id:
        return None

    if folder_id:

        row_id = get_message_row_id(
            konto_id,
            folder_id,
            str(uid),
        )
        if expected_row_id is not None and row_id != expected_row_id:
            return None

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

            # Bind the downloaded bytes to the mapping and generation selected
            # here; never look up a replacement row after FETCH has finished.
            generation = session.selected_generation
            account_revision = getattr(session, "account_revision", None)
            row_id = get_message_row_id(konto_id, folder_id, str(uid)) if folder_id else None
            if expected_row_id is not None and row_id != expected_row_id:
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

    if row_id and not save_current_raw_message(konto_id, folder_id, str(uid), row_id, generation, raw_bytes, **_revision_args(account_revision)):
        return None

    return parse_message(
        raw_bytes,
    )


def prefetch_messages(
    konto_id: int,
    folder: str,
    uids: List[str],
    timeout: int = 30,
    strict: bool = False,
    on_progress=None,
    cancel_event=None,
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

    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Synchronizacja przerwana.")
    cache_states = get_message_cache_states(konto_id,folder_id,unique_uids)
    if all(cache_states.get(uid,(None,False))[1] for uid in unique_uids):
        if on_progress:
            on_progress(total,total)
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

            generation = session.selected_generation
            account_revision = getattr(session, "account_revision", None)
            # SELECT can adopt a generation already recorded by another sync,
            # in which case its success says nothing about the captured IDs.
            cache_states = get_message_cache_states(konto_id, folder_id, unique_uids)
            for index, uid in enumerate(unique_uids, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Synchronizacja przerwana; zostanie wznowiona po uruchomieniu aplikacji.")
                try:
                    row_id, cached = cache_states.get(uid,(None,False))

                    if not row_id:
                        if strict:
                            failed += 1
                        else:
                            skipped += 1
                    elif cached:
                        # Pełna treść jest już w bazie; nie pobieraj jej ponownie do RAM.
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
                            if save_current_raw_message(konto_id, folder_id, uid, row_id, generation, raw, **_revision_args(account_revision)):
                                fetched += 1
                            else:
                                failed += 1
                                logger.warning("Zmieniono mapowanie UID podczas pobierania: %s/%s", folder, uid)

                except Exception:
                    failed += 1
                    logger.warning(
                        "Nie udało się pobrać uid=%s, folder=%s",
                        uid, folder,
                        exc_info=True,
                    )

                # Raport co 100 UID-ów oraz na końcu folderu.
                if index % 100 == 0 or index == total:
                    if on_progress:
                        on_progress(index, total)
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

def _uidvalidity(conn) -> Optional[str]:
    """Read SELECT's UIDVALIDITY response without assuming test/server extensions."""
    try:
        _, values = conn.response("UIDVALIDITY")
        if isinstance(values, (list, tuple)) and values:
            value = values[-1]
            if isinstance(value, bytes):
                value = value.decode("ascii")
            if isinstance(value, str) and value.isdigit():
                return value
    except (AttributeError, TypeError, ValueError, UnicodeError):
        pass
    return None


def fetch_headers(
    konto_id: int,
    folder: str,
    timeout: int = 30,
    known_uids: Optional[set] = None,
    flags_out: Optional[Dict[str, Any]] = None,
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
        raise RuntimeError("Brak konfiguracji konta e-mail.")

    imap_conn = _connect_imap(
        account,
        timeout,
    )

    if not imap_conn:
        raise ConnectionError("Nie można połączyć się z serwerem IMAP.")

    try:
        observed = get_mailbox_generation(konto_id, folder)
        if not _select_folder(
            imap_conn,
            folder,
            readonly=True,
        ):
            raise RuntimeError(f"Nie można otworzyć folderu IMAP: {folder}")

        generation = _uidvalidity(imap_conn)
        if generation:
            record_mailbox_generation(konto_id, folder, generation, expected_generation=observed, **_revision_args(account.get("connection_revision")))
        if flags_out is not None:
            flags_out["generation"] = generation
            if account.get("connection_revision") is not None:
                flags_out["account_revision"] = account["connection_revision"]
        if known_uids is not None:
            # Another selector may already have adopted this generation. Its
            # retirement boolean cannot validate the caller's earlier UID set.
            folder_id = get_folder_id(konto_id, folder)
            known_uids = get_known_uids(konto_id, folder_id) if folder_id else set()

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
            raise RuntimeError(f"Nie można pobrać listy wiadomości z folderu {folder}.")

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
                    "(FROM TO CC SUBJECT DATE MESSAGE-ID "
                    "IN-REPLY-TO REFERENCES CONTENT-TYPE)])",
                )

            except Exception:

                logger.warning(
                    "Błąd pobierania nagłówków",
                    exc_info=True,
                )

                break

            if typ_h != "OK":
                raise RuntimeError(f"Nie można pobrać nagłówków z folderu {folder}.")

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
                        "recipient": ", ".join(
                            part for part in (safe_header(msg.get("To", "")), safe_header(msg.get("Cc", ""))) if part
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
        generation = session.selected_generation
        account_revision = getattr(session, "account_revision", None)
        uids = get_uids_missing_refs(konto_id, folder_id, limit)
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
    save_refs(konto_id, folder_id, updates, expected_generation=generation, **_revision_args(account_revision))
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


def _assert_selected_rows(konto_id, folder, expected_rows):
    """A stale UI UID must never move/delete/mark a replacement message."""
    if expected_rows is None:
        return
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT w.uid, w.id FROM wiadomosci_email w JOIN foldery_email f ON f.id=w.folder_id "
            "WHERE w.konto_id=%s AND f.server_name=%s AND w.uid=ANY(%s) AND COALESCE(w.usunieta,0)=0",
            (konto_id, folder, list(expected_rows)),
        ).fetchall()
    current = {str(row[0]): row[1] for row in rows}
    if any(current.get(str(uid)) != row_id or row_id is None for uid, row_id in expected_rows.items()):
        raise RuntimeError("Wiadomość zmieniła identyfikator na serwerze. Odśwież listę przed ponowieniem operacji.")


def move_message(
    konto_id: int,
    uid: str,
    source_folder: str,
    dest_folder: str,
    timeout: int = 30,
    expected_rows=None,
) -> bool:
    """Przenosi wiadomo??; `uid` mo?e by? list? oddzielonych przecinkami UID-?w."""
    with _shared_session(konto_id, timeout) as session:
        if session is None or not session.select(source_folder, False):
            return False
        _assert_selected_rows(konto_id, source_folder, expected_rows)
        conn = session.conn
        if _supports_move(conn):
            typ, _ = conn.uid("MOVE", uid, quote_mailbox(dest_folder))
            return typ == 'OK'
        typ, _ = conn.uid("COPY", uid, quote_mailbox(dest_folder))
        if typ != "OK":
            return False
        typ, _ = conn.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
        if typ != 'OK':
            raise RuntimeError('Wiadomość została skopiowana, ale nie usunięto jej z folderu źródłowego. Odśwież widok przed kolejną operacją.')
        _expunge_selected(conn, uid)
        return True


def delete_message(
    konto_id: int,
    uid: str,
    folder: str,
    timeout: int = 30,
    expected_row_id=None,
) -> bool:
    with _shared_session(konto_id, timeout) as session:
        if session is None or not session.select(folder, False):
            return False
        _assert_selected_rows(konto_id, folder, None if expected_row_id is None else {str(uid): expected_row_id})
        typ, _ = session.conn.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
        if typ != "OK":
            return False
        _expunge_selected(session.conn, uid)
        return True

def _store_folders(
    konto_id: int,
    infos: List[Dict[str, Any]],
) -> None:

    if not infos:
        return
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
    *, expected_row_id=None,
) -> bool:
    return _set_seen_remote(konto_id, folder, uid, False, timeout, expected_row_id=expected_row_id)

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
    # A mailbox may be recreated while any remote batch is in flight. Retry the
    # entire snapshot once; continued churn remains an error for sync callers.
    for attempt in range(2):
        try:
            return _sync_folder_once(konto_id, folder, full_sync, entire_mailbox)
        except MailboxGenerationChanged:
            if attempt:
                raise
            logger.warning("%s — generacja zmieniła się, ponawiam synchronizację", folder)
    raise AssertionError("unreachable")


def _sync_folder_once(konto_id: int, folder: str, full_sync: bool, entire_mailbox: bool) -> int:
    history_days = None if entire_mailbox else (730 if full_sync else EMAIL_HISTORY_DAYS)

    folder_id = get_folder_id(konto_id, folder)

    if not folder_id:
        folder_id = save_folder(
            konto_id,
            folder,
            decode_mailbox_utf7(folder),
        )

    flags: Dict[str, Any] = {}
    known = None

    if not full_sync and not entire_mailbox:
        known = get_known_uids(konto_id, folder_id)

    headers = fetch_headers(
        konto_id,
        folder,
        known_uids=known,
        flags_out=flags,
        history_days=history_days,
    )

    header_rows = save_message_headers(
        konto_id,
        folder_id,
        headers,
        expected_generation=flags.get("generation"),
        **_revision_args(flags.get("account_revision")),
    )

    try:
        backfill_refs(
            konto_id,
            folder,
            folder_id,
        )
    except MailboxGenerationChanged:
        raise
    except Exception:
        logger.warning(
            "Nie udało się pobrać nagłówków wątków",
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
            expected_generation=flags.get("generation"),
            **_revision_args(flags.get("account_revision")),
        )

    if known and headers and folder.upper() == "INBOX":
        try:
            from app.email.email_rules import apply_rules

            apply_rules(konto_id, folder, headers, expected_rows=header_rows)
        except Exception:
            logger.warning("Nie udało się zastosować filtrów", exc_info=True)

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
    """Only a completed full two-year download counts as initial sync."""
    return get_setting(f"email_initial_sync_complete_{konto_id}") != "1" or get_setting(f"email_initial_sync_pending_{konto_id}") == "1"

def _sync_and_prefetch_impl(
    konto_id: int,
    folders: List[str],
    first_run: bool = False,
    on_headers=None,
    on_progress=None,
    all_folders: bool = False,
    full_mailbox: bool = False,
    headers_only: bool = False,
    cancel_event=None,
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
        if (full_mailbox or first_run) and not refreshed:
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

    historical = bool(first_run or all_folders or full_mailbox)

    for index, folder in enumerate(folders):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Synchronizacja przerwana; zostanie wznowiona po uruchomieniu aplikacji.")
        if on_progress and index == 0:
            on_progress(0, total, folder, total_headers)

        folder_saved = 0
        folder_checked = 0
        count = 0

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

            if headers_only:
                if on_progress:
                    on_progress(index + 1, total, folder, total_headers)
                continue

            # 2. Pobierz wiadomości z tego folderu z PostgreSQL.
            folder_id = get_folder_id(konto_id, folder)

            if not folder_id:
                logger.warning(
                    "%s — brak folder_id, pomijam treści",
                    folder,
                )
                if full_mailbox or first_run:
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

            folder_checked = len(uids)

            # 3. Prefetch wiadomości w paczkach po 100 UID-ów.
            for start in range(0, len(uids), 100):
                batch = uids[start:start + 100]

                progress_callback = None
                if on_progress:
                    progress_callback = lambda done, total, offset=start, count=len(uids), name=folder: on_progress(
                        index, len(folders), f"{name} · pełne wiadomości {offset+done}/{count}", total_headers)
                options = {"strict": (full_mailbox or first_run)}
                if cancel_event is not None:
                    options["cancel_event"] = cancel_event
                if progress_callback:
                    options["on_progress"] = progress_callback
                saved = prefetch_messages(konto_id, folder, batch, **options)

                total_bodies += saved
                folder_saved += saved

            if historical:
                logger.warning(
                    "%s: nagłówki %s, zapisano treści %s, pominięto %s, błędy 0",
                    folder, count, folder_saved, max(folder_checked - folder_saved, 0),
                )

        except Exception as exc:
            logger.error("%s: BŁĄD — %s: %s", folder, type(exc).__name__, exc)
            logger.debug("Szczegóły błędu folderu %s", folder, exc_info=True)
            failed_folders.append(folder)

        if on_progress:
            on_progress(
                index + 1,
                total,
                folders[index + 1] if index + 1 < total else "",
                total_headers,
            )

    try:
        from app.utils.saldeo_status import (
            apply_saldeo_emails,
            rescan_on_startup,
        )
        apply_saldeo_emails()
        rescan_on_startup()
    except Exception:
        logger.exception(
            "Nie udało się zaktualizować statusów z maili Saldeo"
        )

    if failed_folders:
        raise RuntimeError(
            "Błędy synchronizacji folderów: " + ", ".join(failed_folders[:15])
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
    headers_only: bool = False,
    cancel_event=None,
) -> int:
    lock = _account_sync_lock(konto_id)
    while not lock.acquire(timeout=0.5):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Synchronizacja przerwana.")
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Synchronizacja przerwana.")
        result = _sync_and_prefetch_impl(
            konto_id, folders, first_run=first_run, on_headers=on_headers,
            on_progress=on_progress, all_folders=all_folders,
            full_mailbox=full_mailbox, headers_only=headers_only, cancel_event=cancel_event,
        )
        if full_mailbox or (first_run and not headers_only):
            set_setting(f"email_initial_sync_complete_{konto_id}", "1")
            set_setting(f"email_initial_sync_pending_{konto_id}", "0")
        return result
    finally:
        lock.release()


def _set_seen_remote(
    konto_id: int,
    folder: str,
    uid: str,
    seen: bool,
    timeout: int = 30,
    expected_row_id=None,
) -> bool:
    with _shared_session(konto_id, timeout) as session:
        if session is None or not session.select(folder, False):
            return False
        _assert_selected_rows(konto_id, folder, None if expected_row_id is None else {str(uid): expected_row_id})
        typ, _ = session.conn.uid(
            "STORE",
            str(uid),
            "+FLAGS.SILENT" if seen else "-FLAGS.SILENT",
            r"(\Seen)",
        )
        if typ != "OK":
            return False
    update_message_read_flag(konto_id, str(uid), seen, get_folder_id(konto_id, folder), expected_row_id=expected_row_id)
    return True


def mark_read_remote(konto_id: int, folder: str, uid: str, timeout: int = 30, *, expected_row_id=None) -> bool:
    return _set_seen_remote(konto_id, folder, uid, True, timeout, expected_row_id=expected_row_id)


def list_folders_from_imap_and_store(konto_id: int) -> bool:
    infos = list_folder_infos(konto_id)
    if not infos:
        return False
    _store_folders(konto_id, infos)
    return True


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


def delete_email_remote(konto_id: int, folder: str, uid: str, *, expected_row_id=None) -> bool:
    options = {} if expected_row_id is None else {"expected_row_id": expected_row_id}
    ok = delete_message(konto_id, uid, folder, **options)

    if ok:
        mark_deleted(konto_id, str(uid), get_folder_id(konto_id, folder), expected_row_id=expected_row_id)
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
    *, expected_rows=None,
) -> None:
    """Przenosi wiele wiadomo?ci jednym poleceniem IMAP i odnotowuje to w bazie lokalnej."""
    uids = [str(u) for u in uids]
    if not uids or source_folder == dest_folder:
        return
    options = {} if expected_rows is None else {"expected_rows": expected_rows}
    if not move_message(konto_id, ",".join(uids), source_folder, dest_folder, **options):
        raise RuntimeError("Serwer odrzuci? przeniesienie wiadomo?ci.")
    folder_id = get_folder_id(konto_id, source_folder)
    for uid in uids:
        mark_deleted(konto_id, uid, folder_id, expected_row_id=expected_rows.get(uid) if expected_rows is not None else None)
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


_STATUS_RE = re.compile(r"(UIDVALIDITY|UIDNEXT|MESSAGES|UNSEEN)\s+(\d+)", re.IGNORECASE)
FULL_SWEEP_EVERY = 10
_auto_runs: Dict[int, int] = {}


def _folder_status(session: _Session, folder: str) -> Optional[str]:
    """Tani odcisk folderu z polecenia STATUS (bez SELECT i bez pobierania nagłówków)."""
    for candidate in (quote_mailbox(folder), folder):
        try:
            typ, data = session.conn.status(candidate, "(UIDVALIDITY UIDNEXT MESSAGES UNSEEN)")
        except Exception:
            continue
        if typ == "OK" and data and data[0]:
            raw = data[0] if isinstance(data[0], bytes) else str(data[0]).encode()
            pairs = _STATUS_RE.findall(raw.decode("ascii", "replace").rsplit("(", 1)[-1])
            if pairs:
                return ",".join(f"{key.upper()}={value}" for key, value in sorted(pairs))
    return None


def _changed_folders(konto_id: int, folders: List[str], force_full: bool) -> Tuple[List[str], Dict[str, str]]:
    """Zwraca foldery, których STATUS różni się od zapamiętanego, oraz bieżący odcisk.

    Brak odcisku (pierwsze uruchomienie, błąd połączenia, folder bez STATUS) oznacza
    „zmieniony”, więc w razie problemów działa jak dotychczasowa pełna kontrola.
    """
    try:
        previous = json.loads(get_setting(f"email_status_snapshot_{konto_id}") or "{}")
    except Exception:
        previous = {}
    current: Dict[str, str] = {}
    try:
        with _shared_session(konto_id) as session:
            if session is not None:
                for folder in folders:
                    status = _folder_status(session, folder)
                    if status:
                        current[folder] = status
    except Exception:
        logger.debug("Odczyt STATUS folderów nie powiódł się", exc_info=True)
    changed = [f for f in folders if force_full or f not in current or previous.get(f) != current[f]]
    return changed, current


def sync_automatic_accounts(accounts, current_account_id=None, current_folder=None, *, on_headers=None, on_progress=None, cancel_event=None):
    """Check new mail only. Full history is never initiated by app startup.

    Accounts run independently so a slow account or a full-mailbox import does
    not prevent other accounts from receiving mail. Same-account syncs retain
    their existing lock. Full history downloads remain an explicit action.

    Foldery są najpierw porównywane przez IMAP STATUS (UIDNEXT/MESSAGES/UNSEEN);
    nagłówki pobierane są tylko ze zmienionych. Co FULL_SWEEP_EVERY-ty przebieg
    (i pierwszy po starcie) sprawdza wszystkie foldery, by wychwycić zmiany flag.
    """
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    ordered = sorted(accounts, key=lambda account: account["id"] != current_account_id)
    if not ordered:
        return []

    def headers(account):
        konto_id = account["id"]
        folders = []
        lock = _account_sync_lock(konto_id)
        if not lock.acquire(blocking=False):
            return konto_id, [], None
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Synchronizacja przerwana.")
            if not list_folders_from_imap_and_store(konto_id):
                raise ConnectionError("Nie udało się odświeżyć listy folderów IMAP.")
            folders = selectable_folder_names(konto_id)
            priority = [current_folder] if konto_id == current_account_id and current_folder in folders else []
            if "INBOX" in folders:
                priority.append("INBOX")
            folders = list(dict.fromkeys(priority + folders))
            if not folders:
                raise RuntimeError("Brak wybieralnych folderów IMAP.")
            sweep_key = f"email_full_sweep_{konto_id}"
            today = datetime.now().strftime("%Y-%m-%d")
            run_number = _auto_runs.get(konto_id, 0)
            full_sweep = get_setting(sweep_key) != today or run_number % FULL_SWEEP_EVERY == 0
            changed, snapshot = _changed_folders(konto_id, folders, force_full=full_sweep)
            logger.info("Konto %s: zmienione foldery %s z %s", konto_id, len(changed), len(folders))
            if not changed:
                _auto_runs[konto_id] = run_number + 1
                if full_sweep:
                    set_setting(sweep_key, today)
                return konto_id, [], None
            options = {"first_run":False,"headers_only":True,"on_headers":on_headers,"on_progress":on_progress}
            if cancel_event is not None:
                options["cancel_event"] = cancel_event
            sync_and_prefetch(konto_id, changed, **options)
            _auto_runs[konto_id] = run_number + 1
            if snapshot:
                set_setting(f"email_status_snapshot_{konto_id}", json.dumps(snapshot))
            if full_sweep:
                set_setting(sweep_key, today)
            return konto_id, changed, None
        except Exception as exc:
            return konto_id, [], f"Konto #{konto_id}: {exc}"

        finally:
            lock.release()

    with ThreadPoolExecutor(max_workers=min(4, len(ordered))) as pool:
        futures = [pool.submit(copy_context().run, headers, account) for account in ordered]
        jobs = [future.result() for future in futures]
    errors = [error for _, _, error in jobs if error]
    # Body prefetch cannot postpone other accounts' header/unread updates.
    for konto_id, folders, error in jobs:
        if cancel_event is not None and cancel_event.is_set():
            break
        if error:
            continue
        wanted = [folder for folder in folders if folder == "INBOX" or (konto_id == current_account_id and folder == current_folder)]
        for folder in wanted:
            try:
                lock = _account_sync_lock(konto_id)
                if not lock.acquire(blocking=False):
                    continue
                try:
                    prefetch_recent(konto_id, folder, INCREMENTAL_PREFETCH_LIMIT)
                finally:
                    lock.release()
            except Exception as exc:
                errors.append(f"Konto #{konto_id}, {folder}: {exc}")
    return errors


def sync_requested_accounts(accounts, *, on_progress=None, cancel_event=None):
    """Explicit Settings action: full messages/attachments, all folders, 730 days.

    Pending markers retain progress information, never permission to start a
    full download automatically. Repeating the Settings action reuses the cache.
    """
    errors = []
    for account in accounts:
        if cancel_event is not None and cancel_event.is_set():
            break
        account_id = account["id"]
        try:
            set_setting(f"email_initial_sync_pending_{account_id}", "1")
            def progress(done, total, folder, headers):
                if on_progress:
                    on_progress(account.get("email") or str(account_id), done, total, folder, headers)
            sync_and_prefetch(account_id, ["INBOX"], first_run=True,
                              on_progress=progress, cancel_event=cancel_event)
        except Exception as exc:
            errors.append(f"Konto #{account_id}: {exc}")
    return errors
