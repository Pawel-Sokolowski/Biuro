"""Pierwsze uruchomienie: tworzenie konta administratora zamiast stałego hasła w kodzie."""

from __future__ import annotations

from app.db.dao_conn import get_conn
from app.utils.crypto import hash_password

MIN_PASSWORD_LENGTH = 8
ADMIN_ROLE_ID = 4


def needs_initial_admin() -> bool:
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM pracownicy").fetchone()[0] == 0
    finally:
        conn.close()


def create_initial_admin(email: str, password: str, imie: str = "Główny", nazwisko: str = "Administrator") -> int:
    """Tworzy pierwszego administratora. Działa tylko na bazie bez pracowników."""
    email = (email or "").strip()
    if "@" not in email:
        raise ValueError("Podaj poprawny adres e-mail.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Hasło musi mieć co najmniej {MIN_PASSWORD_LENGTH} znaków.")
    if not needs_initial_admin():
        raise PermissionError("Administrator już istnieje. Nowe konta tworzy administrator.")

    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO pracownicy (email, haslo_hash, imie, nazwisko, aktywny) VALUES (?, ?, ?, ?, 1)",
            (email, hash_password(password), imie, nazwisko),
        )
        admin_id = int(cur.lastrowid)
        conn.execute("INSERT OR IGNORE INTO firmy (id, nazwa) VALUES (1, 'Domyślna firma')")
        conn.execute(
            "INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id) VALUES (?, 1, ?)",
            (admin_id, ADMIN_ROLE_ID),
        )
        conn.execute(
            """
            UPDATE role
            SET can_view_invoices=1, can_edit_companies=1, can_manage_users=1,
                can_view_finances=1, can_export_data=1, can_access_admin_settings=1
            WHERE id = ?
            """,
            (ADMIN_ROLE_ID,),
        )
        conn.commit()
        return admin_id
    finally:
        conn.close()
