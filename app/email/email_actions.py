# app/email/email_actions.py  -- część 1/4
from __future__ import annotations

import email
import email.header
import imaplib
import smtplib
import os
import sqlite3
import traceback
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Iterable

from app.db.dao_conn import get_conn
from app.config import EMAIL_LOCAL_LIMIT
from app.email.email_storage import (
    get_email_accounts_for_employee,
    get_email_filters_for_message,
    prune_email_storage,
)
from app.email.email_helpers import (
    SERVER_TO_LOCAL, LOCAL_TO_SERVER, normalize_folder_name,
    denormalize_folder_name, safe_header, extract_html, get_attachments,
)
from app.utils.crypto import decrypt_password

# MAPOWANIE NAZW FOLDERÓW
def parse_mailbox_name(raw: bytes) -> str:
    s = raw.decode(errors="replace")
    first_quote = s.find('"')
    if first_quote != -1:
        last_quote = s.rfind('"')
        if last_quote > first_quote:
            return s[first_quote + 1:last_quote]
    return s.split()[-1].strip('"')

# KONTA I LOGOWANIE IMAP
def _get_account_by_id(konto_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM konta_email WHERE id = ? AND COALESCE(aktywne, 1) = 1",
            (konto_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def _open_imap_for_account(acc: Dict[str, Any]) -> Optional[imaplib.IMAP4_SSL]:
    email_addr = acc.get("email")
    host = acc.get("serwer_imap") or ""
    port = int(acc.get("port_imap") or 993)
    enc = acc.get("haslo_enc")
    if not enc:
        return None
    try:
        pwd = decrypt_password(enc)
    except Exception:
        return None
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(email_addr, pwd)
        return imap
    except Exception:
        return None

# SMTP / WYSYŁKA (pojedyncze)
def get_smtp_config_for_account(konto_id: int) -> dict:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT serwer_smtp, port_smtp, email, haslo_enc, display_name FROM konta_email WHERE id = ?", (konto_id,))
        r = cur.fetchone()
        if not r:
            raise ValueError(f"konto {konto_id} nie znalezione")
        pwd = decrypt_password(r["haslo_enc"]) if r["haslo_enc"] else ""
        return {
            "host": r["serwer_smtp"],
            "port": int(r["port_smtp"] or 587),
            "user": r["email"],
            "password": pwd,
            "display_name": r["display_name"] or r["email"]
        }
    finally:
        conn.close()

def prepare_message(from_addr: str, from_name: str, to_addrs: Iterable[str],
                    subject: str, body: str, attachments: Optional[List[str]] = None,
                    cc: Optional[Iterable[str]] = None, bcc: Optional[Iterable[str]] = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = ", ".join(to_addrs) if isinstance(to_addrs, (list, tuple)) else str(to_addrs)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject or ""
    msg.set_content(body or "")
    if attachments:
        for p in attachments:
            try:
                path = Path(p)
                if not path.exists():
                    continue
                maintype, subtype = ("application", "octet-stream")
                with open(path, "rb") as f:
                    data = f.read()
                msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
            except Exception:
                continue
    return msg

def send_email_smtp(konto_id: int, to_addrs: Iterable[str], subject: str, body: str,
                    attachments: Optional[List[str]] = None, cc: Optional[Iterable[str]] = None,
                    bcc: Optional[Iterable[str]] = None, timeout: int = 30) -> Tuple[bool, str]:
    try:
        cfg = get_smtp_config_for_account(konto_id)
        msg = prepare_message(cfg["user"], cfg["display_name"], to_addrs, subject, body, attachments, cc, bcc)
        all_recipients = list(to_addrs or []) + list(cc or []) + list(bcc or [])
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=timeout) as s:
            s.starttls()
            if cfg["password"]:
                s.login(cfg["user"], cfg["password"])
            s.send_message(msg, from_addr=cfg["user"], to_addrs=all_recipients)
        return True, "OK"
    except Exception as e:
        tb = traceback.format_exc()
        return False, f"{e}\n{tb}"

def send_batch_emails(konto_id: int, messages: List[Dict[str, Any]], retry: int = 2) -> List[Dict[str, Any]]:
    results = []
    for m in messages:
        ok = False
        last_err = ""
        for attempt in range(retry + 1):
            ok, msg = send_email_smtp(
                konto_id,
                m.get("to") or [],
                m.get("subject") or "",
                m.get("body") or "",
                attachments=m.get("attachments"),
                cc=m.get("cc"),
                bcc=m.get("bcc")
            )
            if ok:
                break
            last_err = msg
        results.append({"message": m, "ok": ok, "info": "OK" if ok else last_err})
    return results

# -----------------------------
# ŁADOWANIE POJEDYNCZEJ WIADOMOŚCI (z DB)
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

# -----------------------------
# FILTRY (DB)
# -----------------------------
def add_filter(konto_id: int, od: str, folder: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO reguly_filtrow (pracownik_id, konto_id, od, folder_docelowy, aktywna)
            VALUES (
                (SELECT pracownik_id FROM konta_email WHERE id = ?),
                ?, ?, ?, 1
            )
        """,
            (konto_id, konto_id, od, folder),
        )
        conn.commit()
    finally:
        conn.close()

# -----------------------------
# OZNACZANIE WIADOMOŚCI (lokalnie)
# -----------------------------
def mark_read(konto_id: int, uid: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE wiadomosci_email SET przeczytana = 1 WHERE konto_id = ? AND uid = ?",
            (konto_id, uid),
        )
        conn.commit()
    finally:
        conn.close()

def mark_unread(konto_id: int, uid: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE wiadomosci_email SET przeczytana = 0 WHERE konto_id = ? AND uid = ?",
            (konto_id, uid),
        )
        conn.commit()
    finally:
        conn.close()

def delete_email(konto_id: int, uid: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE wiadomosci_email SET usunieta = 1 WHERE konto_id = ? AND uid = ?",
            (konto_id, uid),
        )
        conn.commit()
    finally:
        conn.close()

# -----------------------------
# FETCH HEADERS FROM IMAP -> STORE IN DB
# -----------------------------
def fetch_headers_from_imap_and_store(konto_id: int, server_folder: str, imap_host: str = None, imap_port: int = 993) -> Tuple[int, int]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT serwer_imap, port_imap, email, haslo_enc FROM konta_email WHERE id = ?", (konto_id,))
        r = cur.fetchone()
        if not r:
            raise ValueError("konto nie znalezione")
        host = imap_host or r["serwer_imap"]
        port = int(imap_port or r["port_imap"] or 993)
        pwd = decrypt_password(r["haslo_enc"]) if r["haslo_enc"] else ""
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(r["email"], pwd)
        try:
            status, _ = imap.select(f'"{server_folder}"')
            if status != "OK":
                status, _ = imap.select(server_folder)
            if status != "OK":
                raise RuntimeError(f"Nie można wybrać folderu {server_folder}")
        except Exception:
            imap.logout()
            raise
        typ, data = imap.search(None, "ALL")
        if typ != "OK":
            imap.logout()
            raise RuntimeError("search failed")
        uids = data[0].split()
        # Headers are retained only for the newest local window. The server
        # remains authoritative for older UIDs and full message bodies.
        uids = uids[-EMAIL_LOCAL_LIMIT:]
        fetched = 0
        updated = 0
        for uid in uids:
            uid_b = uid if isinstance(uid, bytes) else uid.encode()
            try:
                typ_h, msg_data = imap.fetch(uid_b, "(BODY.PEEK[HEADER])")
                if typ_h != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not raw:
                    continue
                hdr = email.message_from_bytes(raw)
                nadawca = safe_header(hdr.get("From", ""))
                temat = safe_header(hdr.get("Subject", ""))
                data_hdr = safe_header(hdr.get("Date", ""))
                typ_f, flags_data = imap.fetch(uid_b, "(FLAGS)")
                flags_str = ""
                try:
                    if flags_data and flags_data[0]:
                        flags_str = flags_data[0][0].decode(errors="replace") if isinstance(flags_data[0][0], (bytes, bytearray)) else str(flags_data[0])
                except Exception:
                    flags_str = ""
                przeczytana = 1 if ("\\Seen" in flags_str or "\\SEEN" in flags_str) else 0
                cur.execute("SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?", (konto_id, server_folder))
                row = cur.fetchone()
                if not row:
                    cur.execute("INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)", (konto_id, server_folder))
                    folder_id = cur.lastrowid
                else:
                    folder_id = row["id"]
                uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
                cur.execute("SELECT id FROM wiadomosci_email WHERE konto_id = ? AND uid = ?", (konto_id, uid_str))
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        "UPDATE wiadomosci_email SET nadawca = ?, temat = ?, data = ?, przeczytana = ?, folder_id = ? WHERE konto_id = ? AND uid = ?",
                        (nadawca, temat, data_hdr, przeczytana, folder_id, konto_id, uid_str),
                    )
                    updated += 1
                else:
                    cur.execute(
                        "INSERT INTO wiadomosci_email (konto_id, folder_id, uid, nadawca, temat, data, przeczytana) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (konto_id, folder_id, uid_str, nadawca, temat, data_hdr, przeczytana),
                    )
                    fetched += 1
                conn.commit()
            except Exception:
                continue
        prune_email_storage(konto_id)
        imap.logout()
        return fetched, updated
    finally:
        conn.close()

def fetch_message_from_imap_and_store(
    konto_id: int,
    server_folder: str,
    uid: str,
    imap_host: str = None,
    imap_port: int = 993
) -> bool:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # pobranie danych konta
        cur.execute(
            "SELECT serwer_imap, port_imap, email, haslo_enc FROM konta_email WHERE id = ?",
            (konto_id,)
        )
        r = cur.fetchone()
        if not r:
            raise ValueError("konto nie znalezione")

        host = imap_host or r["serwer_imap"]
        port = int(imap_port or r["port_imap"] or 993)
        pwd = decrypt_password(r["haslo_enc"]) if r["haslo_enc"] else ""

        # połączenie IMAP
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(r["email"], pwd)

        # wybór folderu
        try:
            status, _ = imap.select(f'"{server_folder}"')
            if status != "OK":
                status, _ = imap.select(server_folder)
            if status != "OK":
                raise RuntimeError(f"Nie można wybrać folderu {server_folder}")
        except Exception:
            imap.logout()
            raise

        # pobranie pełnej wiadomości
        typ, data = imap.fetch(
            uid.encode() if isinstance(uid, str) else uid,
            "(BODY[])"
        )

        if typ != "OK" or not data or not data[0]:
            imap.logout()
            return False

        raw = data[0][1]
        if not raw:
            imap.logout()
            return False

        raw_str = (
            raw.decode("utf-8", errors="replace")
            if isinstance(raw, (bytes, bytearray))
            else str(raw)
        )

        # folder_id
        cur.execute(
            "SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?",
            (konto_id, server_folder)
        )
        row = cur.fetchone()

        if not row:
            cur.execute(
                "INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)",
                (konto_id, server_folder)
            )
            folder_id = cur.lastrowid
        else:
            folder_id = row["id"]

        # zapis treści
        cur.execute(
            "SELECT id FROM wiadomosci_email WHERE konto_id = ? AND uid = ?",
            (konto_id, uid)
        )
        existing = cur.fetchone()

        if existing:
            cur.execute(
                "UPDATE wiadomosci_email SET tresc = ? WHERE konto_id = ? AND uid = ?",
                (raw_str, konto_id, uid)
            )
        else:
            cur.execute(
                """
                INSERT INTO wiadomosci_email (konto_id, folder_id, uid, tresc)
                VALUES (?, ?, ?, ?)
                """,
                (konto_id, folder_id, uid, raw_str)
            )

        conn.commit()

        # ---------------------------------------------------------
        # CACHE 300 — usuń treść ze starszych wiadomości
        # ---------------------------------------------------------
        cur.execute("""
            SELECT id FROM wiadomosci_email
            WHERE konto_id = ? AND tresc IS NOT NULL
            ORDER BY id DESC
            LIMIT -1 OFFSET 300
        """, (konto_id,))
        rows_to_delete = cur.fetchall()

        for rdel in rows_to_delete:
            cur.execute(
                "UPDATE wiadomosci_email SET tresc = NULL WHERE id = ?",
                (rdel["id"],)
            )

        conn.commit()
        # ---------------------------------------------------------

        imap.logout()
        return True

    finally:
        conn.close()

# -----------------------------
# MARK READ/UNREAD ON SERVER + DB
# -----------------------------
def mark_read_remote(konto_id: int, server_folder: str, uid: str) -> bool:
    try:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, email, haslo_enc FROM konta_email WHERE id = ?", (konto_id,))
        r = cur.fetchone()
        if not r:
            return False
        host = r["serwer_imap"]
        port = int(r["port_imap"] or 993)
        pwd = decrypt_password(r["haslo_enc"]) if r["haslo_enc"] else ""
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(r["email"], pwd)
        try:
            status, _ = imap.select(f'"{server_folder}"')
            if status != "OK":
                status, _ = imap.select(server_folder)
            if status != "OK":
                imap.logout()
                return False
            typ, _ = imap.store(uid, "+FLAGS", r"(\Seen)")
            imap.logout()
            if typ == "OK":
                cur.execute("UPDATE wiadomosci_email SET przeczytana = 1 WHERE konto_id = ? AND uid = ?", (konto_id, uid))
                conn.commit()
                return True
        except Exception:
            try:
                imap.logout()
            except Exception:
                pass
            raise
    except Exception:
        # swallow and return False
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

def mark_unread_remote(konto_id: int, server_folder: str, uid: str) -> bool:
    try:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, email, haslo_enc FROM konta_email WHERE id = ?", (konto_id,))
        r = cur.fetchone()
        if not r:
            return False
        host = r["serwer_imap"]
        port = int(r["port_imap"] or 993)
        pwd = decrypt_password(r["haslo_enc"]) if r["haslo_enc"] else ""
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(r["email"], pwd)
        try:
            status, _ = imap.select(f'"{server_folder}"')
            if status != "OK":
                status, _ = imap.select(server_folder)
            if status != "OK":
                imap.logout()
                return False
            typ, _ = imap.store(uid, "-FLAGS", r"(\Seen)")
            imap.logout()
            if typ == "OK":
                cur.execute("UPDATE wiadomosci_email SET przeczytana = 0 WHERE konto_id = ? AND uid = ?", (konto_id, uid))
                conn.commit()
                return True
        except Exception:
            try:
                imap.logout()
            except Exception:
                pass
            raise
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

# -----------------------------
# DELETE REMOTE (and mark local)
# -----------------------------
def delete_email_remote(konto_id: int, server_folder: str, uid: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    try:
        acc = _get_account_by_id(konto_id)
        if not acc:
            return False
        imap = _open_imap_for_account(acc)
        if not imap:
            return False
        try:
            try:
                imap.select(f'"{server_folder}"')
            except Exception:
                imap.select(server_folder)
            typ, _ = imap.store(uid, "+FLAGS", r"(\Deleted)")
            if typ == "OK":
                imap.expunge()
            try:
                imap.logout()
            except Exception:
                pass
        except Exception:
            try:
                imap.logout()
            except Exception:
                pass
            return False
        cur.execute("UPDATE wiadomosci_email SET usunieta = 1 WHERE konto_id = ? AND uid = ?", (konto_id, uid))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

# -----------------------------
# EMPTY FOLDER (remote + local mark)
# -----------------------------
def empty_folder(konto_id: int, server_name: str) -> bool:
    acc = _get_account_by_id(konto_id)
    if not acc:
        return False
    imap = _open_imap_for_account(acc)
    if not imap:
        return False
    ok = False
    try:
        try:
            imap.select(f'"{server_name}"')
        except Exception:
            imap.select(server_name)
        typ, data = imap.search(None, "ALL")
        if typ != "OK":
            pass
        else:
            uids = data[0].split()
            for uid in uids:
                imap.store(uid, "+FLAGS", r"(\Deleted)")
            imap.expunge()
            ok = True
    except Exception:
        ok = False
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    if not ok:
        return False
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?",
            (konto_id, server_name),
        )
        row = cur.fetchone()
        if row:
            folder_id = row["id"]
            cur.execute(
                "UPDATE wiadomosci_email SET usunieta = 1 WHERE konto_id = ? AND folder_id = ?",
                (konto_id, folder_id),
            )
            conn.commit()
    finally:
        conn.close()
    return True

# -----------------------------
# MOVE EMAIL (local + server)
# -----------------------------
def move_email_and_get_sender(konto_id: int, uid: str, target_folder: str) -> Tuple[bool, Optional[str]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, nadawca, folder_id FROM wiadomosci_email WHERE konto_id = ? AND uid = ?",
            (konto_id, uid),
        )
        row = cur.fetchone()
        if not row:
            return False, None
        nadawca = row["nadawca"]
        src_folder_id = row["folder_id"]
        cur.execute(
            "SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?",
            (konto_id, target_folder),
        )
        frow = cur.fetchone()
        if not frow:
            cur.execute(
                "INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)",
                (konto_id, target_folder),
            )
            folder_id = cur.lastrowid
        else:
            folder_id = frow["id"]
        cur.execute(
            "UPDATE wiadomosci_email SET folder_id = ? WHERE konto_id = ? AND uid = ?",
            (folder_id, konto_id, uid),
        )
        conn.commit()
        acc = _get_account_by_id(konto_id)
        if acc:
            imap = _open_imap_for_account(acc)
            if imap:
                try:
                    cur.execute(
                        "SELECT nazwa FROM foldery_email WHERE id = ?",
                        (src_folder_id,),
                    )
                    srow = cur.fetchone()
                    src_name_local = srow["nazwa"] if srow else "INBOX"
                    src_name_server = denormalize_folder_name(src_name_local)
                    dst_name_server = denormalize_folder_name(target_folder)
                    try:
                        imap.select(f'"{src_name_server}"')
                    except Exception:
                        imap.select(src_name_server)
                    typ, _ = imap.copy(uid, f'"{dst_name_server}"')
                    if typ == "OK":
                        imap.store(uid, "+FLAGS", r"(\Deleted)")
                        imap.expunge()
                    else:
                        pass
                except Exception:
                    pass
                finally:
                    try:
                        imap.logout()
                    except Exception:
                        pass
        return True, nadawca
    finally:
        conn.close()

# -----------------------------
# CREATE / DELETE FOLDER (remote + local)
# -----------------------------
def create_folder(konto_id: int, name: str, parent_server: Optional[str]) -> bool:
    acc = _get_account_by_id(konto_id)
    if not acc:
        return False
    server_name = name if not parent_server else f"{parent_server}.{name}"
    imap = _open_imap_for_account(acc)
    if not imap:
        return False
    ok = False
    try:
        typ, _ = imap.create(f'"{server_name}"')
        if typ == "OK":
            ok = True
    except Exception:
        ok = False
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    if not ok:
        return False
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)",
            (konto_id, server_name),
        )
        conn.commit()
    finally:
        conn.close()
    return True

def delete_folder(konto_id: int, server_name: str) -> bool:
    acc = _get_account_by_id(konto_id)
    if not acc:
        return False
    imap = _open_imap_for_account(acc)
    if not imap:
        return False
    ok = False
    try:
        typ, _ = imap.delete(f'"{server_name}"')
        if typ == "OK":
            ok = True
    except Exception:
        ok = False
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    if not ok:
        return False
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM foldery_email WHERE konto_id = ? AND nazwa = ?",
            (konto_id, server_name),
        )
        conn.commit()
    finally:
        conn.close()
    return True

# -----------------------------
# LIST FOLDERS FROM IMAP -> STORE
# -----------------------------
def list_folders_from_imap_and_store(konto_id: int) -> List[str]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT serwer_imap, port_imap, email, haslo_enc FROM konta_email WHERE id = ?", (konto_id,))
        r = cur.fetchone()
        if not r:
            return []
        host = r["serwer_imap"]
        port = int(r["port_imap"] or 993)
        pwd = decrypt_password(r["haslo_enc"]) if r["haslo_enc"] else ""
        try:
            imap = imaplib.IMAP4_SSL(host, port)
            imap.login(r["email"], pwd)
        except Exception:
            return []
        folders = []
        for args in [("INBOX", "*"), ("INBOX", "%"), ("", "*"), ("", "%")]:
            try:
                typ, boxes = imap.list(*args)
                if typ == "OK" and boxes:
                    for b in boxes:
                        try:
                            name = parse_mailbox_name(b)
                            if name and name != ".":
                                folders.append(name)
                                cur.execute("SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?", (konto_id, name))
                                if not cur.fetchone():
                                    cur.execute("INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)", (konto_id, name))
                        except Exception:
                            continue
                    conn.commit()
            except Exception:
                continue
        try:
            imap.logout()
        except Exception:
            pass
        return folders
    finally:
        conn.close()

# -----------------------------
# SYNC ALL EMAILS (IMAP -> SQLite)
# -----------------------------
def sync_all_emails(pracownik_id: int) -> bool:
    accounts = get_email_accounts_for_employee(pracownik_id)
    success_any = False
    for acc in accounts:
        try:
            imap = _open_imap_for_account(acc)
            if not imap:
                continue
            email_addr = acc.get("email")
            try:
                typ, mailboxes = imap.list()
            except Exception:
                mailboxes = []
            conn = get_conn()
            cur = conn.cursor()
            try:
                for m in mailboxes:
                    try:
                        name_server = parse_mailbox_name(m)
                    except Exception:
                        name_server = "INBOX"
                    name_local = normalize_folder_name(name_server)
                    cur.execute(
                        "SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?",
                        (acc["id"], name_local),
                    )
                    if not cur.fetchone():
                        cur.execute(
                            "INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)",
                            (acc["id"], name_local),
                        )
                conn.commit()
            finally:
                conn.close()
            conn = get_conn()
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT id, nazwa FROM foldery_email WHERE konto_id = ?",
                    (acc["id"],),
                )
                folders = cur.fetchall()
                for f in folders:
                    fname_local = f["nazwa"]
                    fname_server = denormalize_folder_name(fname_local)
                    try:
                        try:
                            imap.select(f'"{fname_server}"', readonly=True)
                        except Exception:
                            imap.select(fname_server, readonly=True)
                    except Exception:
                        continue
                    typ, data = imap.search(None, "ALL")
                    if typ != "OK":
                        continue
                    uids = data[0].split()
                    uids = uids[-EMAIL_LOCAL_LIMIT:]
                    for uid in uids:
                        try:
                            uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
                            cur.execute(
                                "SELECT id FROM wiadomosci_email WHERE konto_id = ? AND uid = ?",
                                (acc["id"], uid_str),
                            )
                            if cur.fetchone():
                                continue
                            typ, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER])")
                            if typ != "OK" or not msg_data or not msg_data[0]:
                                continue
                            raw = msg_data[0][1]
                            raw_bytes = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8", errors="replace")
                            try:
                                msg = email.message_from_bytes(raw_bytes)
                            except Exception:
                                msg = None
                            if msg:
                                nadawca = safe_header(msg.get("From", ""))
                                temat = safe_header(msg.get("Subject", ""))
                                data_hdr = safe_header(msg.get("Date", ""))
                            else:
                                nadawca = ""
                                temat = ""
                                data_hdr = ""
                            folder_id = f["id"]
                            przeczytana = 0
                            usunieta = 0
                            matching_rules = get_email_filters_for_message(
                                acc["id"], nadawca, temat, raw_bytes.decode("utf-8", errors="replace")
                            )
                            for rule in matching_rules:
                                target_folder = (rule.get("folder_docelowy") or "").strip()
                                if not target_folder:
                                    continue
                                cur.execute(
                                    "SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?",
                                    (acc["id"], target_folder),
                                )
                                target_row = cur.fetchone()
                                if target_row:
                                    folder_id = target_row["id"] if isinstance(target_row, sqlite3.Row) else target_row[0]
                                else:
                                    cur.execute(
                                        "INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)",
                                        (acc["id"], target_folder),
                                    )
                                    folder_id = cur.lastrowid
                                if rule.get("oznacz_jako_przeczytane"):
                                    przeczytana = 1
                                if rule.get("usun"):
                                    usunieta = 1
                                break
                            cur.execute(
                                """
                                INSERT INTO wiadomosci_email
                                (konto_id, folder_id, uid, nadawca, odbiorca, temat, data, tresc, przeczytana, usunieta)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                                (
                                    acc["id"],
                                    folder_id,
                                    uid_str,
                                    nadawca,
                                    email_addr,
                                    temat,
                                    data_hdr,
                                    None,
                                    przeczytana,
                                    usunieta,
                                ),
                            )
                        except Exception:
                            pass
                conn.commit()
            finally:
                conn.close()
            try:
                prune_email_storage(acc["id"])
            except Exception:
                pass
            try:
                imap.logout()
            except Exception:
                pass
            success_any = True
        except Exception:
            pass
    return success_any

def get_account_info(konto_id: int) -> Optional[Dict[str, Any]]:
    """
    Zwraca wiersz konta (dict) lub None.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM konta_email WHERE id = ?", (konto_id,))
        r = cur.fetchone()
        return dict(r) if r else None
    finally:
        conn.close()

def ensure_local_folder_exists(konto_id: int, folder_name: str) -> int:
    """
    Upewnia się, że folder istnieje w tabeli foldery_email.
    Zwraca folder_id.
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM foldery_email WHERE konto_id = ? AND nazwa = ?", (konto_id, folder_name))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("INSERT INTO foldery_email (konto_id, nazwa) VALUES (?, ?)", (konto_id, folder_name))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def list_accounts() -> List[Dict[str, Any]]:
    """
    Zwraca listę wszystkich kont (do debugu / UI).
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, email, serwer_imap, serwer_smtp, aktywne FROM konta_email ORDER BY id")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def safe_uid(u: Any) -> str:
    """
    Normalizuje UID do stringa.
    """
    if u is None:
        return ""
    if isinstance(u, bytes):
        try:
            return u.decode()
        except Exception:
            return str(u)
    return str(u)

__all__ = [
    "list_folders_from_imap_and_store",
    "fetch_headers_from_imap_and_store",
    "fetch_message_from_imap_and_store",
    "mark_read_remote",
    "mark_unread_remote",
    "delete_email_remote",
    "empty_folder",
    "move_email_and_get_sender",
    "create_folder",
    "delete_folder",
    "sync_all_emails",
    "send_email_smtp",
    "send_batch_emails",
    "load_message",
    "extract_html",
    "get_attachments",
    "open_attachment",
    "download_all_attachments",
    "add_filter",
    "mark_read",
    "mark_unread",
    "delete_email",
    "get_account_info",
    "ensure_local_folder_exists",
    "list_accounts",
    "safe_uid",
    "safe_header",
    "parse_mailbox_name",
    "normalize_folder_name",
    "denormalize_folder_name",
]