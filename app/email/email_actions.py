from __future__ import annotations

import email
import mimetypes
import traceback

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from app.email.email_helpers import (
    attachment_output_path,
    get_attachments,
    iter_message_attachments,
)
from app.email.email_storage import (
    add_email_filter,
    get_account_info,
    mark_deleted,
    update_message_read_flag,
)
from app.email.email_connection import describe_error, open_smtp
from app.email.email_sync import fetch_message_from_imap
from app.utils.crypto import decrypt_password


def get_smtp_config_for_account(
    konto_id: int,
) -> Dict[str, Any]:
    data = get_account_info(konto_id)

    if not data:
        raise ValueError(f"konto {konto_id} nie znalezione")

    return {
        "host": data.get("serwer_smtp"),
        "port": int(
            data.get("port_smtp") or 587
        ),
        "user": data.get("email"),
        "password": decrypt_password(
            data.get("haslo_enc")
        )
        if data.get("haslo_enc")
        else "",
        "display_name": (
            data.get("display_name")
            or data.get("email")
        ),
        "ssl": data.get("ssl_smtp"),
    }


def prepare_message(
    from_addr: str,
    from_name: str,
    to_addrs: Iterable[str],
    subject: str,
    body: str,
    attachments: Optional[List[str]] = None,
    cc: Optional[Iterable[str]] = None,
    bcc: Optional[Iterable[str]] = None,
    html_body: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> EmailMessage:
    msg = EmailMessage()

    msg["From"] = formataddr(
        (from_name or "", from_addr)
    )

    msg["To"] = ", ".join(
        list(to_addrs or [])
    )

    if cc:
        msg["Cc"] = ", ".join(cc)

    msg["Subject"] = subject or ""
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
    msg.set_content(body or "")
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    for path_str in attachments or []:
        path = Path(path_str)

        if not path.exists():
            continue

        try:
            ctype, encoding = mimetypes.guess_type(path.name)
            maintype, _, subtype = (
                ctype if ctype and not encoding else "application/octet-stream"
            ).partition("/")

            msg.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )

        except Exception:
            continue

    return msg


def choose_copy_folder(konto_id: int, from_addr: str, to_addrs: Iterable[str], subject: str) -> Optional[str]:
    """Folder z reguły filtra pasującej do wychodzącej wiadomości (np. folder klienta)."""
    from app.email.email_rules import _active_rules, rule_matches

    header = {"sender": from_addr, "recipient": ", ".join(to_addrs or []), "subject": subject}
    for rule in _active_rules(konto_id):
        if rule_matches(rule, header):
            return rule["target"]
    return None


def store_sent_copy(konto_id: int, msg: EmailMessage, folder: Optional[str]) -> None:
    """Zapisuje kopię wysłanej wiadomości na serwerze i w bazie; błąd nie psuje wysyłki."""
    import logging
    from app.email.email_storage import find_special_folder
    from app.email.email_sync import append_message, sync_folder

    log = logging.getLogger(__name__)
    try:
        target = folder or find_special_folder(konto_id, "sent")
        if not target:
            return
        bcc = msg.get("Bcc")
        if append_message(konto_id, target, msg.as_bytes()):
            sync_folder(konto_id, target)
        else:
            log.warning("Nie udało się zapisać kopii wysłanej wiadomości w %s", target)
    except Exception:
        log.warning("Nie udało się zapisać kopii wysłanej wiadomości", exc_info=True)


def send_email_smtp(
    konto_id: int,
    to_addrs: Iterable[str],
    subject: str,
    body: str,
    attachments: Optional[List[str]] = None,
    cc: Optional[Iterable[str]] = None,
    bcc: Optional[Iterable[str]] = None,
    timeout: int = 30,
    html_body: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    save_copy: bool = False,
    copy_folder: Optional[str] = None,
) -> Tuple[bool, str]:
    """Wysyła wiadomość. Przy `save_copy` kopia trafia do `copy_folder` (np. folder oryginału,
    folder z reguły filtra), a gdy go brak - do folderu Wysłane."""

    cfg = get_smtp_config_for_account(
        konto_id
    )

    msg = prepare_message(
        cfg["user"],
        cfg["display_name"],
        to_addrs,
        subject,
        body,
        attachments,
        cc,
        bcc,
        html_body,
        in_reply_to,
        references,
    )

    recipients = (
        list(to_addrs or [])
        + list(cc or [])
        + list(bcc or [])
    )

    try:
        with open_smtp(
            cfg["host"], cfg["port"], cfg["ssl"], cfg["user"], cfg["password"], timeout
        ) as smtp:
            smtp.send_message(
                msg,
                from_addr=cfg["user"],
                to_addrs=recipients,
            )

        if save_copy:
            store_sent_copy(konto_id, msg, copy_folder)
        return True, "OK"

    except Exception as exc:
        return (
            False,
            f"{describe_error(exc)}\n{traceback.format_exc()}",
        )


def send_batch_emails(
    konto_id: int,
    messages: List[Dict[str, Any]],
    retry: int = 2,
) -> List[Dict[str, Any]]:

    results = []

    for msg_data in messages:

        ok = False
        info = ""

        for _ in range(retry + 1):

            ok, info = send_email_smtp(
                konto_id,
                msg_data.get("to") or [],
                msg_data.get("subject") or "",
                msg_data.get("body") or "",
                attachments=msg_data.get(
                    "attachments"
                ),
                cc=msg_data.get("cc"),
                bcc=msg_data.get("bcc"),
            )

            if ok:
                break

        results.append(
            {
                "message": msg_data,
                "ok": ok,
                "info": info,
            }
        )

    return results


def load_message(
    konto_id: int,
    folder: str,
    uid: str,
) -> Optional[email.message.Message]:
    """
    IMAP-first.
    Nie korzysta już z SQLite.
    """

    return fetch_message_from_imap(
        konto_id,
        folder,
        uid,
    )


def open_attachment(
    konto_id: int,
    folder: str,
    uid: str,
    index: int,
) -> Optional[str]:

    msg = load_message(
        konto_id,
        folder,
        uid,
    )

    if not msg:
        return None

    attachments = iter_message_attachments(msg)

    if not 0 <= index < len(attachments):
        return None

    part = attachments[index]
    payload = part.get_payload(decode=True)

    if payload is None:
        return None

    try:
        output_path = attachment_output_path(part.get_filename() or "attachment")

        with open(output_path, "wb") as f:
            f.write(payload)

        return output_path

    except Exception:
        return None


def download_all_attachments(
    konto_id: int,
    folder: str,
    uid: str,
    attachments: List[Dict[str, Any]],
) -> List[str]:

    saved = []

    for att in attachments:

        path = open_attachment(
            konto_id,
            folder,
            uid,
            att.get("index", 0),
        )

        if path:
            saved.append(path)

    return saved


def add_filter(konto_id: int, od: str, folder: str) -> None:
    add_email_filter(konto_id, None, od, None, None, None, None, folder)


def mark_read(konto_id: int, uid: str) -> None:
    update_message_read_flag(konto_id, uid, True)


def mark_unread(konto_id: int, uid: str) -> None:
    update_message_read_flag(konto_id, uid, False)


def delete_email(konto_id: int, uid: str) -> None:
    mark_deleted(konto_id, uid)


__all__ = [
    "get_smtp_config_for_account",
    "prepare_message",
    "send_email_smtp",
    "send_batch_emails",
    "load_message",
    "open_attachment",
    "download_all_attachments",
    "add_filter",
    "mark_read",
    "mark_unread",
    "delete_email",
    "get_attachments",
]