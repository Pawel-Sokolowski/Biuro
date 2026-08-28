# app/email/email_actions.py

from __future__ import annotations

import email
import email.header
import imaplib
import os
import sqlite3
import traceback
from typing import Any, Dict, List, Optional, Tuple

from app.db.dao_conn import get_conn
from app.email.email_storage import get_email_accounts_for_employee
from app.utils.crypto import decrypt_password

# -----------------------------
# MAPOWANIE NAZW FOLDERÓW
# -----------------------------

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


def parse_mailbox_name(raw: bytes) -> str:
    """
    Parsuje linię z IMAP LIST i wyciąga pełną nazwę folderu,
    bez rozbijania na słowa (ważne dla nazw typu 'ART GROUP SP Z O O').
    """
    s = raw.decode(errors="replace")
    first_quote = s.find('"')
    if first_quote != -1:
        last_quote = s.rfind('"')
        if last_quote > first_quote:
            return s[first_quote + 1:last_quote]
    return s.split()[-1].strip('"')


def normalize_folder_name(server_name: str) -> str:
    """
    Zamienia nazwę folderu z serwera na nazwę lokalną (UI),
    np. INBOX.Sent -> Wysłane.
    Jeśli nie jest folderem systemowym, zwraca oryginalną nazwę.
    """
    return SERVER_TO_LOCAL.get(server_name, server_name)


def denormalize_folder_name(local_name: str) -> str:
    """
    Zamienia nazwę lokalną (UI) na nazwę folderu na serwerze IMAP,
    np. Wysłane -> INBOX.Sent.
    Jeśli nie jest folderem systemowym, zwraca oryginalną nazwę.
    """
    return LOCAL_TO_SERVER.get(local_name, local_name)


# -----------------------------
# KONTA I FOLDERY
# -----------------------------

def load_accounts(pracownik_id: Optional[int]) -> List[Tuple[int, str]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        if pracownik_id is None:
            cur.execute("SELECT id, email FROM konta_email ORDER BY id")
        else:
            cur.execute(
                """
                SELECT ke.id, ke.email
                FROM konta_email ke
                JOIN pracownik_konto_email pke ON pke.konto_id = ke.id
                WHERE pke.pracownik_id = ?
                ORDER BY ke.id
            """,
                (pracownik_id,),
            )
        rows = cur.fetchall()
        return [(r["id"], r["email"]) for r in rows]
    finally:
        conn.close()


def load_all_accounts_folders(pracownik_id: Optional[int]) -> List[Tuple[int, str, List[str]]]:
    """
    Zwraca listę: [(konto_id, adres_email, [lista_folderów_lokalnych]), ...]
    Foldery są już po normalize_folder_name (czyli np. 'Odebrane', 'Wysłane', itd.).
    """
    accounts = load_accounts(pracownik_id)
    result: List[Tuple[int, str, List[str]]] = []
    for kid, addr in accounts:
        folders: List[str] = []
        try:
            conn = get_conn()
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT nazwa FROM foldery_email WHERE konto_id = ? ORDER BY id",
                (kid,),
            )
            rows = cur.fetchall()
            folders = [r["nazwa"] for r in rows]
        except Exception as e:
            print(f"[DEBUG] load_all_accounts_folders error for konto_id={kid}: {e}")
            folders = []
        finally:
            conn.close()
        result.append((kid, addr, folders))
    return result


# -----------------------------
# LISTA WIADOMOŚCI
# -----------------------------

