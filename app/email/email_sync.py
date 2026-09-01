from __future__ import annotations

import codecs
import email
from typing import Optional, List, Tuple


def safe_header(value: str) -> str:
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        decoded = []
        for text, charset in parts:
            if isinstance(text, bytes):
                try:
                    decoded.append(text.decode(charset or "utf-8", errors="replace"))
                except Exception:
                    decoded.append(text.decode("utf-8", errors="replace"))
            else:
                decoded.append(text)
        return "".join(decoded)
    except Exception:
        return value


def decode_imap_utf7(name: bytes | str) -> str:
    if not name:
        return ""
    try:
        s = name.decode("utf-8", errors="replace") if isinstance(name, bytes) else str(name)
        try:
            return codecs.decode(s.encode("ascii", errors="replace"), "imap4-utf-7")
        except Exception:
            try:
                return codecs.decode(s, "imap4-utf-7")
            except Exception:
                return s
    except Exception:
        return str(name)


def encode_imap_utf7(name: str) -> str:
    if not name:
        return ""
    try:
        return codecs.encode(name, "imap4-utf-7").decode("ascii", errors="replace")
    except Exception:
        return name


def extract_folder_name(raw: bytes) -> str:
    if not raw:
        return ""
    line = raw.decode(errors="replace").strip()
    if '"' in line:
        parts = line.split('"')
        if len(parts) >= 2:
            return parts[-2].strip()
    tokens = line.split()
    return tokens[-1].strip() if tokens else ""


def extract_separator(raw: bytes) -> str:
    line = raw.decode(errors="replace")
    if '"' in line:
        parts = line.split('"')
        if len(parts) >= 3:
            return parts[1]
    return "/"


def extract_flags(raw: bytes) -> str:
    line = raw.decode(errors="replace")
    if "(" in line and ")" in line:
        return line.split(")")[0].replace("(", "").strip()
    return ""


def imap_list_folders(imap) -> List[str]:
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
        except Exception:
            pass
    return []


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
        except Exception:
            pass
    return False


def imap_fetch_headers(imap, uid: str) -> Optional[email.message.Message]:
    try:
        typ, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            return None
        return email.message_from_bytes(msg_data[0][1])
    except Exception:
        return None


def imap_fetch_full(imap, uid: str) -> Optional[email.message.Message]:
    try:
        typ, msg_data = imap.fetch(uid, "(BODY[])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            return None
        return email.message_from_bytes(msg_data[0][1])
    except Exception:
        return None


def imap_search_uids(imap) -> List[str]:
    try:
        typ, data = imap.search(None, "ALL")
        if typ != "OK":
            return []
        return [uid.decode() for uid in data[0].split()]
    except Exception:
        return []


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


def imap_create_folder(imap, folder: str) -> bool:
    try:
        typ, _ = imap.create(folder)
        return typ == "OK"
    except Exception:
        return False


def imap_delete_folder(imap, folder: str) -> bool:
    try:
        typ, _ = imap.delete(folder)
        return typ == "OK"
    except Exception:
        return False


def sync_folders(imap, db_folders: List[str]) -> Tuple[List[str], List[str], List[str]]:
    mailboxes = []
    for args in [
        ("INBOX", "*"),
        ("INBOX", "%"),
        ("", "*"),
        ("", "%"),
    ]:
        try:
            typ, boxes = imap.list(*args)
            if typ == "OK" and boxes:
                mailboxes = boxes
                break
        except Exception:
            pass

    imap_folders = [extract_folder_name(m) for m in mailboxes]
    imap_set = set(imap_folders)
    db_set = set(db_folders)
    new_folders = list(imap_set - db_set)
    deleted_folders = list(db_set - imap_set)
    return imap_folders, new_folders, deleted_folders
