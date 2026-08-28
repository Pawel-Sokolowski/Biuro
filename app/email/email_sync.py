# app/email/email_sync.py

from __future__ import annotations
import email
import email.header
import imaplib
from typing import Optional, List, Tuple


# =====================================================================
# BEZPIECZNY HEADER
# =====================================================================

def safe_header(value: str) -> str:
    try:
        decoded = email.header.decode_header(value)
        parts = []
        for text, enc in decoded:
            if isinstance(text, bytes):
                parts.append(text.decode(enc or "utf-8", errors="replace"))
            else:
                parts.append(text)
        return "".join(parts)
    except Exception:
        return value


# =====================================================================
# NAJWAŻNIEJSZA FUNKCJA — ZERO PARSOWANIA
# =====================================================================

def extract_folder_name(raw: bytes) -> str:
    """
    Pobiera nazwę folderu IMAP dokładnie tak, jak IMAP ją zwraca.
    Zero parsowania, zero zgadywania.
    """
    try:
        text = raw.decode()
    except:
        text = str(raw)

    # IMAP zawsze trzyma nazwę folderu w ostatnim cudzysłowie
    if '"' in text:
        return text.split('"')[-2].strip()

    # jeśli nie ma cudzysłowów — bierz ostatni token
    return text.split()[-1].strip()


# =====================================================================
# IMAP LIST — pobieranie folderów
# =====================================================================

def imap_list_folders(imap) -> List[str]:
    """
    Pobiera listę folderów IMAP w sposób kompatybilny z każdym serwerem.
    Zwraca listę nazw folderów dokładnie takich, jak IMAP je zwrócił.
    """
    attempts = [
        ("INBOX", "*"),
        ("INBOX", "%"),
        ("", "*"),
        ("", "%"),
    ]

    for args in attempts:
        try:
            typ, boxes = imap.list(*args)
            if typ == "OK" and boxes:
                return [extract_folder_name(b) for b in boxes]
        except:
            pass

    return []


# =====================================================================
# IMAP SELECT — stabilny
# =====================================================================

def imap_safe_select(imap, folder: str) -> bool:
    attempts = [
        (folder, True),
        (folder, False),
        (folder, None),
    ]

    for f, readonly in attempts:
        try:
            if readonly is None:
                status, _ = imap.select(f)
            else:
                status, _ = imap.select(f, readonly=readonly)

            if status == "OK":
                return True
        except:
            pass

    return False


# =====================================================================
# FETCH — nagłówki
# =====================================================================

def imap_fetch_headers(imap, uid: str) -> Optional[email.message.Message]:
    try:
        typ, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            return None
        return email.message_from_bytes(msg_data[0][1])
    except Exception:
        return None


# =====================================================================
# FETCH — pełna wiadomość
# =====================================================================

def imap_fetch_full(imap, uid: str) -> Optional[email.message.Message]:
    try:
        typ, msg_data = imap.fetch(uid, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            return None
        return email.message_from_bytes(msg_data[0][1])
    except Exception:
        return None


# =====================================================================
# SEARCH — lista UID
# =====================================================================

def imap_search_uids(imap) -> List[str]:
    try:
        typ, data = imap.search(None, "ALL")
        if typ != "OK":
            return []
        return [uid.decode() for uid in data[0].split()]
    except Exception:
        return []


# =====================================================================
# FLAGI — przeczytane / nieprzeczytane
# =====================================================================

def imap_mark_read(imap, uid: str) -> bool:
    try:
        typ, _ = imap.store(uid, "+FLAGS", r"(\Seen)")
        return typ == "OK"
    except Exception:
        return False


def imap_mark_unread(imap, uid: str) -> bool:
    try:
        typ, _ = imap.store(uid, "-FLAGS", r"(\Seen)")
        return typ == "OK"
    except Exception:
        return False


# =====================================================================
# PRZENOSZENIE WIADOMOŚCI
# =====================================================================

def imap_move_message(imap, uid: str, target_folder: str) -> bool:
    try:
        typ, _ = imap.copy(uid, target_folder)
        if typ != "OK":
            return False
        imap.store(uid, "+FLAGS", r"(\Deleted)")
        imap.expunge()
        return True
    except Exception:
        return False
# =====================================================================
# IMAP CREATE — tworzenie folderów
# =====================================================================

def imap_create_folder(imap, folder: str) -> bool:
    """
    Tworzy folder na serwerze IMAP.
    """
    try:
        typ, _ = imap.create(folder)
        return typ == "OK"
    except Exception:
        return False


# =====================================================================
# IMAP DELETE — usuwanie folderów
# =====================================================================

def imap_delete_folder(imap, folder: str) -> bool:
    """
    Usuwa folder na serwerze IMAP.
    """
    try:
        typ, _ = imap.delete(folder)
        return typ == "OK"
    except Exception:
        return False


# =====================================================================
# IMAP OPEN — logowanie
# =====================================================================

def imap_open(host: str, port: int, email_addr: str, password: str):
    """
    Łączy z serwerem IMAP i loguje użytkownika.
    """
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(email_addr, password)
        return imap
    except Exception:
        return None


# =====================================================================
# IMAP CLOSE — wylogowanie
# =====================================================================

def imap_close(imap):
    """
    Bezpieczne wylogowanie.
    """
    try:
        imap.logout()
    except:
        pass


# =====================================================================
# SYNCHRONIZACJA FOLDERÓW IMAP ↔ DB (1:1, BEZ PARSOWANIA)
# =====================================================================

def sync_folders(imap, db_folders: list[str]) -> tuple[list[str], list[str], list[str]]:
    """
    Synchronizuje foldery IMAP z bazą danych.

    Zwraca:
    - imap_folders: wszystkie foldery z IMAP (dokładnie takie jak IMAP zwrócił)
    - new_folders: foldery, które są na IMAP, ale nie ma ich w DB
    - deleted_folders: foldery, które są w DB, ale zniknęły z IMAP
    """

    # pobierz foldery 1:1 z IMAP
    mailboxes = []

    for args in [
        ("INBOX", "*"),
        ("INBOX", "%"),
        ("", "*"),
        ("", "%")
    ]:
        try:
            typ, boxes = imap.list(*args)
            if typ == "OK" and boxes:
                mailboxes = boxes
                break
        except:
            pass

    # NAJWAŻNIEJSZE: zero parsowania
    imap_folders = [extract_folder_name(m) for m in mailboxes]

    # porównanie z DB
    imap_set = set(imap_folders)
    db_set = set(db_folders)

    new_folders = list(imap_set - db_set)
    deleted_folders = list(db_set - imap_set)

    return imap_folders, new_folders, deleted_folders