def refresh_email_list(konto_id: int, folder: str) -> List[Dict[str, Any]]:
    """
    Ładuje listę wiadomości dla danego folderu (po nazwie lokalnej),
    sortując po dacie malejąco.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        print(f"[DEBUG] refresh_email_list: konto_id={konto_id}, folder={folder}")
        cur.execute(
            """
            SELECT id FROM foldery_email
            WHERE konto_id = ? AND nazwa = ?
        """,
            (konto_id, folder),
        )
        row = cur.fetchone()

        if not row:
            print(f"[DEBUG] refresh_email_list: folder '{folder}' not found for konto_id={konto_id}")
            return []

        folder_id = row["id"]

        cur.execute(
            """
            SELECT id, uid, nadawca, temat, data, przeczytana
            FROM wiadomosci_email
            WHERE konto_id = ? AND folder_id = ?
            ORDER BY data DESC
        """,
            (konto_id, folder_id),
        )

        rows = cur.fetchall()
        print(f"[DEBUG] refresh_email_list: fetched {len(rows)} messages")
        return [dict(r) for r in rows]

    finally:
        conn.close()


def search_messages(konto_id: int, folder: str, query: str) -> List[Dict[str, Any]]:
    """
    Szuka wiadomości w danym folderze (po nazwie lokalnej),
    po nadawcy, temacie lub treści (LOWER LIKE %query%).
    """
    q = f"%{query.lower()}%"
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        print(f"[DEBUG] search_messages: konto_id={konto_id}, folder={folder}, query={query}")
        cur.execute(
            """
            SELECT id FROM foldery_email
            WHERE konto_id = ? AND nazwa = ?
        """,
            (konto_id, folder),
        )
        row = cur.fetchone()

        if not row:
            print(f"[DEBUG] search_messages: folder '{folder}' not found for konto_id={konto_id}")
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
            ORDER BY id DESC
        """,
            (konto_id, folder_id, q, q, q),
        )

        rows = cur.fetchall()
        print(f"[DEBUG] search_messages: found {len(rows)} messages")
        return [dict(r) for r in rows]

    finally:
        conn.close()

# -----------------------------
# ŁADOWANIE POJEDYNCZEJ WIADOMOŚCI
# -----------------------------

