"""Ukryte konto systemowe: wysyła nowe hasła i odbiera logi kopii zapasowej.

Konto jest zapisane w `konta_email` z flagą `systemowe = 1`, więc nie pojawia się
na listach kont ani w module poczty. Jego id jest pod tym samym kluczem ustawień,
którego używa reset hasła.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn

SYSTEM_ACCOUNT_KEY = "password_reset_account_id"


def get_system_account_id() -> Optional[int]:
    value = (get_setting(SYSTEM_ACCOUNT_KEY) or "").strip()
    return int(value) if value.isdigit() else None


def get_system_account() -> Optional[Dict[str, Any]]:
    account_id = get_system_account_id()
    if account_id is None:
        return None
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM konta_email WHERE id = %s", (account_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def save_system_account(
    email: str,
    password: str,
    imap_server: str,
    smtp_server: str,
    imap_port: int = 993,
    smtp_port: int = 465,
    ssl_imap: str = "SSL/TLS",
    ssl_smtp: str = "SSL/TLS",
) -> int:
    """Tworzy albo aktualizuje konto systemowe. Puste hasło zachowuje dotychczasowe."""
    from app.utils.crypto import encrypt_password

    email = (email or "").strip()
    if not email:
        raise ValueError("Podaj adres e-mail konta systemowego.")
    current = get_system_account()
    if not password and not current:
        raise ValueError("Podaj hasło konta systemowego.")
    encrypted = encrypt_password(password) if password else current["haslo_enc"]

    conn = get_conn()
    try:
        values = (
            email, imap_server.strip(), int(imap_port), ssl_imap,
            smtp_server.strip(), int(smtp_port), ssl_smtp, encrypted,
        )
        if current:
            conn.execute(
                """
                UPDATE konta_email
                SET email = %s, serwer_imap = %s, port_imap = %s, ssl_imap = %s,
                    serwer_smtp = %s, port_smtp = %s, ssl_smtp = %s, haslo_enc = %s,
                    aktywne = 1, systemowe = 1, pracownik_id = NULL
                WHERE id = %s
                """,
                values + (current["id"],),
            )
            account_id = int(current["id"])
        else:
            cur = conn.execute(
                """
                INSERT INTO konta_email (
                    email, serwer_imap, port_imap, ssl_imap,
                    serwer_smtp, port_smtp, ssl_smtp, haslo_enc, aktywne, systemowe
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, 1)
                RETURNING id
                """,
                values,
            )
            account_id = int(cur.fetchone()[0])
        conn.commit()
    finally:
        conn.close()
    set_setting(SYSTEM_ACCOUNT_KEY, str(account_id))
    return account_id
