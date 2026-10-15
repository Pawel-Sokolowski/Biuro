from __future__ import annotations

import email
import email.header
import email.policy
import re
import uuid

from pathlib import Path
from typing import Any, Dict, List, Optional

from email.message import Message


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

# Odpowiedź LIST: (\Flags) "delimiter" "nazwa" albo nazwa bez cudzysłowu
_LIST_RESPONSE_RE = re.compile(
    r'\((?P<flags>.*?)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$'
)


def normalize_folder_name(server_name: str) -> str:
    return SERVER_TO_LOCAL.get(server_name, server_name or "")


def denormalize_folder_name(local_name: str) -> str:
    return LOCAL_TO_SERVER.get(local_name, local_name or "")


def parse_mailbox_name(raw: bytes) -> str:
    text = raw.decode(errors="replace")
    match = _LIST_RESPONSE_RE.match(text)
    name = match.group("name").strip() if match else text.split()[-1]
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return name


def safe_header(value: Optional[str]) -> str:
    if value is None:
        return ""

    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")

    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except Exception:
        return str(value)


def parse_message(raw: bytes) -> Message:
    """Parsuje wiadomość nowoczesną polityką (get_body / iter_attachments)."""
    return email.message_from_bytes(raw, policy=email.policy.default)


def _modern(msg: Message) -> Message:
    if hasattr(msg, "get_body"):
        return msg
    return parse_message(msg.as_bytes())


def _body_content(msg: Optional[Message], subtype: str) -> Optional[str]:
    if msg is None:
        return None

    part = _modern(msg).get_body(preferencelist=(subtype,))
    if part is None:
        return None

    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True)
        return payload.decode("latin-1", errors="replace") if payload else None


def extract_html(msg: Optional[Message]) -> Optional[str]:
    return _body_content(msg, "html")


def extract_text(msg: Optional[Message]) -> str:
    return _body_content(msg, "plain") or ""


def iter_message_attachments(msg: Optional[Message]) -> List[Message]:
    if msg is None:
        return []
    return list(_modern(msg).iter_attachments())


def get_attachments(msg: Optional[Message]) -> List[Dict[str, Any]]:
    attachments = []

    for index, part in enumerate(iter_message_attachments(msg)):
        filename = part.get_filename()
        payload = part.get_payload(decode=True)

        attachments.append(
            {
                "index": index,
                "filename": Path(filename).name if filename else "attachment",
                "content_type": part.get_content_type(),
                "size": len(payload) if payload else None,
            }
        )

    return attachments


def attachment_output_path(
    filename: str,
    base_dir: Optional[str] = None,
) -> str:
    root = (Path(base_dir) if base_dir else Path.cwd() / "tmp_attachments").resolve()
    root.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(
        c for c in Path(safe_header(filename)).name
        if c.isalnum() or c in "._- "
    ).strip() or "attachment"

    return str((root / f"{uuid.uuid4().hex}_{safe_name}").resolve())


__all__ = [
    "SERVER_TO_LOCAL",
    "LOCAL_TO_SERVER",
    "normalize_folder_name",
    "denormalize_folder_name",
    "parse_mailbox_name",
    "safe_header",
    "parse_message",
    "extract_html",
    "extract_text",
    "iter_message_attachments",
    "get_attachments",
    "attachment_output_path",
]