def load_message(konto_id: int, folder: str, uid: str) -> Optional[email.message.Message]:
    """
    Ładuje pełną wiadomość z bazy (tresc = raw RFC822).
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        print(f"[DEBUG] load_message: konto_id={konto_id}, folder={folder}, uid={uid}")

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
            print("[DEBUG] load_message: message not found")
            return None

        raw = row["tresc"]

        try:
            msg = email.message_from_string(raw)
            return msg
        except Exception as e:
            print(f"[DEBUG] load_message: parse error {e}")
            return None

    finally:
        conn.close()


# -----------------------------
# EKSTRAKCJA HTML
# -----------------------------

def extract_html(msg: email.message.Message) -> Optional[str]:
    """
    Zwraca treść HTML z wiadomości, jeśli istnieje.
    """
    if msg is None:
        return None

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                except Exception:
                    try:
                        return part.get_payload(decode=True).decode("utf-8", errors="replace")
                    except Exception:
                        return None
    else:
        if msg.get_content_type() == "text/html":
            try:
                return msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or "utf-8",
                    errors="replace",
                )
            except Exception:
                return None

    return None


# -----------------------------
# ZAŁĄCZNIKI
# -----------------------------

def get_attachments(msg: email.message.Message) -> List[Dict[str, Any]]:
    """
    Zwraca listę załączników:
    [
        {"index": 0, "filename": "..."},
        {"index": 1, "filename": "..."},
        ...
    ]
    """
    if msg is None:
        return []

    attachments: List[Dict[str, Any]] = []
    idx = 0

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition") is None:
            continue

        filename = part.get_filename()
        if not filename:
            continue

        attachments.append({"index": idx, "filename": filename})
        idx += 1

    return attachments


def open_attachment(konto_id: int, folder: str, uid: str, index: int) -> Optional[str]:
    """
    Zapisuje załącznik do pliku i zwraca ścieżkę.
    """
    msg = load_message(konto_id, folder, uid)
    if not msg:
        return None

    idx = 0
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition") is None:
            continue

        filename = part.get_filename()
        if not filename:
            continue

        if idx == index:
            data = part.get_payload(decode=True)

            save_dir = os.path.join(os.getcwd(), "tmp_attachments")
            os.makedirs(save_dir, exist_ok=True)

            path = os.path.join(save_dir, filename)

            try:
                with open(path, "wb") as f:
                    f.write(data)
                return path
            except Exception as e:
                print(f"[DEBUG] open_attachment: write error {e}")
                return None

        idx += 1

    return None


def download_all_attachments(konto_id: int, folder: str, uid: str, attachments: List[Dict[str, Any]]) -> None:
    """
    Pobiera wszystkie załączniki.
    """
    for att in attachments:
        open_attachment(konto_id, folder, uid, att["index"])


# -----------------------------
# FILTRY
# -----------------------------

def add_filter(konto_id: int, od: str, folder: str) -> None:
    """
    Dodaje filtr lokalny (nie serwerowy).
    """
    conn = get_conn()
    cur = conn.cursor()

    try:
        print(f"[DEBUG] add_filter: konto_id={konto_id}, od={od}, folder={folder}")

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
# OZNACZANIE WIADOMOŚCI
# -----------------------------

def mark_read(konto_id: int, uid: str) -> None:
    conn = get_conn()
    cur = conn.cursor()

    try:
        print(f"[DEBUG] mark_read: konto_id={konto_id}, uid={uid}")

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
        print(f"[DEBUG] mark_unread: konto_id={konto_id}, uid={uid}")

        cur.execute(
            "UPDATE wiadomosci_email SET przeczytana = 0 WHERE konto_id = ? AND uid = ?",
            (konto_id, uid),
        )

        conn.commit()

    finally:
        conn.close()


def delete_email(konto_id: int, uid: str) -> None:
    """
    Miękkie usunięcie (usunieta = 1).
    """
    conn = get_conn()
    cur = conn.cursor()

    try:
        print(f"[DEBUG] delete_email: konto_id={konto_id}, uid={uid}")

        cur.execute(
            "UPDATE wiadomosci_email SET usunieta = 1 WHERE konto_id = ? AND uid = ?",
            (konto_id, uid),
        )

        conn.commit()

    finally:
        conn.close()
# -----------------------------
# IMAP — LOGOWANIE DO KONTA
# -----------------------------

def _open_imap_for_account(acc: Dict[str, Any]) -> Optional[imaplib.IMAP4_SSL]:
    """
    Logowanie do IMAP dla danego konta.
    """
    email_addr = acc.get("email")
    host = acc.get("serwer_imap") or ""
    port = int(acc.get("port_imap") or 993)
    enc = acc.get("haslo_enc")

    if not enc:
        print(f"[DEBUG] _open_imap_for_account: no password for {email_addr}")
        return None

    try:
        pwd = decrypt_password(enc)
    except Exception as e:
        print(f"[DEBUG] _open_imap_for_account: decrypt error for {email_addr}: {e}")
        return None

    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(email_addr, pwd)
        print(f"[DEBUG] _open_imap_for_account: IMAP login OK for {email_addr}")
        return imap
    except Exception as e:
        print(f"[IMAP] Błąd połączenia dla {email_addr}: {e}")
        return None


def _get_account_by_id(konto_id: int) -> Optional[Dict[str, Any]]:
    """
    Pobiera konto email po ID.
    """
    accounts = get_email_accounts_for_employee(None)
    for acc in accounts:
        if acc.get("id") == konto_id:
            return acc
    return None


# -----------------------------
# PRZENOSZENIE WIADOMOŚCI (LOCAL + SERVER)
# -----------------------------

def move_email_and_get_sender(konto_id: int, uid: str, target_folder: str) -> Tuple[bool, Optional[str]]:
    """
    Przenosi wiadomość:
    ✔ lokalnie (SQLite)
    ✔ na serwerze IMAP (COPY + DELETE + EXPUNGE)
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        print(f"[DEBUG] move_email_and_get_sender: konto_id={konto_id}, uid={uid}, target_folder={target_folder}")

        # Pobierz nadawcę i folder źródłowy
        cur.execute(
            "SELECT id, nadawca, folder_id FROM wiadomosci_email WHERE konto_id = ? AND uid = ?",
            (konto_id, uid),
        )
        row = cur.fetchone()

        if not row:
            print("[DEBUG] move_email_and_get_sender: message not found")
            return False, None

        nadawca = row["nadawca"]
        src_folder_id = row["folder_id"]

        # Upewnij się, że folder docelowy istnieje lokalnie
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

        # Aktualizacja lokalna
        cur.execute(
            "UPDATE wiadomosci_email SET folder_id = ? WHERE konto_id = ? AND uid = ?",
            (folder_id, konto_id, uid),
        )
        conn.commit()

        # -----------------------------
        # PRZENIESIENIE NA SERWERZE IMAP
        # -----------------------------
        acc = _get_account_by_id(konto_id)
        if acc:
            imap = _open_imap_for_account(acc)
            if imap:
                try:
                    # Pobierz nazwę folderu źródłowego
                    cur.execute(
                        "SELECT nazwa FROM foldery_email WHERE id = ?",
                        (src_folder_id,),
                    )
                    srow = cur.fetchone()
                    src_name_local = srow["nazwa"] if srow else "INBOX"
                    src_name_server = denormalize_folder_name(src_name_local)
                    dst_name_server = denormalize_folder_name(target_folder)

                    # Wybierz folder źródłowy
                    try:
                        imap.select(f'"{src_name_server}"')
                    except Exception:
                        imap.select(src_name_server)

                    # COPY → przeniesienie
                    typ, _ = imap.copy(uid, f'"{dst_name_server}"')

                    if typ == "OK":
                        # Usuń z folderu źródłowego
                        imap.store(uid, "+FLAGS", r"(\Deleted)")
                        imap.expunge()
                    else:
                        print(f"[DEBUG] IMAP copy failed for UID={uid}")

                except Exception as e:
                    print(f"[DEBUG] move_email_and_get_sender: IMAP move error {e}")

                finally:
                    try:
                        imap.logout()
                    except Exception:
                        pass

        return True, nadawca

    finally:
        conn.close()


