from __future__ import annotations

import email
import smtplib
import traceback

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from email.message import EmailMessage
from email.utils import formataddr

from app.db.dao_conn import get_conn
from app.email.email_helpers import (
    attachment_output_path,
    get_attachments,
)
from app.email.email_sync import fetch_message_from_imap
from app.utils.crypto import decrypt_password


def _get_account_by_id(
    konto_id: int,
) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = None

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM konta_email
            WHERE id = ?
              AND COALESCE(aktywne, 1) = 1
            """,
            (konto_id,),
        )

        row = cur.fetchone()

        if not row:
            return None

        cols = [c[0] for c in cur.description]

        return dict(zip(cols, row))

    finally:
        conn.close()


def get_smtp_config_for_account(
    konto_id: int,
) -> Dict[str, Any]:
    conn = get_conn()
    conn.row_factory = None

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                serwer_smtp,
                port_smtp,
                email,
                haslo_enc,
                display_name,
                ssl_smtp
            FROM konta_email
            WHERE id = ?
            """,
            (konto_id,),
        )

        row = cur.fetchone()

        if not row:
            raise ValueError(
                f"konto {konto_id} nie znalezione"
            )

        cols = [c[0] for c in cur.description]

        data = dict(zip(cols, row))

    finally:
        conn.close()

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

    msg.set_content(body or "")

    for path_str in attachments or []:
        path = Path(path_str)

        if not path.exists():
            continue

        try:
            with open(path, "rb") as f:
                data = f.read()

            msg.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=path.name,
            )

        except Exception:
            continue

    return msg


def send_email_smtp(
    konto_id: int,
    to_addrs: Iterable[str],
    subject: str,
    body: str,
    attachments: Optional[List[str]] = None,
    cc: Optional[Iterable[str]] = None,
    bcc: Optional[Iterable[str]] = None,
    timeout: int = 30,
) -> Tuple[bool, str]:

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
    )

    recipients = (
        list(to_addrs or [])
        + list(cc or [])
        + list(bcc or [])
    )

    try:
        port = int(cfg["port"])

        if port == 465:
            with smtplib.SMTP_SSL(
                cfg["host"],
                port,
                timeout=timeout,
            ) as smtp:

                if cfg["password"]:
                    smtp.login(
                        cfg["user"],
                        cfg["password"],
                    )

                smtp.send_message(
                    msg,
                    from_addr=cfg["user"],
                    to_addrs=recipients,
                )

        else:
            with smtplib.SMTP(
                cfg["host"],
                port,
                timeout=timeout,
            ) as smtp:

                smtp.starttls()

                if cfg["password"]:
                    smtp.login(
                        cfg["user"],
                        cfg["password"],
                    )

                smtp.send_message(
                    msg,
                    from_addr=cfg["user"],
                    to_addrs=recipients,
                )

        return True, "OK"

    except Exception as exc:
        return (
            False,
            f"{exc}\n{traceback.format_exc()}",
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

    cur_idx = 0

    for part in msg.walk():

        if (
            part.get_content_maintype()
            == "multipart"
        ):
            continue

        content_disposition = (
            part.get(
                "Content-Disposition",
                "",
            )
            or ""
        )

        if (
            "attachment"
            not in content_disposition.lower()
        ):
            continue

        filename = part.get_filename()

        if not filename:
            continue

        if cur_idx == index:

            payload = part.get_payload(
                decode=True
            )

            if payload is None:
                return None

            try:
                output_path = (
                    attachment_output_path(
                        filename
                    )
                )

                with open(
                    output_path,
                    "wb",
                ) as f:
                    f.write(payload)

                return output_path

            except Exception:
                return None

        cur_idx += 1

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


def add_filter(
    konto_id: int,
    od: str,
    folder: str,
) -> None:

    conn = get_conn()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO reguly_filtrow
            (
                pracownik_id,
                konto_id,
                od,
                folder_docelowy,
                aktywna
            )
            VALUES
            (
                (SELECT pracownik_id
                 FROM konta_email
                 WHERE id = ?),
                ?,
                ?,
                ?,
                1
            )
            """,
            (
                konto_id,
                konto_id,
                od,
                folder,
            ),
        )

        conn.commit()

    finally:
        conn.close()


def mark_read(
    konto_id: int,
    uid: str,
) -> None:

    conn = get_conn()

    try:
        conn.execute(
            """
            UPDATE wiadomosci_email
            SET przeczytana = 1
            WHERE konto_id = ?
              AND uid = ?
            """,
            (
                konto_id,
                uid,
            ),
        )

        conn.commit()

    finally:
        conn.close()


def mark_unread(
    konto_id: int,
    uid: str,
) -> None:

    conn = get_conn()

    try:
        conn.execute(
            """
            UPDATE wiadomosci_email
            SET przeczytana = 0
            WHERE konto_id = ?
              AND uid = ?
            """,
            (
                konto_id,
                uid,
            ),
        )

        conn.commit()

    finally:
        conn.close()


def delete_email(
    konto_id: int,
    uid: str,
) -> None:

    conn = get_conn()

    try:
        conn.execute(
            """
            UPDATE wiadomosci_email
            SET usunieta = 1
            WHERE konto_id = ?
              AND uid = ?
            """,
            (
                konto_id,
                uid,
            ),
        )

        conn.commit()

    finally:
        conn.close()


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