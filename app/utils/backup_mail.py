"""Pobieranie logu kopii zapasowej z maila wysyłanego przez program do backupu."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from app.db.dao_app_settings import get_setting, set_setting
from app.utils.system_mailbox import get_system_account_id
from app.db.dao_conn import get_conn

logger = logging.getLogger(__name__)

LOG_TEXT_KEY = "backup_mail_log_text"
LOG_MAIL_KEY = "backup_mail_ref"
LOG_MAIL_DATE_KEY = "backup_mail_date"
CHECKED_AT_KEY = "backup_mail_checked_at"
TEXT_ATTACHMENT_SUFFIXES = (".log", ".txt")
BLOCK_TAGS = {"br", "p", "div", "tr", "li", "pre", "table", "h1", "h2", "h3"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return "".join(parser.parts)


def message_log_text(msg) -> str:
    """Tekst logu z treści maila; gdy treść jest pusta, z załącznika .log/.txt."""
    from app.email.email_helpers import extract_html, extract_text, iter_message_attachments

    text = extract_text(msg).strip()
    if not text:
        html = extract_html(msg)
        text = html_to_text(html).strip() if html else ""
    if text:
        return text
    for part in iter_message_attachments(msg):
        name = (part.get_filename() or "").lower()
        if name.endswith(TEXT_ATTACHMENT_SUFFIXES) or part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True) or b""
            for encoding in ("utf-8-sig", "utf-16", "cp1250"):
                try:
                    return payload.decode(encoding)
                except UnicodeError:
                    continue
    return ""


def _message_date(value: Optional[str]) -> datetime:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return datetime.min
    if parsed.tzinfo:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _uid_number(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def find_latest_backup_mail() -> Optional[Dict[str, Any]]:
    """Najnowsza wiadomość w ukrytej skrzynce systemowej."""
    account_id = get_system_account_id()
    if account_id is None:
        return None
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT w.id, w.konto_id, w.uid, w.data, w.temat, f.server_name
            FROM wiadomosci_email w
            JOIN foldery_email f ON f.id = w.folder_id
            WHERE w.konto_id = %s AND COALESCE(w.usunieta, 0) = 0
            ORDER BY w.id DESC
            LIMIT 200
            """,
            (account_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    best = max((dict(r) for r in rows), key=lambda r: (_message_date(r["data"]), _uid_number(r["uid"])))
    best["data_dt"] = _message_date(best["data"])
    return best


def _sync_inbox(account_id: int) -> None:
    from app.email.email_sync import fetch_headers_from_imap_and_store

    try:
        fetch_headers_from_imap_and_store(account_id, "INBOX")
    except Exception:
        logger.exception("Nie udało się odświeżyć skrzynki konta %s", account_id)


def refresh_backup_log_from_mail(sync_headers: bool = True) -> Dict[str, Any]:
    """Pobiera najnowszy mail z logiem i zapisuje jego tekst w ustawieniach."""
    from app.email.email_sync import fetch_message_from_imap

    account_id = get_system_account_id()
    if account_id is None:
        return {"ok": False, "komunikat": "Skonfiguruj konto systemowe (Ustawienia → Reset hasła i kalendarz)."}
    if sync_headers:
        _sync_inbox(account_id)
    mail = find_latest_backup_mail()
    set_setting(CHECKED_AT_KEY, datetime.now().strftime("%Y-%m-%d %H:%M"))
    if mail is None:
        return {"ok": False, "komunikat": "Nie znaleziono maila z logiem kopii."}
    ref = f"{mail['konto_id']}:{mail['uid']}"
    if get_setting(LOG_MAIL_KEY) == ref and get_setting(LOG_TEXT_KEY):
        return {"ok": True, "komunikat": "Log jest aktualny.", "zmieniono": False}
    msg = fetch_message_from_imap(mail["konto_id"], mail["server_name"], mail["uid"])
    if msg is None:
        return {"ok": False, "komunikat": "Nie udało się pobrać maila z serwera."}
    text = message_log_text(msg)
    if not text:
        return {"ok": False, "komunikat": "Mail nie zawiera tekstu logu."}
    set_setting(LOG_TEXT_KEY, text)
    set_setting(LOG_MAIL_KEY, ref)
    set_setting(LOG_MAIL_DATE_KEY, mail["data_dt"].strftime("%Y-%m-%d %H:%M"))
    return {"ok": True, "komunikat": "Pobrano nowy log.", "zmieniono": True}