# -----------------------------
# TWORZENIE FOLDERÓW (LOCAL + SERVER)
# -----------------------------

def create_folder(konto_id: int, name: str, parent_server: Optional[str]) -> bool:
    """
    Tworzy folder:
    ✔ na serwerze IMAP
    ✔ lokalnie w SQLite
    """
    acc = _get_account_by_id(konto_id)
    if not acc:
        print(f"[DEBUG] create_folder: account not found for konto_id={konto_id}")
        return False

    # pełna nazwa na serwerze
    server_name = name if not parent_server else f"{parent_server}.{name}"

    imap = _open_imap_for_account(acc)
    if not imap:
        return False

    ok = False

    try:
        typ, _ = imap.create(f'"{server_name}"')
        if typ == "OK":
            ok = True
        else:
            print(f"[DEBUG] create_folder: IMAP create failed for {server_name}")
    except Exception as e:
        print(f"[DEBUG] create_folder: IMAP error {e}")
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    if not ok:
        return False

    # zapis lokalny
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


# -----------------------------
# USUWANIE FOLDERÓW (LOCAL + SERVER)
# -----------------------------

def delete_folder(konto_id: int, server_name: str) -> bool:
    """
    Usuwa folder:
    ✔ na serwerze IMAP
    ✔ lokalnie w SQLite
    """
    acc = _get_account_by_id(konto_id)
    if not acc:
        print(f"[DEBUG] delete_folder: account not found for konto_id={konto_id}")
        return False

    imap = _open_imap_for_account(acc)
    if not imap:
        return False

    ok = False

    try:
        typ, _ = imap.delete(f'"{server_name}"')
        if typ == "OK":
            ok = True
        else:
            print(f"[DEBUG] delete_folder: IMAP delete failed for {server_name}")
    except Exception as e:
        print(f"[DEBUG] delete_folder: IMAP error {e}")
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    if not ok:
        return False

    # usuń lokalnie
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
# OPRÓŻNIANIE FOLDERÓW (LOCAL + SERVER)
# -----------------------------

def empty_folder(konto_id: int, server_name: str) -> bool:
    """
    Opróżnia folder:
    ✔ usuwa wszystkie wiadomości na serwerze IMAP
    ✔ oznacza wiadomości jako usunięte lokalnie
    """
    acc = _get_account_by_id(konto_id)
    if not acc:
        print(f"[DEBUG] empty_folder: account not found for konto_id={konto_id}")
        return False

    imap = _open_imap_for_account(acc)
    if not imap:
        return False

    ok = False

    try:
        # wybierz folder
        try:
            imap.select(f'"{server_name}"')
        except Exception:
            imap.select(server_name)

        typ, data = imap.search(None, "ALL")
        if typ != "OK":
            print(f"[DEBUG] empty_folder: search failed for {server_name}")
        else:
            uids = data[0].split()
            for uid in uids:
                imap.store(uid, "+FLAGS", r"(\Deleted)")
            imap.expunge()
            ok = True

    except Exception as e:
        print(f"[DEBUG] empty_folder: IMAP error {e}")

    finally:
        try:
            imap.logout()
        except Exception:
            pass

    if not ok:
        return False

    # lokalnie oznacz jako usunięte
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
# SYNC WSZYSTKICH MAILI (IMAP → SQLite)
# -----------------------------

