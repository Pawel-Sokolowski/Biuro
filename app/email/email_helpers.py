from __future__ import annotations

import base64
import email
import email.header
import email.policy
import email.utils
import re
import uuid

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from email.message import Message
from imapclient import imap_utf7


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


def decode_mailbox_utf7(name: str) -> str:
    """Decode IMAP modified UTF-7, preserving malformed server names for display."""
    try:
        return imap_utf7.decode((name or "").encode("ascii"))
    except (UnicodeError, ValueError):
        return name or ""


def friendly_folder_name(name: str) -> str:
    """Czytelna nazwa folderu: polskie znaki i bez prefiksu INBOX. / INBOX/."""
    text = decode_mailbox_utf7(name or "").strip()
    if text[:6].upper() in ("INBOX.", "INBOX/") and len(text) > 6:
        text = text[6:]
    return text


def encode_mailbox_utf7(text: str) -> str:
    return imap_utf7.encode(text or "").decode("ascii")


def quote_mailbox(name: str) -> str:
    """Zwraca nazwę skrzynki w cudzysłowie, gotową do wysłania w komendzie IMAP."""
    return '"' + (name or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_list_response(raw: Any) -> Optional[Dict[str, Any]]:
    """Parsuje jedną linię odpowiedzi LIST: surowa nazwa, separator, flagi, nazwa do wyświetlenia."""
    if raw is None or (isinstance(raw, tuple)):
        # literały (nazwa przesłana jako osobny blok) obsługujemy poniżej
        if isinstance(raw, tuple) and len(raw) == 2:
            head = raw[0].decode(errors="replace") if isinstance(raw[0], bytes) else str(raw[0])
            tail = raw[1].decode(errors="replace") if isinstance(raw[1], bytes) else str(raw[1])
            head = re.sub(r"\{\d+\}\s*$", "", head)
            text = head + '"' + tail.replace('"', '\\"') + '"'
        else:
            return None
    elif isinstance(raw, (bytes, bytearray)):
        text = bytes(raw).decode(errors="replace")
    else:
        text = str(raw)

    match = _LIST_RESPONSE_RE.match(text.strip())
    if not match:
        return None

    name = match.group("name").strip()
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if not name:
        return None

    delim = match.group("delim")
    delimiter = None if delim.upper() == "NIL" else delim.strip('"').replace("\\\\", "\\")
    flags = [f for f in match.group("flags").split() if f]
    return {
        "name": name,
        "delimiter": delimiter or None,
        "flags": flags,
        "display": decode_mailbox_utf7(name),
    }


SPECIAL_FOLDER_FLAGS = {
    "\\sent": "sent",
    "\\drafts": "drafts",
    "\\trash": "trash",
    "\\junk": "junk",
    "\\archive": "archive",
}

SPECIAL_FOLDER_NAMES = {
    "sent": ("sent", "sent items", "sent mail", "wysłane", "wyslane", "elementy wysłane"),
    "drafts": ("drafts", "draft", "szkice", "robocze"),
    "trash": ("trash", "deleted", "deleted items", "kosz", "elementy usunięte"),
    "junk": ("spam", "junk", "junk e-mail", "junk mail", "niechciane"),
    "archive": ("archive", "archives", "archiwum"),
}

SPECIAL_FOLDER_LABELS = {
    "inbox": "Odebrane",
    "sent": "Wysłane",
    "drafts": "Szkice",
    "trash": "Kosz",
    "junk": "Spam",
    "archive": "Archiwum",
}

SPECIAL_FOLDER_ORDER = ["inbox", "drafts", "sent", "archive", "junk", "trash"]


def folder_role(server_name: str, attributes: Optional[str] = None, delimiter: Optional[str] = None) -> Optional[str]:
    """Rozpoznaje rolę folderu (inbox/sent/...) po flagach SPECIAL-USE albo po nazwie."""
    if (server_name or "").upper() == "INBOX":
        return "inbox"
    for flag in (attributes or "").lower().split():
        if flag in SPECIAL_FOLDER_FLAGS:
            return SPECIAL_FOLDER_FLAGS[flag]
    leaf = decode_mailbox_utf7(server_name or "")
    if delimiter:
        leaf = leaf.split(delimiter)[-1]
    leaf = leaf.strip().casefold()
    for role, names in SPECIAL_FOLDER_NAMES.items():
        if leaf in names:
            return role
    return None


def format_mail_date(value: Optional[str]) -> str:
    """Zamienia nagłówek Date na 'RRRR-MM-DD GG:MM' (sortowalny); w razie błędu zwraca oryginał."""
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


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
                "content_id": (part.get("Content-ID") or "").strip("<> \r\n"),
            }
        )

    return attachments


def inline_resources(msg: Optional[Message]) -> Dict[str, Tuple[bytes, str]]:
    """Części z Content-ID (obrazki w treści HTML): {cid: (bajty, typ MIME)}."""
    result: Dict[str, Tuple[bytes, str]] = {}
    if msg is None:
        return result
    for part in _modern(msg).walk():
        cid = (part.get("Content-ID") or "").strip("<> \r\n")
        if cid and not part.is_multipart():
            payload = part.get_payload(decode=True)
            if payload:
                result[cid] = (payload, part.get_content_type())
    return result


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


RISKY_EXTENSIONS = frozenset({
    "exe", "com", "scr", "pif", "bat", "cmd", "ps1", "psm1", "vbs", "vbe", "js", "jse",
    "wsf", "wsh", "hta", "lnk", "url", "jar", "msi", "msp", "reg", "dll", "cpl", "iso",
    "img", "vhd", "chm", "docm", "xlsm", "pptm", "dotm", "xlam", "ppam", "sh", "appx",
    "msix", "gadget", "inf", "scf", "application", "settingcontent-ms",
})

_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def safe_attachment_name(filename: Optional[str], default: str = "zalacznik") -> str:
    """Nazwa pliku bezpieczna na Windows: bez ścieżek, strumieni ADS, znaków sterujących i nazw zarezerwowanych."""
    raw = str(filename or "").replace("\\", "/").split("/")[-1]
    raw = "".join(c for c in raw if ord(c) >= 32 and c not in '<>:"|?*')
    raw = raw.strip(" .")
    if not raw:
        return default
    stem = raw.split(".")[0].strip().lower()
    if stem in _WINDOWS_RESERVED:
        raw = "_" + raw
    return raw[:200]


def is_risky_attachment(filename: Optional[str]) -> bool:
    """Wykrywa niebezpieczne rozszerzenie w dowolnym segmencie (np. faktura.pdf.exe)."""
    parts = safe_attachment_name(filename).lower().split(".")[1:]
    return any(p.strip() in RISKY_EXTENSIONS for p in parts)


__all__ = [
    "RISKY_EXTENSIONS",
    "safe_attachment_name",
    "is_risky_attachment",
    "SERVER_TO_LOCAL",
    "LOCAL_TO_SERVER",
    "normalize_folder_name",
    "denormalize_folder_name",
    "parse_mailbox_name",
    "decode_mailbox_utf7",
    "encode_mailbox_utf7",
    "quote_mailbox",
    "parse_list_response",
    "folder_role",
    "format_mail_date",
    "SPECIAL_FOLDER_LABELS",
    "SPECIAL_FOLDER_ORDER",
    "safe_header",
    "parse_message",
    "extract_html",
    "extract_text",
    "iter_message_attachments",
    "get_attachments",
    "inline_resources",
]
