from __future__ import annotations

import email
import email.header
import os
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


def normalize_folder_name(server_name: str) -> str:
    if not server_name:
        return ""

    return SERVER_TO_LOCAL.get(
        server_name,
        server_name,
    )


def denormalize_folder_name(local_name: str) -> str:
    if not local_name:
        return ""

    return LOCAL_TO_SERVER.get(
        local_name,
        local_name,
    )


def parse_mailbox_name(raw: bytes) -> str:
    try:
        s = raw.decode(errors="replace")

        first_quote = s.find('"')

        if first_quote != -1:
            last_quote = s.rfind('"')

            if last_quote > first_quote:
                return s[first_quote + 1:last_quote]

        return s.split()[-1].strip('"')

    except Exception:
        return raw.decode(errors="replace")


def safe_header(
    value: Optional[str],
) -> str:
    if value is None:
        return ""

    if isinstance(
        value,
        (bytes, bytearray),
    ):
        try:
            return value.decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            return value.decode(
                "latin-1",
                errors="replace",
            )

    try:
        decoded = email.header.decode_header(
            value
        )

        parts: List[str] = []

        for data, encoding in decoded:
            if isinstance(data, str):
                parts.append(data)
                continue

            enc = encoding or "utf-8"

            if (
                isinstance(enc, str)
                and enc.lower()
                in (
                    "unknown-8bit",
                    "unknown",
                    "x-unknown",
                )
            ):
                enc = "latin-1"

            try:
                parts.append(
                    data.decode(
                        enc,
                        errors="replace",
                    )
                )
            except Exception:
                parts.append(
                    data.decode(
                        "latin-1",
                        errors="replace",
                    )
                )

        return "".join(parts)

    except Exception:
        return str(value)


def extract_html(
    msg: Optional[Message],
) -> Optional[str]:
    if msg is None:
        return None

    parts = (
        msg.walk()
        if msg.is_multipart()
        else [msg]
    )

    for part in parts:
        if (
            part.get_content_maintype()
            == "multipart"
        ):
            continue

        if (
            part.get_content_type()
            != "text/html"
        ):
            continue

        payload = None

        try:
            payload = part.get_payload(
                decode=True
            )

            if payload is None:
                raw = part.get_payload(
                    decode=False
                )

                return (
                    raw
                    if isinstance(raw, str)
                    else None
                )

            charset = (
                part.get_content_charset()
                or "utf-8"
            )

            return payload.decode(
                charset,
                errors="replace",
            )

        except Exception:
            if payload:
                try:
                    return payload.decode(
                        "latin-1",
                        errors="replace",
                    )
                except Exception:
                    pass

    return None


def extract_text(
    msg: Optional[Message],
) -> str:
    if msg is None:
        return ""

    try:
        if msg.is_multipart():
            for part in msg.walk():
                if (
                    part.get_content_type()
                    == "text/plain"
                ):
                    payload = part.get_payload(
                        decode=True
                    )

                    if not payload:
                        continue

                    charset = (
                        part.get_content_charset()
                        or "utf-8"
                    )

                    return payload.decode(
                        charset,
                        errors="replace",
                    )

        else:
            if (
                msg.get_content_type()
                == "text/plain"
            ):
                payload = msg.get_payload(
                    decode=True
                )

                if payload:
                    return payload.decode(
                        msg.get_content_charset()
                        or "utf-8",
                        errors="replace",
                    )

    except Exception:
        pass

    return ""


def get_attachments(
    msg: Optional[Message],
) -> List[Dict[str, Any]]:
    if msg is None:
        return []

    attachments = []
    index = 0

    for part in msg.walk():
        if (
            part.get_content_maintype()
            == "multipart"
        ):
            continue

        filename = part.get_filename()
        disposition = (
            part.get(
                "Content-Disposition",
                "",
            )
            or ""
        )

        if (
            not filename
            and "attachment"
            not in disposition.lower()
        ):
            continue

        payload = None
        size = None

        try:
            payload = part.get_payload(
                decode=True
            )

            if payload:
                size = len(payload)

        except Exception:
            pass

        attachments.append(
            {
                "index": index,
                "filename": (
                    Path(
                        safe_header(filename)
                    ).name
                    if filename
                    else "attachment"
                ),
                "content_type": part.get_content_type(),
                "size": size,
            }
        )

        index += 1

    return attachments


def attachment_output_path(
    filename: str,
    base_dir: Optional[str] = None,
) -> str:
    base = (
        Path(base_dir)
        if base_dir
        else Path.cwd()
        / "tmp_attachments"
    )

    root = base.resolve()

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    safe_name = Path(
        safe_header(filename)
    ).name

    safe_name = "".join(
        c
        for c in safe_name
        if c.isalnum()
        or c in (
            ".",
            "_",
            "-",
            " ",
        )
    ).strip()

    if not safe_name:
        safe_name = "attachment"

    destination = (
        root
        / f"{uuid.uuid4().hex}_{safe_name}"
    ).resolve()

    return str(destination)


__all__ = [
    "SERVER_TO_LOCAL",
    "LOCAL_TO_SERVER",
    "normalize_folder_name",
    "denormalize_folder_name",
    "parse_mailbox_name",
    "safe_header",
    "extract_html",
    "extract_text",
    "get_attachments",
    "attachment_output_path",
]