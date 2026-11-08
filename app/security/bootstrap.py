"""Pierwsze uruchomienie: tworzenie konta administratora zamiast stałego hasła w kodzie."""

from __future__ import annotations

from app.db.dao_conn import get_conn
from app.utils.crypto import hash_password

MIN_PASSWORD_LENGTH = 8
ADMIN_ROLE_ID = 4


def needs_initial_admin() -> bool:
    conn = get_conn()
    try:
        return not conn.execute("""SELECT 1 FROM pracownicy p LEFT JOIN role r ON r.id=p.rola_id
            WHERE COALESCE(p.is_admin,0)=1 OR LOWER(COALESCE(r.nazwa,''))='admin' LIMIT 1""").fetchone()
    finally:
        conn.close()


def create_initial_admin(email: str, password: str, imie: str = "Główny", nazwisko: str = "Administrator") -> int:
    """Tworzy pierwszego administratora. Działa tylko na bazie bez administratora."""
    email = (email or "").strip()
    if "@" not in email:
        raise ValueError("Podaj poprawny adres e-mail.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Hasło musi mieć co najmniej {MIN_PASSWORD_LENGTH} znaków.")
    conn = get_conn()
    try:
        # Serialize first-run creation, including an old database containing
        # only non-admin employee rows. Do not rely on a separate preflight.
        conn.execute("SELECT pg_advisory_xact_lock(748208)")
        if conn.execute("""SELECT 1 FROM pracownicy p LEFT JOIN role r ON r.id=p.rola_id
            WHERE COALESCE(p.is_admin,0)=1 OR LOWER(COALESCE(r.nazwa,''))='admin' LIMIT 1""").fetchone():
            raise PermissionError("Administrator już istnieje. Nowe konta tworzy administrator.")
        cur = conn.execute(
            "INSERT INTO pracownicy (email, haslo_hash, imie, nazwisko, aktywny, is_admin, rola_id) VALUES (%s, %s, %s, %s, 1, 1, %s) RETURNING id",
            (email, hash_password(password), imie, nazwisko, ADMIN_ROLE_ID),
        )
        admin_id = int(cur.fetchone()[0])
        conn.execute(
            """
            UPDATE role
            SET can_view_invoices=1, can_edit_companies=1, can_manage_users=1,
                can_view_finances=1, can_export_data=1, can_access_admin_settings=1
            WHERE id = %s
            """,
            (ADMIN_ROLE_ID,),
        )
        conn.commit()
        return admin_id
    finally:
        conn.close()