def sync_all_emails(pracownik_id: int) -> bool:
    """
    Pobiera wszystkie foldery i wiadomości z IMAP i zapisuje je lokalnie.
    Obsługuje:
    ✔ foldery systemowe (mapowanie)
    ✔ foldery użytkownika
    ✔ podfoldery (hierarchia IMAP)
    ✔ dekodowanie UTF7-IMAP
    ✔ bez rozbijania nazw na słowa
    """
    print(f"[DEBUG] sync_all_emails: pracownik_id={pracownik_id}")

    accounts = get_email_accounts_for_employee(pracownik_id)
    print(f"[DEBUG] sync_all_emails: accounts={accounts}")

    success_any = False

    for acc in accounts:
        try:
            imap = _open_imap_for_account(acc)
            if not imap:
                continue

            email_addr = acc.get("email")

            # -----------------------------
            # LISTA FOLDERÓW
            # -----------------------------
            try:
                typ, mailboxes = imap.list()
                print(f"[DEBUG] sync_all_emails: mailboxes for {email_addr}: {mailboxes}")
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

            # -----------------------------
            # WIADOMOŚCI
            # -----------------------------
            conn = get_conn()
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            try:
                cur.execute(
                    "SELECT id, nazwa FROM foldery_email WHERE konto_id = ?",
                    (acc["id"],),
                )
                folders = cur.fetchall()

                print(f"[DEBUG] sync_all_emails: DB folders for konto_id={acc['id']}: {folders}")

                for f in folders:
                    fname_local = f["nazwa"]
                    fname_server = denormalize_folder_name(fname_local)

                    try:
                        try:
                            imap.select(f'"{fname_server}"', readonly=True)
                        except Exception:
                            imap.select(fname_server, readonly=True)

                        print(f"[DEBUG] sync_all_emails: selected folder {fname_server}")

                    except Exception as e:
                        print(f"[DEBUG] sync_all_emails: cannot select folder {fname_server}: {e}")
                        continue

                    typ, data = imap.search(None, "ALL")
                    print(f"[DEBUG] sync_all_emails: search result type={typ}, data={data}")

                    if typ != "OK":
                        continue

                    uids = data[0].split()
                    uids = uids[-200:]  # ostatnie 200 wiadomości

                    print(f"[DEBUG] sync_all_emails: {len(uids)} UIDs in {fname_server}")

                    for uid in uids:
                        try:
                            uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)

                            cur.execute(
                                "SELECT id FROM wiadomosci_email WHERE konto_id = ? AND uid = ?",
                                (acc["id"], uid_str),
                            )

                            if cur.fetchone():
                                continue

                            typ, msg_data = imap.fetch(uid, "(RFC822)")

                            if typ != "OK" or not msg_data or not msg_data[0]:
                                print(f"[DEBUG] sync_all_emails: fetch failed for UID={uid_str}")
                                continue

                            raw = msg_data[0][1]

                            raw_bytes = (
                                raw
                                if isinstance(raw, (bytes, bytearray))
                                else str(raw).encode("utf-8", errors="replace")
                            )

                            try:
                                msg = email.message_from_bytes(raw_bytes)
                            except Exception as e:
                                print(f"[DEBUG] sync_all_emails: parse error for UID={uid_str}: {e}")
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
                            raw_s = raw_bytes.decode("utf-8", errors="replace")

                            cur.execute(
                                """
                                INSERT INTO wiadomosci_email
                                (konto_id, folder_id, uid, nadawca, odbiorca, temat, data, tresc, przeczytana, usunieta)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                            """,
                                (
                                    acc["id"],
                                    folder_id,
                                    uid_str,
                                    nadawca,
                                    email_addr,
                                    temat,
                                    data_hdr,
                                    raw_s,
                                ),
                            )

                        except Exception as e:
                            print("sync email store error:", e)

                conn.commit()

            finally:
                conn.close()

            try:
                imap.logout()
            except Exception:
                pass

            success_any = True

        except Exception:
            print("sync account outer error:", traceback.format_exc())

    return success_any

