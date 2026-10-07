# app/email/email_storage.py
from __future__ import annotations

import re
import os
import email
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from email.message import Message
from app.db.dao_conn import get_conn

logger = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"[^@]+@[^@]+\.[^@]+")

# mapowania folderów (lokalne <-> serwer)
SERVER_TO_LOCAL = {
    "INBOX": "Odebrane",
    "INBOX.Sent": "Wysłane",
    "INBOX.spam": "Spam",
    "INBOX.Spam": "Spam",
    "INBOX.Junk": "Spam",
    "INBOX.Drafts": "Szkice",
    "INBOX.Archive": "Archiwum",
}
LOCAL_TO_SERVER = {
    "Odebrane": "INBOX",
    "Wysłane": "INBOX.Sent",
    "Spam": "INBOX.spam",
    "Szkice": "INBOX.Drafts",
    "Archiwum": "INBOX.Archive",
}

def normalize_folder_name(server_name: str) -> str:
    return SERVER_TO_LOCAL.get(server_name, server_name)

def denormalize_folder_name(local_name: str) -> str:
    return LOCAL_TO_SERVER.get(local_name, local_name)

# helper do bezpiecznego dekodowania nagłówków
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
        cur.execute("""
            SELECT ke.*
            FROM konta_email ke
            JOIN pracownik_konto_email pke ON pke.konto_id = ke.id
            WHERE pke.pracownik_id = ?
            ORDER BY ke.id
        """, (pracownik_id,))
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
            ORDER BY data DESC
            """,
            (konto_id, folder_id),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
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
        if not row:
            return None
        raw = row["tresc"]
        try:
            msg = email.message_from_string(raw)
            return msg
        except Exception:
            return None
    finally:
        conn.close()

def extract_html(msg: email.message.Message) -> Optional[str]:
    if msg is None:
        return None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    try:
                        return part.get_payload(decode=True).decode("utf-8", errors="replace")
                    except Exception:
                        return None
    else:
        if msg.get_content_type() == "text/html":
            try:
                return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                return None
    return None

def get_attachments(msg: email.message.Message) -> List[Dict[str, Any]]:
    if msg is None:
        return []
    attachments: List[Dict[str, Any]] = []
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
        attachments.append({"index": idx, "filename": safe_header(filename)})
        idx += 1
    return attachments

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
            save_dir = os.path.join(os.getcwd(), "tmp_attachments")
            os.makedirs(save_dir, exist_ok=True)
            path = os.path.join(save_dir, safe_header(filename))
            try:
                with open(path, "wb") as f:
                    f.write(data)
                return path
            except Exception:
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
