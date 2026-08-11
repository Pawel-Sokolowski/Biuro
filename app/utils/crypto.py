# app/utils/crypto.py
"""
Zaktualizowany moduł kryptograficzny i autoryzacyjny.
Nie importuje main.py (unika cyklicznych importów). Korzysta z DAO get_conn().
Zawiera:
- szyfrowanie/odszyfrowanie haseł do kont email (Fernet)
- hash/verify haseł użytkowników (bcrypt)
- funkcje rejestracji i logowania użytkowników
- pomocnicze funkcje pobierania informacji o pracowniku i ról
"""

import sqlite3
import os
import typing as _t

import bcrypt
from cryptography.fernet import Fernet

from app.db.dao_companies import get_conn

# --- Konfiguracja szyfrowania (Fernet) ---
# W produkcji klucz powinien pochodzić ze zmiennych środowiskowych.
# Możesz wygenerować nowy klucz: Fernet.generate_key()
_DEFAULT_ENCRYPTION_KEY = os.environ.get(
    "BIO_ENCRYPTION_KEY", b"Qa6bILYFSFYxYL2wAskbJN29N3FOnJb0zcqMwXrgtXM="
)
if isinstance(_DEFAULT_ENCRYPTION_KEY, str):
    _DEFAULT_ENCRYPTION_KEY = _DEFAULT_ENCRYPTION_KEY.encode("utf-8")
cipher_suite = Fernet(_DEFAULT_ENCRYPTION_KEY)

# --- Funkcje szyfrowania haseł email ---
def encrypt_password(password: str) -> str:
    """
    Zaszyfruj hasło (np. do konta e-mail) i zwróć string (utf-8).
    """
    if password is None:
        raise ValueError("Password must not be None")
    return cipher_suite.encrypt(password.encode("utf-8")).decode("utf-8")


def decrypt_password(encrypted_password: str) -> str:
    """
    Odszyfruj hasło zaszyfrowane przez encrypt_password.
    """
    if encrypted_password is None:
        return ""
    return cipher_suite.decrypt(encrypted_password.encode("utf-8")).decode("utf-8")


# --- Funkcje hashujące hasła użytkowników (bcrypt) ---
def hash_password(password: str) -> str:
    """
    Zwraca bcrypt hash jako string utf-8.
    """
    if password is None:
        raise ValueError("Password must not be None")
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """
    Weryfikuje hasło względem hasha bcrypt.
    Obsługuje wejście jako str lub bytes.
    """
    if password is None or password_hash is None:
        return False
    p_bytes = password.encode("utf-8") if isinstance(password, str) else password
    h_bytes = password_hash.encode("utf-8") if isinstance(password_hash, str) else password_hash
    try:
        return bcrypt.checkpw(p_bytes, h_bytes)
    except Exception:
        return False


# --- Pomocniczne funkcje do wykrywania kolumn w tabeli pracownicy ---
def _get_pracownicy_columns(conn: sqlite3.Connection) -> _t.List[str]:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(pracownicy)")
    cols = [row[1] for row in cur.fetchall()]  # row[1] = name
    return cols


def _choose_password_column(cols: _t.List[str]) -> _t.Optional[str]:
    """
    Wybiera kolumnę przechowującą hash hasła.
    Preferencje: haslo_hash, haslo, password, passwd
    """
    preferred = ["haslo_hash", "haslo", "password", "passwd"]
    for p in preferred:
        if p in cols:
            return p
    # jeśli żadna nie istnieje, zwróć None
    return None


# --- Funkcje autoryzacyjne i zarządzania użytkownikami ---
def register_user(email: str, password: str, imie: str = "", nazwisko: str = "", is_admin_flag: bool = False) -> _t.Tuple[bool, _t.Optional[int], str]:
    """
    Rejestruje nowego pracownika.
    Zwraca (success, user_id_or_None, message).
    Próbuje dopasować kolumnę hasła automatycznie.
    """
    if not email or not password:
        return False, None, "Email i hasło są wymagane."

    conn = get_conn()
    cur = conn.cursor()
    try:
        # sprawdź czy email już istnieje
        cur.execute("SELECT id FROM pracownicy WHERE email = ?", (email,))
        if cur.fetchone():
            return False, None, "Użytkownik o podanym adresie email już istnieje."

        pwd_hash = hash_password(password)

        cols = _get_pracownicy_columns(conn)
        pwd_col = _choose_password_column(cols)

        # przygotuj kolumny do insertu - spróbuj użyć haslo_hash, jeśli nie ma, użyj haslo, a jeśli nie ma, spróbuj utworzyć rekord z minimalnymi kolumnami
        if pwd_col:
            # spróbuj wstawić z kolumną pwd_col
            try:
                cur.execute(
                    f"INSERT INTO pracownicy (email, {pwd_col}, imie, nazwisko, aktywny, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
                    (email, pwd_hash, imie, nazwisko, 1, 1 if is_admin_flag else 0),
                )
                conn.commit()
                return True, cur.lastrowid, "Użytkownik zarejestrowany pomyślnie."
            except sqlite3.OperationalError:
                # spróbujemy alternatywnie bez kolumny hasła (rzadki przypadek)
                pass

        # fallback: spróbuj wstawić minimalny rekord (jeśli tabela ma kolumny email, imie, nazwisko)
        try:
            cur.execute(
                "INSERT INTO pracownicy (email, imie, nazwisko, aktywny, is_admin) VALUES (?, ?, ?, ?, ?)",
                (email, imie, nazwisko, 1, 1 if is_admin_flag else 0),
            )
            user_id = cur.lastrowid
            # spróbuj zapisać hash w kolumnie haslo_hash lub haslo jeśli istnieje
            if "haslo_hash" in cols:
                cur.execute("UPDATE pracownicy SET haslo_hash = ? WHERE id = ?", (pwd_hash, user_id))
            elif "haslo" in cols:
                cur.execute("UPDATE pracownicy SET haslo = ? WHERE id = ?", (pwd_hash, user_id))
            conn.commit()
            return True, user_id, "Użytkownik zarejestrowany pomyślnie (fallback)."
        except Exception as e:
            conn.rollback()
            return False, None, f"Błąd rejestracji: {str(e)}"
    finally:
        conn.close()


