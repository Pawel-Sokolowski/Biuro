from __future__ import annotations

import secrets
import string
from typing import Tuple

from app.db.dao_app_settings import get_setting
from app.db.dao_conn import get_conn
from app.db.dao_employees import change_employee_password
from app.email.email_actions import send_email_smtp

RESET_ACCOUNT_KEY = "password_reset_account_id"
PASSWORD_LENGTH = 12
MAX_ATTEMPTS_PER_EMAIL = 3
MAX_ATTEMPTS_TOTAL = 20
GENERIC_MESSAGE = (
    "Jeśli konto o podanym adresie istnieje, nowe hasło zostało wysłane na ten adres email."
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
    """Wysyła nowe losowe hasło z konta ustawionego w panelu admina i zmienia je w bazie.

    Hasło w bazie zmieniane jest dopiero po udanej wysyłce, aby nie zablokować użytkownika.
    Odpowiedź nie ujawnia, czy konto istnieje.
    """
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

    new_password = generate_password()
    body = (
        f"Dzień dobry {row['imie']},\n\n"
        f"Twoje nowe hasło do aplikacji Biuro: {new_password}\n\n"
        "Po zalogowaniu zalecamy jego zmianę."
    )
    ok, error = send_email_smtp(int(account_id), [email], "Nowe hasło do aplikacji Biuro", body)
    if not ok:
        return False, f"Nie udało się wysłać wiadomości: {error}"

    change_employee_password(row["id"], new_password)
    return True, GENERIC_MESSAGE
