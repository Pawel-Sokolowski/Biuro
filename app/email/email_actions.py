from __future__ import annotations
from app.remote.boundary import endpoint as office_endpoint

import email
import base64
import re
import mimetypes
import traceback
from copy import deepcopy
from smtplib import SMTPDataError, SMTPRecipientsRefused, SMTPSenderRefused

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from app.email.email_helpers import (
    attachment_output_path,
    attachment_name,
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
        # Embed signature images as MIME related parts for Outlook and other
        # clients that do not display data URLs. Never read local/remote URLs.
        related = {}
        def inline_image(match):
            source = match[2]
            if source not in related:
                data = base64.b64decode(match[4], validate=True)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError("Obraz stopki jest zbyt duży (maks. 2 MB).")
                related[source] = (make_msgid(), match[3].lower(), data)
            return match[1] + 'cid:' + related[source][0][1:-1] + match[5]
        html_body = re.sub(r"(src\s*=\s*['\"])(data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+))(['\"])", inline_image, html_body, flags=re.I)
        msg.add_alternative(html_body, subtype="html")
        html_part = msg.get_payload()[-1]
        for cid, subtype, data in related.values():
            html_part.add_related(data, maintype='image', subtype=subtype, cid=cid, disposition='inline')

    added = 0
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
            added += 1

        except Exception:
            continue

    # Każde żądane załączniki muszą wejść do wiadomości: brak pliku lub błąd
    # odczytu nie może po cichu zamienić faktury w e-mail bez załącznika.
    if attachments and added != len(list(attachments)):
        raise ValueError(
            "Nie dołączono wszystkich załączników: "
            + ", ".join(Path(p).name for p in attachments if not Path(p).exists())
        )

    return msg


@office_endpoint('app.email.email_actions.choose_copy_folder')
def choose_copy_folder(konto_id: int, from_addr: str, to_addrs: Iterable[str], subject: str) -> Optional[str]:
    """Folder z reguły filtra pasującej do wychodzącej wiadomości (np. folder klienta)."""
    from app.email.email_rules import _active_rules, find_rule

    header = {"sender": from_addr, "recipient": ", ".join(to_addrs or []), "subject": subject}
    rule = find_rule(_active_rules(konto_id), header)
    return rule["target"] if rule else None


@office_endpoint('app.email.email_actions.store_sent_copy')
def store_sent_copy(konto_id: int, msg: EmailMessage, folder: Optional[str]) -> bool:
    """Zapisuje kopię i próbuje ją od razu zsynchronizować z IMAP."""
    import logging
    from app.email.sent_copies import flush_copy, stage_copy

    try:
        if not msg["Message-ID"]:
            msg["Message-ID"] = make_msgid()
        return flush_copy(stage_copy(konto_id, msg, folder, confirmed=True))
    except Exception:
        logging.getLogger(__name__).exception("Nie można zapisać kopii wysłanej wiadomości")
        return False


@office_endpoint('app.email.email_actions.send_email_smtp')
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
    require_attachments: bool = False,
) -> Tuple[bool, str]:
    """Wysyła wiadomość. Przy `save_copy` zapisuje ją w Wysłanych i opcjonalnym `copy_folder`."""

    try:
        cfg = get_smtp_config_for_account(konto_id)
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

        if require_attachments and len(list(msg.iter_attachments())) != len(attachments or []):
            raise ValueError("Nie udało się dołączyć wszystkich wymaganych załączników. Wiadomość nie została wysłana.")
    except Exception as exc:
        return False, f"{describe_error(exc)}\n{traceback.format_exc()}"

    recipients = (
        list(to_addrs or [])
        + list(cc or [])
        + list(bcc or [])
    )
    return _send_smtp_message(konto_id, cfg, msg, recipients, subject, timeout=timeout,
                              bcc=bcc, save_copy=save_copy, copy_folder=copy_folder)


