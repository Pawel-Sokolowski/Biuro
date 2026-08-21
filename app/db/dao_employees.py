# app/db/dao_employees.py
import bcrypt
from typing import List, Optional
from app.db.dao_conn import get_conn


def add_employee(email: str, haslo: str, imie: str, nazwisko: str, aktywny: int = 1) -> int:
    """
    Dodaje nowego pracownika.
    Zwraca ID nowo utworzonego pracownika.
    """

    haslo_hash = bcrypt.hashpw(haslo.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO pracownicy (email, haslo_hash, imie, nazwisko, aktywny)
        VALUES (?, ?, ?, ?, ?)
    """, (email, haslo_hash, imie, nazwisko, aktywny))

    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    return new_id

def login_employee(email: str, haslo: str) -> Optional[int]:
    """
    Próbuje zalogować pracownika.
    Zwraca ID pracownika lub None.
    Blokuje logowanie jeśli aktywny = 0.
    """

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, haslo_hash, aktywny
        FROM pracownicy
        WHERE email = ?
    """, (email,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    if row["aktywny"] == 0:
        return None  # pracownik zablokowany

    if bcrypt.checkpw(haslo.encode("utf-8"), row["haslo_hash"].encode("utf-8")):
        return row["id"]

    return None


def get_employee_info(pracownik_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, imie, nazwisko, aktywny, data_utworzenia
        FROM pracownicy
        WHERE id = ?
    """, (pracownik_id,))
    row = cur.fetchone()
    conn.close()
    return row


def list_employees() -> List:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, imie, nazwisko, aktywny
        FROM pracownicy
        ORDER BY imie, nazwisko
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def update_employee(pracownik_id: int, email: str, imie: str, nazwisko: str, aktywny: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE pracownicy
        SET email = ?, imie = ?, nazwisko = ?, aktywny = ?
        WHERE id = ?
    """, (email, imie, nazwisko, aktywny, pracownik_id))

    conn.commit()
    conn.close()


def change_employee_password(pracownik_id: int, new_password: str):
    new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE pracownicy
        SET haslo_hash = ?
        WHERE id = ?
    """, (new_hash, pracownik_id))

    conn.commit()
    conn.close()


def set_employee_active(pracownik_id: int, active: bool):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE pracownicy
        SET aktywny = ?
        WHERE id = ?
    """, (1 if active else 0, pracownik_id))

    conn.commit()
    conn.close()

def delete_employee(pracownik_id: int):
    conn = get_conn()
    cur = conn.cursor()

    # Usuwamy powiązania pracownik_firma
    cur.execute("DELETE FROM pracownik_firma WHERE pracownik_id = ?", (pracownik_id,))

    # Usuwamy pracownika
    cur.execute("DELETE FROM pracownicy WHERE id = ?", (pracownik_id,))

    conn.commit()
    conn.close()
