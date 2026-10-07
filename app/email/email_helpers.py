from __future__ import annotations

import email
from email.message import Message
from typing import Any, Dict, List, Optional


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


def safe_header(value: str) -> str:
    if not value:
        return ""
    try:
        parts = []
        for value_part, encoding in email.header.decode_header(value):
            if isinstance(value_part, str):
                parts.append(value_part)
                continue
            encoding = encoding or "utf-8"
            if encoding.lower() in ("unknown-8bit", "x-unknown", "unknown"):
                encoding = "latin-1"
            try:
                parts.append(value_part.decode(encoding, errors="replace"))
            except Exception:
                parts.append(value_part.decode("latin-1", errors="replace"))
        return "".join(parts)
    except Exception:
        return value


def extract_html(msg: Optional[Message]) -> Optional[str]:
    if msg is None:
        return None
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() != "text/html":
            continue
        try:
            return part.get_payload(decode=True).decode(
                part.get_content_charset() or "utf-8", errors="replace"
            )
        except Exception:
            return None
    return None


def get_attachments(msg: Optional[Message]) -> List[Dict[str, Any]]:
    if msg is None:
        return []
    attachments: List[Dict[str, Any]] = []
    index = 0
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or not part.get("Content-Disposition"):
            continue
        filename = part.get_filename()
        if filename:
            attachments.append({"index": index, "filename": safe_header(filename)})
            index += 1
    return attachments
