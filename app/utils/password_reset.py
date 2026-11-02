from __future__ import annotations

import hashlib
import bcrypt
import secrets
import string
from typing import Tuple

from app.db.dao_app_settings import get_setting
from app.db.dao_conn import get_conn
from app.email.email_actions import send_email_smtp

RESET_ACCOUNT_KEY = "password_reset_account_id"
PASSWORD_LENGTH = 12
MAX_ATTEMPTS_PER_EMAIL = 3
MAX_ATTEMPTS_TOTAL = 20
GENERIC_MESSAGE = (
    "Jeśli konto o podanym adresie istnieje, kod resetowania został wysłany na ten adres email."
)


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
        ):
            return password


def _register_attempt(email: str) -> bool:
    """Zapisuje próbę resetu; zwraca False, gdy przekroczono limit na godzinę."""
    conn = get_conn()
    try:
        conn.execute("SELECT pg_advisory_xact_lock(748203)")
        conn.execute("DELETE FROM reset_hasla_proby WHERE czas < to_char(now() - interval '1 day', 'YYYY-MM-DD HH24:MI:SS')")
        per_email = conn.execute(
            "SELECT COUNT(*) FROM reset_hasla_proby WHERE email = %s AND czas >= to_char(now() - interval '1 hour', 'YYYY-MM-DD HH24:MI:SS')",
            (email.lower(),),
        ).fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM reset_hasla_proby WHERE czas >= to_char(now() - interval '1 hour', 'YYYY-MM-DD HH24:MI:SS')"
        ).fetchone()[0]
        if per_email >= MAX_ATTEMPTS_PER_EMAIL or total >= MAX_ATTEMPTS_TOTAL:
            conn.commit()
            return False
        conn.execute("INSERT INTO reset_hasla_proby (email) VALUES (%s)", (email.lower(),))
        conn.commit()
        return True
    finally:
        conn.close()


def reset_password(email: str) -> Tuple[bool, str]:
    """Send a reset code without changing the password; keep account lookup private."""
    email = (email or "").strip()
    if not email:
        return False, "Podaj adres email."

    account_id = get_setting(RESET_ACCOUNT_KEY)
    if not account_id:
        return False, "Reset hasła nie jest skonfigurowany. Skontaktuj się z administratorem."

    if not _register_attempt(email):
        return False, "Zbyt wiele prób resetu hasła. Spróbuj ponownie za godzinę."

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, imie FROM pracownicy WHERE LOWER(email) = LOWER(%s) AND aktywny = 1",
            (email,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return True, GENERIC_MESSAGE

    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with get_conn() as conn:
        conn.execute("INSERT INTO password_reset_tokens(token_hash, employee_id, expires_at) VALUES (%s, %s, now() + interval '15 minutes')", (digest, row["id"]))
    body = (
        f"Dzień dobry {row['imie']},\n\n"
        f"Kod resetowania hasła Biuro: {token}\n\n"
        "Kod jest jednorazowy i wygasa po 15 minutach. Wpisz go w oknie logowania, "
        "aby ustawić nowe hasło. Jeśli nie zlecano resetu, zignoruj wiadomość."
    )
    ok, error = send_email_smtp(int(account_id), [email], "Reset hasła Biuro", body)
    if not ok:
        with get_conn() as conn:
            conn.execute("DELETE FROM password_reset_tokens WHERE token_hash = %s", (digest,))
        return False, f"Nie udało się wysłać wiadomości: {error}"
    return True, GENERIC_MESSAGE


def redeem_password_reset(email: str, token: str, new_password: str) -> None:
    """Consume a hashed, expiring token and update the password atomically."""
    if len(new_password) < 10 or len(new_password.encode("utf-8")) > 72:
        raise ValueError("Hasło musi mieć co najmniej 10 znaków i najwyżej 72 bajty UTF-8.")
    digest = hashlib.sha256(token.strip().encode()).hexdigest()
    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    with get_conn() as conn:
        employee = conn.execute("SELECT id FROM pracownicy WHERE LOWER(email) = LOWER(%s) AND aktywny = 1 FOR UPDATE", (email.strip(),)).fetchone()
        if not employee:
            raise ValueError("Kod jest nieprawidłowy lub wygasł.")
        row = conn.execute("UPDATE password_reset_tokens SET used_at = now() WHERE token_hash = %s AND employee_id = %s AND used_at IS NULL AND expires_at > now() RETURNING employee_id", (digest, employee[0])).fetchone()
        if not row:
            raise ValueError("Kod jest nieprawidłowy lub wygasł.")
        conn.execute("UPDATE pracownicy SET haslo_hash = %s WHERE id = %s", (new_hash, employee[0]))
        conn.execute("UPDATE password_reset_tokens SET used_at = now() WHERE employee_id = %s AND used_at IS NULL", (employee[0],))
        conn.execute("DELETE FROM user_sessions WHERE pracownik_id = %s", (employee[0],))
