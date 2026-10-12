from __future__ import annotations

import email
import os
import uuid
from pathlib import Path
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
    """
    Convert server folder name to localized folder name.
    """
    return SERVER_TO_LOCAL.get(server_name, server_name)


def denormalize_folder_name(local_name: str) -> str:
    """
    Convert localized folder name back to server folder name.
    """
    return LOCAL_TO_SERVER.get(local_name, local_name)


def safe_header(value: str) -> str:
    """
    Safely decode an email header that may contain multiple encodings.

    Args:
        value: The raw header string.

    Returns:
        Decoded unicode string.
    """
    if not value:
        return ""
    try:
        parts = []
        for value_part, encoding in email.header.decode_header(value):
            if isinstance(value_part, str):
                parts.append(value_part)
                continue
            encoding = encoding or "utf-8"
            # Handle unknown encodings by falling back to latin-1
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
    """
    Extract the first HTML part from an email message.

    Args:
        msg: Email message object.

    Returns:
        Decoded HTML content or None if not found.
    """
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
    """
    Extract attachment info from an email message.

    Args:
        msg: Email message object.

    Returns:
        List of dicts with attachment index and decoded filename.
    """
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


def attachment_output_path(filename: str, base_dir: Optional[str] = None) -> str:
    """
    Create a unique, safe file path for saving an attachment.

    Args:
        filename: Original filename from the attachment.
        base_dir: Optional base directory to save attachments.

    Returns:
        Absolute path to save the attachment.

    Raises:
        ValueError: If the resolved path escapes the base directory.
    """
    root = Path(base_dir or (Path.cwd() / "tmp_attachments")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Sanitize filename by taking only the name part (no directories)
    safe_name = Path(safe_header(filename)).name or "attachment"
    # Remove problematic characters from filename (optional)
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in (" ", ".", "_", "-")).strip()
    if not safe_name:
        safe_name = "attachment"
    destination = (root / f"{uuid.uuid4().hex}_{safe_name}").resolve()
    # Prevent directory traversal attacks
    if os.path.commonpath((str(root), str(destination))) != str(root):
        raise ValueError("Invalid attachment filename")
    return str(destination)
