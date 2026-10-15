"""Połączenia IMAP/SMTP z obsługą SSL/TLS, STARTTLS i braku szyfrowania oraz test połączenia."""

from __future__ import annotations

import imaplib
import smtplib
import ssl
from typing import Any, Dict, Tuple

from app.utils.crypto import decrypt_password

SECURITY_SSL = "SSL/TLS"
SECURITY_STARTTLS = "STARTTLS"
SECURITY_NONE = "Brak"
SECURITY_CHOICES = [SECURITY_SSL, SECURITY_STARTTLS, SECURITY_NONE]
DEFAULT_PORTS = {
    "imap": {SECURITY_SSL: 993, SECURITY_STARTTLS: 143, SECURITY_NONE: 143},
    "smtp": {SECURITY_SSL: 465, SECURITY_STARTTLS: 587, SECURITY_NONE: 25},
}


def normalize_security(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ("ssl", "ssl/tls", "tls", "1", "true", "yes"):
        return SECURITY_SSL
    if text == "starttls":
        return SECURITY_STARTTLS
    if text in ("brak", "none", "0", "false", "no", ""):
        return SECURITY_NONE
    return SECURITY_SSL


def open_imap(host: str, port: int, security: Any, login: str, password: str, timeout: int = 30) -> imaplib.IMAP4:
    mode = normalize_security(security)
    port = int(port or DEFAULT_PORTS["imap"][mode])
    context = ssl.create_default_context()
    if mode == SECURITY_SSL:
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=timeout)
    else:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
        if mode == SECURITY_STARTTLS:
            conn.starttls(ssl_context=context)
    try:
        conn.login(login, password)
        conn.capability()
    except Exception:
        try:
            conn.logout()
        except Exception:
            pass
        raise
    return conn


def open_smtp(host: str, port: int, security: Any, login: str, password: str, timeout: int = 30) -> smtplib.SMTP:
    mode = normalize_security(security)
    port = int(port or DEFAULT_PORTS["smtp"][mode])
    context = ssl.create_default_context()
    if mode == SECURITY_SSL:
        conn: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    else:
        conn = smtplib.SMTP(host, port, timeout=timeout)
    try:
        conn.ehlo()
        if mode == SECURITY_STARTTLS:
            conn.starttls(context=context)
            conn.ehlo()
        if password:
            conn.login(login, password)
    except Exception:
        try:
            conn.quit()
        except Exception:
            pass
        raise
    return conn


def describe_error(exc: Exception) -> str:
    """Czytelny komunikat zamiast surowego wyjątku imaplib/smtplib."""
    if isinstance(exc, (imaplib.IMAP4.error, smtplib.SMTPAuthenticationError)):
        args = getattr(exc, "smtp_error", None) or (exc.args[0] if exc.args else exc)
        text = args.decode("utf-8", "replace") if isinstance(args, (bytes, bytearray)) else str(args)
        return f"Logowanie odrzucone przez serwer: {text}"
    if isinstance(exc, ssl.SSLError):
        return f"Błąd szyfrowania (sprawdź ustawienie SSL/TLS i port): {exc}"
    if isinstance(exc, (TimeoutError, OSError)):
        return f"Nie można połączyć się z serwerem (adres, port lub sieć): {exc}"
    return str(exc)


def _password(account: Dict[str, Any]) -> str:
    if account.get("haslo"):
        return account["haslo"]
    return decrypt_password(account.get("haslo_enc")) if account.get("haslo_enc") else ""


def check_imap(account: Dict[str, Any], timeout: int = 15) -> Tuple[bool, str]:
    try:
        conn = open_imap(
            account.get("serwer_imap"), account.get("port_imap"), account.get("ssl_imap"),
            account.get("email"), _password(account), timeout,
        )
    except Exception as exc:
        return False, describe_error(exc)
    try:
        typ, data = conn.select("INBOX", readonly=True)
        count = int(data[0]) if typ == "OK" and data and data[0] else 0
        return True, f"Połączono z IMAP. Wiadomości w skrzynce odbiorczej: {count}."
    except Exception as exc:
        return False, describe_error(exc)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def check_smtp(account: Dict[str, Any], timeout: int = 15) -> Tuple[bool, str]:
    try:
        conn = open_smtp(
            account.get("serwer_smtp"), account.get("port_smtp"), account.get("ssl_smtp"),
            account.get("email"), _password(account), timeout,
        )
    except Exception as exc:
        return False, describe_error(exc)
    try:
        conn.quit()
    except Exception:
        pass
    return True, "Połączono z SMTP i zalogowano."