def _send_smtp_message(konto_id, cfg, msg, recipients, subject, *, timeout=30,
                       bcc=None, save_copy=False, copy_folder=None):
    import logging
    from app.email.sent_copies import confirm_copy, discard_reservation, flush_copy, stage_copy

    log = logging.getLogger(__name__)
    reservation = None
    started = accepted = False
    copy_warning = ''
    rejected = set()
    try:
        # Wiadomość bez odbiorców nie może zostać uznana za wysłaną: SMTP przyjmie
        # kopię, a do nikogo ona nie dotrze. Ten błąd jest wyłapywany przed wysyłką.
        if not [address for address in recipients if str(address or '').strip()]:
            raise ValueError('Brak odbiorcy wiadomości. Nie wysłano.')
        if save_copy:
            reservation = stage_copy(konto_id, _archive_message(msg, bcc))
        with open_smtp(
            cfg["host"], cfg["port"], cfg["ssl"], cfg["user"], cfg["password"], timeout
        ) as smtp:
            started = True
            refused = smtp.send_message(
                msg,
                from_addr=cfg["user"],
                to_addrs=recipients,
            )
            accepted = True
            rejected = {address.casefold() for address in refused} if isinstance(refused, dict) else set()
            if reservation is not None:
                accepted_bcc = [address for address in bcc or [] if address.casefold() not in rejected]
                archive = _archive_message(msg, accepted_bcc)
                try:
                    confirm_copy(reservation, archive)
                except Exception:
                    log.exception("Nie można potwierdzić zapisanej kopii wiadomości")
                    copy_warning = "Zapisana kopia wymaga potwierdzenia przez administratora."
    except Exception as exc:
        if not accepted:
            if reservation is not None and (not started or isinstance(exc, (SMTPDataError, SMTPRecipientsRefused, SMTPSenderRefused))):
                try:
                    discard_reservation(reservation)
                except Exception:
                    log.exception("Nie można wycofać rezerwacji kopii wiadomości")
            elif reservation is not None:
                log.warning("sent_copy_awaiting_smtp_confirmation copy_id=%s", reservation)
            return False, f"{describe_error(exc)}\n{traceback.format_exc()}"
        # SMTP already confirmed DATA. A failed QUIT does not undo that confirmation.
        log.warning("SMTP zakończył połączenie po przyjęciu wiadomości", exc_info=True)

    if reservation is not None and not copy_warning:
        try:
            archived = flush_copy(reservation)
            archived = _store_additional_copies(konto_id, cfg['user'], msg, archive, accepted_bcc, subject, copy_folder) and archived
        except Exception:
            log.exception("Nie można zsynchronizować kopii wiadomości")
            archived = False
        if not archived:
            copy_warning = "Nie potwierdzono synchronizacji wszystkich kopii z IMAP. Biuro ponowi zapis kopii z kolejki."
    if rejected:
        result = "SMTP odrzucił część odbiorców: " + ", ".join(refused)
        return False, result + (" " + copy_warning if copy_warning else "")
    if copy_warning:
        return True, f"Wysłano przez SMTP. {copy_warning} Sprawdź Wysłane i foldery klientów. Nie ponawiaj wysyłki."
    return True, "OK"


def _archive_message(message, bcc):
    archive = deepcopy(message)
    if bcc:
        archive['Bcc'] = ', '.join(bcc)
    return archive


def _store_additional_copies(account_id, sender, message, archive, accepted_bcc, subject, copy_folder):
    if not (copy_folder or accepted_bcc):
        return True
    from app.email.email_storage import find_special_folder

    sent = find_special_folder(account_id, 'sent')
    copies = {copy_folder: archive} if copy_folder and copy_folder != sent else {}
    folders = {}
    for recipient in accepted_bcc:
        target = choose_copy_folder(account_id, sender, [recipient], subject)
        if target and target != sent and target not in copies:
            folders.setdefault(target, []).append(recipient)
    copies.update({target: _archive_message(message, recipients) for target, recipients in folders.items()})
    saved = True
    for target, copy in copies.items():
        if not store_sent_copy(account_id, copy, target):
            saved = False
    return saved


class _MailTransaction:
    """Jedna sesja SMTP dla kilku wiadomości: mniej logowań, mniej zerwanych połączeń.

    Każda wiadomość jest wysyłana osobno (jeden odbiorca = jedna wiadomość),
    a błąd transportu zamyka sesję i jest zgłaszany wołającemu, żeby ten mógł
    ponowić dopiero kolejną wiadomość — bez ryzyka podwójnej wysyłki.
    """

    def __init__(self, konto_id: int, timeout: int = 30):
        self.cfg = get_smtp_config_for_account(konto_id)
        self.timeout = timeout
        self.connection = None

    def __enter__(self):
        self._open()
        return self

    def _open(self):
        self.close()
        self.connection = open_smtp(self.cfg["host"], self.cfg["port"], self.cfg["ssl"],
                                    self.cfg["user"], self.cfg["password"], self.timeout)

    def send(self, msg, recipients):
        if not [address for address in recipients if str(address or '').strip()]:
            raise ValueError('Brak odbiorcy wiadomości. Nie wysłano.')
        try:
            return self.connection.send_message(msg, from_addr=self.cfg["user"], to_addrs=recipients)
        except Exception:
            self.close()
            raise

    def close(self):
        if self.connection is not None:
            try:
                self.connection.quit()
            except Exception:
                pass
            self.connection = None

    def __exit__(self, *exc_info):
        self.close()
        return False


@office_endpoint('app.email.email_actions.send_batch_emails')
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
                save_copy=True,
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
    *, expected_row_id: Optional[int] = None,
) -> Optional[email.message.Message]:
    """
    IMAP-first.
    Nie korzysta już z SQLite.
    """

    options = {} if expected_row_id is None else {"expected_row_id": expected_row_id}
    return fetch_message_from_imap(
        konto_id,
        folder,
        uid,
        **options,
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
        output_path = attachment_output_path(attachment_name(part.get_filename(), payload))

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


@office_endpoint('app.email.email_actions.add_filter')
def add_filter(konto_id: int, od: str, folder: str) -> None:
    add_email_filter(konto_id, None, od, None, None, None, None, folder)


@office_endpoint('app.email.email_actions.mark_read')
def mark_read(konto_id: int, uid: str) -> None:
    update_message_read_flag(konto_id, uid, True)


@office_endpoint('app.email.email_actions.mark_unread')
def mark_unread(konto_id: int, uid: str) -> None:
    update_message_read_flag(konto_id, uid, False)


@office_endpoint('app.email.email_actions.delete_email')
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
