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
import logging

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from app.db.dao_companies import get_conn

logger = logging.getLogger(__name__)

# --- Konfiguracja szyfrowania (Fernet) ---
# W produkcji klucz powinien pochodzić ze zmiennych środowiskowych.
def _get_encryption_key() -> bytes:
    """Get the deployment-specific encryption key."""
    key_str = os.environ.get("BIO_ENCRYPTION_KEY")
    if not key_str:
        raise RuntimeError(
            "BIO_ENCRYPTION_KEY is required to access stored email passwords."
        )
    
    if isinstance(key_str, str):
        key_str = key_str.encode("utf-8")
    
    try:
        # Validate Fernet key format
        Fernet(key_str)
        return key_str
    except Exception as e:
        logger.error(f"Invalid encryption key format: {e}")
        raise ValueError("Invalid encryption key format. Must be a valid Fernet key.") from e

def _cipher() -> Fernet:
    return Fernet(_get_encryption_key())

# --- Funkcje szyfrowania haseł email ---
def encrypt_password(password: str) -> str:
    """
    Zaszyfruj hasło (np. do konta e-mail) i zwróć string (utf-8).
    
    Args:
        password: Plain text password to encrypt
        
    Returns:
        Encrypted password as string
        
    Raises:
        ValueError: If password is None or empty
    """
    if not password:
        raise ValueError("Password must not be None or empty")
    
    try:
        encrypted = _cipher().encrypt(password.encode("utf-8")).decode("utf-8")
        return encrypted
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        raise ValueError("Failed to encrypt password") from e


def decrypt_password(encrypted_password: str) -> str:
    """
    Odszyfruj hasło zaszyfrowane przez encrypt_password.
    
    Args:
        encrypted_password: Encrypted password string
        
    Returns:
        Decrypted password as string, empty string if decryption fails
    """
    if not encrypted_password:
        return ""
    
    try:
        decrypted = _cipher().decrypt(encrypted_password.encode("utf-8")).decode("utf-8")
        return decrypted
    except InvalidToken:
        logger.warning("Failed to decrypt password: invalid token or corrupted data")
        return ""
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return ""


# --- Funkcje hashujące hasła użytkowników (bcrypt) ---
def hash_password(password: str) -> str:
    """
    Zwraca bcrypt hash jako string utf-8.
    
    Args:
        password: Plain text password
        
    Returns:
        Bcrypt hash as string
        
    Raises:
        ValueError: If password is None or empty
    """
    if not password:
        raise ValueError("Password must not be None or empty")
    
    try:
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        return hashed.decode("utf-8")
    except Exception as e:
        logger.error(f"Password hashing failed: {e}")
        raise ValueError("Failed to hash password") from e


def verify_password(password: str, password_hash: str) -> bool:
    """
    Weryfikuje hasło względem hasha bcrypt.
    Obsługuje wejście jako str lub bytes.
    
    Args:
        password: Plain text password
        password_hash: Bcrypt hash to verify against
        
    Returns:
        True if password matches, False otherwise
    """
    if not password or not password_hash:
        return False
    
    try:
        p_bytes = password.encode("utf-8") if isinstance(password, str) else password
        h_bytes = password_hash.encode("utf-8") if isinstance(password_hash, str) else password_hash
        return bcrypt.checkpw(p_bytes, h_bytes)
    except (ValueError, TypeError):
        return False
    except Exception as e:
        logger.error(f"Password verification failed: {e}")
        return False


# --- Pomocniczne funkcje do wykrywania kolumn w tabeli pracownicy ---
def _get_pracownicy_columns(conn: sqlite3.Connection) -> _t.List[str]:
    """Get available columns in pracownicy table."""
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(pracownicy)")
        cols = [row[1] for row in cur.fetchall()]  # row[1] = name
        return cols
    except sqlite3.OperationalError as e:
        logger.error(f"Failed to get pracownicy columns: {e}")
        return []


def _choose_password_column(cols: _t.List[str]) -> _t.Optional[str]:
    """
    Wybiera kolumnę przechowującą hash hasła.
    Preferencje: haslo_hash, haslo, password, passwd
    """
    preferred = ["haslo_hash", "haslo", "password", "passwd"]
    for p in preferred:
        if p in cols:
            return p
    return None


