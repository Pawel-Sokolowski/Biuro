# app/db/dao_employees.py
import bcrypt
from typing import List, Optional, Dict, Any
from app.db.dao_conn import get_conn


def add_employee(email: str, haslo: str, imie: str, nazwisko: str, aktywny: int = 1) -> int:
    """
    Dodaje nowego pracownika.
    Zwraca ID nowo utworzonego pracownika.
    Jeśli email już istnieje, rzuca ValueError.
    """

    # sprawdź czy email już istnieje
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM pracownicy WHERE email = ?", (email,))
        if cur.fetchone():
            raise ValueError("Pracownik z takim adresem email już istnieje")

        haslo_hash = bcrypt.hashpw(haslo.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        cur.execute("""
            INSERT INTO pracownicy (email, haslo_hash, imie, nazwisko, aktywny)
            VALUES (?, ?, ?, ?, ?)
        """, (email, haslo_hash, imie, nazwisko, aktywny))

        new_id = cur.lastrowid
        conn.commit()
        return new_id
    finally:
        conn.close()


def login_employee(email: str, haslo: str) -> Optional[int]:
    """
    Próbuje zalogować pracownika.
    Zwraca ID pracownika lub None.
    Blokuje logowanie jeśli aktywny = 0.
    """

    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT id, haslo_hash, aktywny
            FROM pracownicy
            WHERE email = ?
        """, (email,))
        row = cur.fetchone()

        if not row:
            return None

        if row["aktywny"] == 0:
            return None  # pracownik zablokowany

        if bcrypt.checkpw(haslo.encode("utf-8"), row["haslo_hash"].encode("utf-8")):
            return row["id"]

        return None
    finally:
        conn.close()


def get_employee_info(pracownik_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, email, imie, nazwisko, aktywny, data_utworzenia
            FROM pracownicy
            WHERE id = ?
        """, (pracownik_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_employees() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, email, imie, nazwisko, aktywny
            FROM pracownicy
            ORDER BY imie, nazwisko
        """)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_employee(pracownik_id: int, email: str, imie: str, nazwisko: str, aktywny: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE pracownicy
            SET email = ?, imie = ?, nazwisko = ?, aktywny = ?
            WHERE id = ?
        """, (email, imie, nazwisko, aktywny, pracownik_id))
        conn.commit()
    finally:
        conn.close()


def change_employee_password(pracownik_id: int, new_password: str) -> None:
    new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE pracownicy
            SET haslo_hash = ?
            WHERE id = ?
        """, (new_hash, pracownik_id))
        conn.commit()
    finally:
        conn.close()


def set_employee_active(pracownik_id: int, active: bool) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE pracownicy
            SET aktywny = ?
            WHERE id = ?
        """, (1 if active else 0, pracownik_id))
        conn.commit()
    finally:
        conn.close()


def delete_employee(pracownik_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM pracownik_firma WHERE pracownik_id = ?", (pracownik_id,))
        cur.execute("DELETE FROM pracownicy WHERE id = ?", (pracownik_id,))
        conn.commit()
    finally:
        conn.close()
