from __future__ import annotations

import re
import os
import email
import logging
import sqlite3
import imaplib
from typing import Any, Dict, List, Optional, Tuple

from email.message import Message
from app.db.dao_conn import get_conn
from app.config import EMAIL_LOCAL_LIMIT
from app.email.email_helpers import (
    normalize_folder_name, denormalize_folder_name, safe_header,
    attachment_output_path, extract_html, get_attachments,
)

logger = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"[^@]+@[^@]+\.[^@]+")

# NOTE: placeholder decryption — replace with your real decrypt routine
def _decrypt_password(haslo_enc: Optional[str]) -> str:
    # If you store encrypted passwords, replace this with actual decryption.
    # For now we assume haslo_enc is plaintext or already decrypted.
    return haslo_enc or ""

def _get_account_info(konto_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, email, serwer_imap, port_imap, ssl_imap, haslo_enc FROM konta_email WHERE id = ?",
            (konto_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()

def fetch_message_from_imap(konto_id: int, folder: str, uid: str, timeout: int = 30) -> Optional[Message]:
    """
    Connect to IMAP, fetch message by UID and return email.message.Message.
    Uses account data from 'konta_email' table for connection and auth.
    """
    account = _get_account_info(konto_id)
    if not account:
        logger.debug("No account info for konto_id=%s", konto_id)
        return None

    host = account.get("serwer_imap")
    port = account.get("port_imap") or None
    ssl_flag = account.get("ssl_imap")
    user = account.get("email")
    password = _decrypt_password(account.get("haslo_enc"))

    if not host or not user:
        logger.debug("Incomplete IMAP credentials for konto_id=%s", konto_id)
        return None

    imap_conn = None
    try:
        # Decide between SSL and plain connection
        if ssl_flag and str(ssl_flag).lower() in ("1", "true", "yes", "ssl"):
            # imaplib.IMAP4_SSL accepts None for default port
            if port:
                imap_conn = imaplib.IMAP4_SSL(host, int(port))
            else:
                imap_conn = imaplib.IMAP4_SSL(host)
        else:
            if port:
                imap_conn = imaplib.IMAP4(host, int(port))
            else:
                imap_conn = imaplib.IMAP4(host)

        # Login
        imap_conn.login(user, password)

        # Select mailbox. Many servers expect modified UTF-7; if selection fails
        # you may need to encode mailbox name. For now try direct select and fallback.
        try:
            typ, data = imap_conn.select(folder, readonly=True)
            if typ != "OK":
                # try with quoted folder or root
                typ, data = imap_conn.select('"%s"' % folder, readonly=True)
                if typ != "OK":
                    logger.debug("IMAP select failed for folder=%s", folder)
        except Exception:
            # fallback attempt (no raise)
            try:
                typ, data = imap_conn.select('"%s"' % folder, readonly=True)
            except Exception as e:
                logger.exception("IMAP select exception for folder %s: %s", folder, e)
                typ = "NO"

        if typ != "OK":
            logger.debug("Could not select folder %s for account %s", folder, konto_id)
            imap_conn.logout()
            return None

        # Fetch message by UID
        typ, fetch_data = imap_conn.uid("FETCH", str(uid), "(RFC822)")
        if typ != "OK" or not fetch_data or fetch_data[0] is None:
            logger.debug("IMAP fetch failed for UID=%s (typ=%s)", uid, typ)
            imap_conn.logout()
            return None

        # fetch_data structure: [(b'UID ... (RFC822 {bytes}', b'...raw...'), b')'] or similar
        raw_bytes = None
        # find the first tuple that contains bytes payload
        for part in fetch_data:
            if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                raw_bytes = part[1]
                break
        if raw_bytes is None:
            # sometimes fetch_data[0][1] is the bytes
            try:
                raw_bytes = fetch_data[0][1]
            except Exception:
                raw_bytes = None

        if raw_bytes is None:
            logger.debug("No raw payload returned for UID=%s", uid)
            imap_conn.logout()
            return None

        # Parse into Message
        try:
            msg = email.message_from_bytes(raw_bytes)
        except Exception:
            try:
                raw_str = raw_bytes.decode("utf-8", errors="replace")
                msg = email.message_from_string(raw_str)
            except Exception:
                logger.exception("Failed to parse message UID=%s", uid)
                imap_conn.logout()
                return None

        imap_conn.logout()
        return msg
    except imaplib.IMAP4.error:
        logger.exception("IMAP error while fetching UID=%s for konto_id=%s", uid, konto_id)
        if imap_conn:
            try:
                imap_conn.logout()
            except Exception:
                pass
        return None
    except Exception:
        logger.exception("Unexpected error while fetching from IMAP for konto_id=%s", konto_id)
        if imap_conn:
            try:
                imap_conn.logout()
            except Exception:
                pass
        return None

# helper safe_header already defined in the original file — keep original implementation
def safe_header(value: str) -> str:
    if not value:
        return ""
    try:
        decoded = email.header.decode_header(value)
        parts: List[str] = []
        for b, enc in decoded:
            if isinstance(b, str):
                parts.append(b)
            else:
                enc = enc or "utf-8"
                if enc.lower() in ("unknown-8bit", "x-unknown", "unknown"):
                    enc = "latin-1"
                try:
                    parts.append(b.decode(enc, errors="replace"))
                except Exception:
                    parts.append(b.decode("latin-1", errors="replace"))
        return "".join(parts)
    except Exception:
        return value

# -----------------------------
# ACCOUNTS / ASSIGNMENTS / FILTERS
# -----------------------------
def get_email_accounts_for_employee(pracownik_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT ke.*
            FROM konta_email ke
            JOIN pracownik_konto_email pke ON pke.konto_id = ke.id
            WHERE pke.pracownik_id = ?
            ORDER BY ke.id
        """,
            (pracownik_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def get_folders_for_account(account_id: int) -> List[str]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT nazwa
            FROM foldery_email
            WHERE konto_id = ?
            ORDER BY nazwa ASC
            """,
            (account_id,),
        )
        rows = cur.fetchall()
        return [r["nazwa"] for r in rows]
    finally:
        conn.close()

def get_all_email_accounts() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                id,
                pracownik_id,
                email,
                serwer_imap,
                CAST(port_imap AS INTEGER) AS port_imap,
                ssl_imap,
                serwer_smtp,
                CAST(port_smtp AS INTEGER) AS port_smtp,
                ssl_smtp,
                haslo_enc,
                aktywne
            FROM konta_email
            ORDER BY id
            """
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def add_email_account_for_employee(
    pracownik_id: Optional[int],
    email: str,
    imap_server: str,
    imap_port: int,
    smtp_server: str,
    smtp_port: int,
    encrypted_password: str,
    imap_ssl: Optional[str] = None,
    smtp_ssl: Optional[str] = None,
    aktywne: int = 1,
) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO konta_email
            (pracownik_id, email, serwer_imap, port_imap, ssl_imap,
             serwer_smtp, port_smtp, ssl_smtp, haslo_enc, aktywne)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pracownik_id,
                email,
                imap_server,
                int(imap_port) if imap_port is not None else None,
                imap_ssl,
                smtp_server,
                int(smtp_port) if smtp_port is not None else None,
                smtp_ssl,
                encrypted_password,
                int(aktywne),
            ),
        )
        konto_id = cur.lastrowid
        conn.commit()
        return konto_id
    finally:
        conn.close()

def delete_email_account(konto_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM konta_email WHERE id = ?", (konto_id,))
        cur.execute("DELETE FROM pracownik_konto_email WHERE konto_id = ?", (konto_id,))
        conn.commit()
    finally:
        conn.close()

def get_email_assignments() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT pke.konto_id, ke.email, pke.pracownik_id, pr.email AS pracownik_email
            FROM pracownik_konto_email pke
            JOIN konta_email ke ON pke.konto_id = ke.id
            JOIN pracownicy pr ON pke.pracownik_id = pr.id
            ORDER BY ke.email, pr.email
            """
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def assign_email_to_employee(konto_id: int, pracownik_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO pracownik_konto_email (konto_id, pracownik_id)
            VALUES (?, ?)
            """,
            (konto_id, pracownik_id),
        )
        conn.commit()
    finally:
        conn.close()

def remove_email_assignment(konto_id: int, pracownik_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM pracownik_konto_email
            WHERE konto_id = ? AND pracownik_id = ?
            """,
            (konto_id, pracownik_id),
        )
        conn.commit()
    finally:
        conn.close()

def get_email_filters_for_employee(pracownik_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT rf.id, rf.pracownik_id, rf.konto_id, rf.firma_id, rf.od, rf.do, rf.nadawca_zawiera,
                   rf.temat_zawiera, rf.tresc_zawiera, rf.folder_docelowy, rf.oznacz_jako_przeczytane,
                   rf.usun, rf.aktywna
            FROM reguly_filtrow rf
            JOIN konta_email ke ON rf.konto_id = ke.id
            WHERE ke.pracownik_id = ?
            ORDER BY rf.id
            """,
            (pracownik_id,),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_email_filters_for_company(firma_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT rf.*, ke.email AS konto_email
            FROM reguly_filtrow rf
            JOIN konta_email ke ON ke.id = rf.konto_id
            WHERE rf.firma_id = ?
            ORDER BY rf.id
            """,
            (firma_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def ensure_contact_email_filters(firma_id: int, folder_name: str) -> int:
    """Create sender rules for all company contacts on every active mail account."""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        contacts = conn.execute(
            """
            SELECT DISTINCT LOWER(TRIM(email)) AS email
            FROM company_contacts
            WHERE firma_id = ? AND email IS NOT NULL AND TRIM(email) <> ''
            """,
            (firma_id,),
        ).fetchall()
        accounts = conn.execute(
            """
            SELECT ke.id,
                   COALESCE(ke.pracownik_id, pke.pracownik_id) AS pracownik_id,
                   ke.email
            FROM konta_email ke
            LEFT JOIN pracownik_konto_email pke ON pke.konto_id = ke.id
            WHERE ke.aktywne = 1
            """
        ).fetchall()
        created = 0
        for contact in contacts:
            sender = contact["email"]
            for account in accounts:
                if account["pracownik_id"] is None:
                    continue
                exists = conn.execute(
                    """
                    SELECT 1
                    FROM reguly_filtrow
                    WHERE konto_id = ? AND firma_id = ?
                      AND LOWER(COALESCE(od, nadawca_zawiera, '')) = ?
                    LIMIT 1
                    """,
                    (account["id"], firma_id, sender),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO reguly_filtrow (
                        pracownik_id, konto_id, firma_id, od, folder_docelowy, aktywna
                    )
                    VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (
                        account["pracownik_id"],
                        account["id"],
                        firma_id,
                        sender,
                        folder_name,
                    ),
                )
                created += 1
        conn.commit()
        return created
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
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO reguly_filtrow (
                konto_id, firma_id, od, do, nadawca_zawiera, temat_zawiera, tresc_zawiera,
                folder_docelowy, oznacz_jako_przeczytane, usun, aktywna
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                konto_id,
                firma_id,
                od,
                do,
                nadawca_zawiera,
                temat_zawiera,
                tresc_zawiera,
                folder_docelowy,
                int(oznacz_jako_przeczytane),
                int(usun),
                int(aktywna),
            ),
        )
        fid = cur.lastrowid
        conn.commit()
        return fid
    finally:
        conn.close()

def get_email_filters_for_message(
    konto_id: int, sender: str, subject: str = "", body: str = ""
) -> List[Dict[str, Any]]:
    """Return matching active rules, including company-specific sender rules."""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM reguly_filtrow
            WHERE konto_id = ? AND aktywna = 1
            ORDER BY firma_id IS NULL, id
            """,
            (konto_id,),
        ).fetchall()
        haystack = " ".join((sender or "", subject or "", body or "")).lower()
        sender_value = (sender or "").lower()
        result = []
        for row in rows:
            rule = dict(row)
            sender_filter = (rule.get("od") or rule.get("nadawca_zawiera") or "").strip().lower()
            subject_filter = (rule.get("temat_zawiera") or "").strip().lower()
            body_filter = (rule.get("tresc_zawiera") or "").strip().lower()
            if sender_filter and sender_filter not in sender_value:
                continue
            if subject_filter and subject_filter not in haystack:
                continue
            if body_filter and body_filter not in haystack:
                continue
            if not sender_filter and not subject_filter and not body_filter:
                continue
            result.append(rule)
        return result
    finally:
        conn.close()

def delete_email_filter(filter_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM reguly_filtrow WHERE id = ?", (filter_id,))
        conn.commit()
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

# -----------------------------
# FOLDERS / MESSAGES (DB helpers used by UI)
# -----------------------------
def ensure_folder_in_db(account_id: int, folder_name: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?", (account_id, folder_name))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)", (account_id, folder_name))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def refresh_email_list(konto_id: int, folder: str) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?", (konto_id, folder))
        row = cur.fetchone()
        if not row:
            return []
        folder_id = row["id"]
        cur.execute(
            """
            SELECT id, uid, nadawca, temat, data, przeczytana
            FROM wiadomosci_email
            WHERE konto_id = ? AND folder_id = ? AND usunieta = 0
            ORDER BY datetime(data) DESC, id DESC
            LIMIT ?
            """,
            (konto_id, folder_id, EMAIL_LOCAL_LIMIT),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def prune_email_storage(
    konto_id: Optional[int] = None,
    limit: int = EMAIL_LOCAL_LIMIT,
) -> int:
    """Keep only the newest local message rows per account and folder.

    The IMAP server remains the source of truth. Older messages can be
    downloaded again on demand by UID, while SQLite remains bounded.
    """
    if limit < 1:
        raise ValueError("Email storage limit must be at least 1.")

    conn = get_conn()
    try:
        account_clause = "WHERE konto_id = ?" if konto_id is not None else ""
        params = (konto_id,) if konto_id is not None else ()
        rows = conn.execute(
            f"""
            SELECT id, konto_id, folder_id
            FROM wiadomosci_email
            {account_clause}
            ORDER BY konto_id, folder_id, datetime(data) DESC, id DESC
            """,
            params,
        ).fetchall()

        kept: Dict[Tuple[int, Optional[int]], int] = {}
        delete_ids: List[int] = []
        for row in rows:
            key = (int(row["konto_id"]), row["folder_id"])
            count = kept.get(key, 0)
            if count >= limit:
                delete_ids.append(int(row["id"]))
            else:
                kept[key] = count + 1

        if delete_ids:
            conn.executemany(
                "DELETE FROM wiadomosci_email WHERE id = ?",
                ((message_id,) for message_id in delete_ids),
            )
            conn.commit()
        return len(delete_ids)
    finally:
        conn.close()

def search_messages(konto_id: int, folder: str, query: str) -> List[Dict[str, Any]]:
    q = f"%{query.lower()}%"
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?", (konto_id, folder))
        row = cur.fetchone()
        if not row:
            return []
        folder_id = row["id"]
        cur.execute(
            """
            SELECT id, uid, nadawca AS od, temat, data, przeczytana
            FROM wiadomosci_email
            WHERE konto_id = ? AND folder_id = ?
              AND (
                    LOWER(nadawca) LIKE ?
                 OR LOWER(temat) LIKE ?
                 OR LOWER(tresc) LIKE ?
              )
            ORDER BY data DESC
            """,
            (konto_id, folder_id, q, q, q),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# -----------------------------
# SINGLE MESSAGE LOAD / ATTACHMENTS
# -----------------------------
def load_message(konto_id: int, folder: str, uid: str) -> Optional[email.message.Message]:
    """
    First attempt to load message from local DB; if not found, fetch via IMAP.
    If downloaded from IMAP, store a minimal local copy.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT wiadomosci_email.uid, wiadomosci_email.tresc
            FROM wiadomosci_email
            JOIN foldery_email fe ON wiadomosci_email.folder_id = fe.id
            WHERE wiadomosci_email.konto_id = ?
              AND fe.nazwa = ?
              AND wiadomosci_email.uid = ?
            LIMIT 1
            """,
            (konto_id, folder, uid),
        )
        row = cur.fetchone()
        if row and row["tresc"]:
            raw = row["tresc"]
            try:
                msg = email.message_from_string(raw)
                return msg
            except Exception:
                logger.exception("Failed to parse local message for konto_id=%s uid=%s", konto_id, uid)
                # fall through to IMAP fetch

        # Not found or parse failed — try IMAP
        msg = fetch_message_from_imap(konto_id, folder, uid)
        if not msg:
            return None

        # store minimal local copy for future reads
        try:
            raw_str = msg.as_string()
            # Attempt to get headers for store_message_minimal
            from_header = safe_header(msg.get("From") or "")
            subject = safe_header(msg.get("Subject") or "")
            date_hdr = msg.get("Date") or ""
            store_message_minimal(
                konto_id=konto_id,
                folder_name=folder,
                uid=uid,
                nadawca=from_header,
                temat=subject,
                data_hdr=date_hdr,
                tresc_raw=raw_str,
                przeczytana=0,
            )
        except Exception:
            logger.exception("Failed to store minimal message copy for konto_id=%s uid=%s", konto_id, uid)

        return msg
    finally:
        conn.close()

def open_attachment(konto_id: int, folder: str, uid: str, index: int) -> Optional[str]:
    msg = load_message(konto_id, folder, uid)
    if not msg:
        return None
    idx = 0
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        cd = part.get("Content-Disposition")
        if cd is None:
            continue
        filename = part.get_filename()
        if not filename:
            continue
        if idx == index:
            data = part.get_payload(decode=True)
            path = attachment_output_path(filename)
            try:
                # write if not exists
                with open(path, "xb") as f:
                    f.write(data)
                return path
            except Exception:
                logger.exception("Failed to write attachment to %s", path)
                return None
        idx += 1
    return None

def download_all_attachments(konto_id: int, folder: str, uid: str, attachments: List[Dict[str, Any]]) -> None:
    for att in attachments:
        open_attachment(konto_id, folder, uid, att["index"])

def get_email_storage_stats(konto_id: Optional[int] = None) -> Dict[str, int]:
    """Return SQLite payload sizes without loading message bodies into Python."""
    conn = get_conn()
    try:
        where = "WHERE konto_id = ?" if konto_id is not None else ""
        params = (konto_id,) if konto_id is not None else ()
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS messages,
                   COALESCE(SUM(length(tresc)), 0) AS body_bytes,
                   COALESCE(SUM(length(temat) + length(nadawca) + length(odbiorca)), 0)
                       AS header_bytes
            FROM wiadomosci_email
            {where}
            """,
            params,
        ).fetchone()
        return {
            "messages": int(row["messages"] or 0),
            "body_bytes": int(row["body_bytes"] or 0),
            "header_bytes": int(row["header_bytes"] or 0),
        }
    finally:
        conn.close()

# -----------------------------
# SINGLE MESSAGE STORE (minimal)
# -----------------------------
def store_message_minimal(
    konto_id: int,
    folder_name: str,
    uid: str,
    nadawca: str,
    temat: str,
    data_hdr: str,
    tresc_raw: Optional[str] = None,
    przeczytana: int = 0,
) -> int:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?", (konto_id, folder_name))
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)", (konto_id, folder_name))
            folder_id = cur.lastrowid
        else:
            folder_id = row["id"]

        cur.execute("SELECT id FROM wiadomosci_email WHERE konto_id = ? AND uid = ?", (konto_id, uid))
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE wiadomosci_email SET nadawca = ?, temat = ?, data = ?, tresc = COALESCE(tresc, ?), przeczytana = ? WHERE konto_id = ? AND uid = ?",
                (nadawca, temat, data_hdr, tresc_raw, przeczytana, konto_id, uid),
            )
            conn.commit()
            return existing["id"]
        else:
            cur.execute(
                """
                INSERT INTO wiadomosci_email
                (konto_id, folder_id, uid, nadawca, temat, data, tresc, przeczytana, usunieta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (konto_id, folder_id, uid, nadawca, temat, data_hdr, tresc_raw or "", przeczytana),
            )
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()

# -----------------------------
# DEBUG / EXPORT HELPERS
# -----------------------------
def list_accounts() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, email, serwer_imap, serwer_smtp, aktywne FROM konta_email ORDER BY id")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