# --- Funkcje autoryzacyjne i zarządzania użytkownikami ---
def register_user(email: str, password: str, imie: str = "", nazwisko: str = "", is_admin_flag: bool = False) -> _t.Tuple[bool, _t.Optional[int], str]:
    """
    Rejestruje nowego pracownika.
    Zwraca (success, user_id_or_None, message).
    Próbuje dopasować kolumnę hasła automatycznie.
    
    Args:
        email: User email (must be unique)
        password: Plain text password
        imie: First name
        nazwisko: Last name
        is_admin_flag: Whether user is admin
        
    Returns:
        Tuple of (success, user_id, message)
    """
    if not email or not password:
        return False, None, "Email i hasło są wymagane."
    
    # Validate email format
    if "@" not in email or "." not in email.split("@")[1]:
        return False, None, "Nieprawidłowy format adresu email."

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

        # przygotuj kolumny do insertu
        if pwd_col:
            # Use parameterized query to prevent SQL injection
            try:
                cur.execute(
                    f"INSERT INTO pracownicy (email, {pwd_col}, imie, nazwisko, aktywny, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
                    (email, pwd_hash, imie, nazwisko, 1, 1 if is_admin_flag else 0),
                )
                conn.commit()
                user_id = cur.lastrowid
                logger.info(f"User registered: {email} (ID: {user_id})")
                return True, user_id, "Użytkownik zarejestrowany pomyślnie."
            except sqlite3.OperationalError:
                pass

        # fallback: spróbuj wstawić minimalny rekord
        try:
            cur.execute(
                "INSERT INTO pracownicy (email, imie, nazwisko, aktywny, is_admin) VALUES (?, ?, ?, ?, ?)",
                (email, imie, nazwisko, 1, 1 if is_admin_flag else 0),
            )
            user_id = cur.lastrowid
            
            # spróbuj zapisać hash w kolumnie haslo_hash lub haslo jeśli istnieje
            if pwd_col:
                cur.execute(f"UPDATE pracownicy SET {pwd_col} = ? WHERE id = ?", (pwd_hash, user_id))
            
            conn.commit()
            logger.info(f"User registered (fallback): {email} (ID: {user_id})")
            return True, user_id, "Użytkownik zarejestrowany pomyślnie (fallback)."
        except Exception as e:
            conn.rollback()
            logger.error(f"User registration failed: {e}")
            return False, None, f"Błąd rejestracji: {str(e)}"
    finally:
        conn.close()


def login_user(email: str, password: str) -> _t.Tuple[bool, _t.Optional[int], str]:
    """
    Próbuje zalogować użytkownika po emailu i haśle.
    Zwraca (success, user_id_or_None, message).
    
    Args:
        email: User email
        password: Plain text password
        
    Returns:
        Tuple of (success, user_id, message)
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
            select_cols.append("haslo_hash")  # może nie istnieć, zapytanie obsłuży wyjątek

        q = f"SELECT {', '.join(select_cols)} FROM pracownicy WHERE email = ?"
        cur.execute(q, (email,))
        row = cur.fetchone()
        if not row:
            logger.warning(f"Login attempt with non-existent email: {email}")
            return False, None, "Nie znaleziono użytkownika o podanym adresie email."

        # mapowanie wyników
        user_id = row[0]
        aktywny = bool(row[1]) if row[1] is not None else True
        stored_hash = row[2] if len(row) > 2 else None

        if not aktywny:
            logger.warning(f"Login attempt with inactive account: {email} (ID: {user_id})")
            return False, None, "Konto użytkownika jest nieaktywne."

        if not stored_hash:
            logger.error(f"No password hash for user: {email} (ID: {user_id})")
            return False, None, "Brak hasła dla konta. Skontaktuj się z administratorem."

        if verify_password(password, stored_hash):
            logger.info(f"Successful login: {email} (ID: {user_id})")
            return True, user_id, "Zalogowano pomyślnie."
        
        logger.warning(f"Failed login attempt: {email} (incorrect password)")
        return False, None, "Nieprawidłowe hasło."
    except sqlite3.OperationalError:
        logger.error("Database structure error: missing password column")
        return False, None, "Błąd struktury bazy danych (brak kolumny hasła)."
    finally:
        conn.close()


def get_employee_info(pracownik_id: int) -> _t.Optional[_t.Dict[str, _t.Any]]:
    """
    Zwraca podstawowe informacje o pracowniku jako słownik lub None.
    
    Args:
        pracownik_id: Employee ID
        
    Returns:
        Dictionary with employee info or None if not found
    """
    if not isinstance(pracownik_id, int) or pracownik_id <= 0:
        return None
    
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
    except sqlite3.OperationalError as e:
        logger.error(f"Failed to get employee info: {e}")
        return None
    finally:
        conn.close()


def get_employee_assigned_companies(pracownik_id: int) -> _t.List[_t.Tuple]:
    """
    Zwraca listę firm przypisanych do pracownika.
    Każdy element to krotka zgodna z zapytaniem SQL.
    
    Args:
        pracownik_id: Employee ID
        
    Returns:
        List of company tuples
    """
    if not isinstance(pracownik_id, int) or pracownik_id <= 0:
        return []
    
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
    except sqlite3.OperationalError as e:
        logger.error(f"Failed to get employee companies: {e}")
        return []
    finally:
        conn.close()


def get_employee_role(pracownik_id: int) -> _t.List[str]:
    """
    Zwraca listę nazw ról przypisanych do pracownika.
    
    Args:
        pracownik_id: Employee ID
        
    Returns:
        List of role names
    """
    if not isinstance(pracownik_id, int) or pracownik_id <= 0:
        return []
    
    conn = get_conn()
    cur = conn.cursor()
    try:
        # próbujemy dwie możliwe struktury
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
    
    Args:
        pracownik_id: Employee ID
        
    Returns:
        True if user is admin, False otherwise
    """
    if not isinstance(pracownik_id, int) or pracownik_id <= 0:
        return False
    
    roles = get_employee_role(pracownik_id)
    return any(str(r).lower() == "admin" for r in roles)