def login_user(email: str, password: str) -> _t.Tuple[bool, _t.Optional[int], str]:
    """
    Próbuje zalogować użytkownika po emailu i haśle.
    Zwraca (success, user_id_or_None, message).
    """
    if not email or not password:
        return False, None, "Email i hasło są wymagane."

    conn = get_conn()
    cur = conn.cursor()
    try:
        cols = _get_pracownicy_columns(conn)
        pwd_col = _choose_password_column(cols)

        # zbuduj zapytanie dynamicznie w zależności od dostępnych kolumn
        select_cols = ["id", "aktywny"]
        if pwd_col:
            select_cols.append(pwd_col)
        else:
            # jeśli nie ma kolumny hasła, spróbuj pobrać wszystkie kolumny i znaleźć coś sensownego
            select_cols.append("haslo_hash")  # może nie istnieć, zapytanie obsłuży wyjątek

        q = f"SELECT {', '.join(select_cols)} FROM pracownicy WHERE email = ?"
        cur.execute(q, (email,))
        row = cur.fetchone()
        if not row:
            return False, None, "Nie znaleziono użytkownika o podanym adresie email."

        # mapowanie wyników
        # row[0] = id, row[1] = aktywny, row[2] = hash (jeśli istnieje)
        user_id = row[0]
        aktywny = bool(row[1]) if row[1] is not None else True
        stored_hash = row[2] if len(row) > 2 else None

        if not aktywny:
            return False, None, "Konto użytkownika jest nieaktywne."

        if not stored_hash:
            return False, None, "Brak hasła dla konta. Skontaktuj się z administratorem."

        if verify_password(password, stored_hash):
            return True, user_id, "Zalogowano pomyślnie."
        return False, None, "Nieprawidłowe hasło."
    except sqlite3.OperationalError:
        # struktura tabeli inna niż oczekiwana
        return False, None, "Błąd struktury bazy danych (brak kolumny hasła)."
    finally:
        conn.close()


def get_employee_info(pracownik_id: int) -> _t.Optional[_t.Dict[str, _t.Any]]:
    """
    Zwraca podstawowe informacje o pracowniku jako słownik lub None.
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, email, imie, nazwisko, aktywny FROM pracownicy WHERE id = ?", (pracownik_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "email": row[1],
            "imie": row[2],
            "nazwisko": row[3],
            "aktywny": bool(row[4]) if row[4] is not None else True,
        }
    finally:
        conn.close()


def get_employee_assigned_companies(pracownik_id: int) -> _t.List[_t.Tuple]:
    """
    Zwraca listę firm przypisanych do pracownika.
    Każdy element to krotka zgodna z zapytaniem SQL.
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT f.id, f.nazwa, f.nip, pf.rola
            FROM firmy f
            JOIN pracownik_firma pf ON f.id = pf.firma_id
            WHERE pf.pracownik_id = ?
            ORDER BY f.nazwa
        """, (pracownik_id,))
        return cur.fetchall()
    finally:
        conn.close()


def get_employee_role(pracownik_id: int) -> _t.List[str]:
    """
    Zwraca listę nazw ról przypisanych do pracownika.
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        # próbujemy dwie możliwe struktury: jeśli istnieje tabela role z relacją, użyjemy jej,
        # w przeciwnym razie spróbujemy pola 'rola' w tabeli pracownik_firma.
        try:
            cur.execute("""
                SELECT DISTINCT r.nazwa
                FROM role r
                JOIN pracownik_firma pf ON r.id = pf.rola_id
                WHERE pf.pracownik_id = ?
            """, (pracownik_id,))
            rows = cur.fetchall()
            if rows:
                return [r[0] for r in rows]
        except sqlite3.OperationalError:
            # tabela role lub kolumna rola_id nie istnieje - fallback
            pass

        # fallback: pobierz pole 'rola' z pracownik_firma
        try:
            cur.execute("SELECT DISTINCT rola FROM pracownik_firma WHERE pracownik_id = ?", (pracownik_id,))
            rows = cur.fetchall()
            return [r[0] for r in rows if r[0] is not None]
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()


def is_admin(pracownik_id: int) -> bool:
    """
    Sprawdza, czy pracownik ma rolę 'admin' (ignoruje wielkość liter).
    """
    roles = get_employee_role(pracownik_id)
    return any(str(r).lower() == "admin" for r in roles)
