# app/db/dao_employees.py
import bcrypt
from typing import List, Optional, Dict, Any
from app.db.dao_conn import get_conn

# Pracownicy
def add_employee(email: str, haslo: str, imie: str, nazwisko: str, aktywny: int = 1) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM pracownicy WHERE email = %s", (email,))
        if cur.fetchone():
            raise ValueError("Pracownik z takim adresem email już istnieje")
        haslo_hash = bcrypt.hashpw(haslo.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        cur.execute("""
            INSERT INTO pracownicy (email, haslo_hash, imie, nazwisko, aktywny)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (email, haslo_hash, imie, nazwisko, aktywny))
        _new_id = cur.fetchone()[0]
        new_id = _new_id
        conn.commit()
        return new_id
    finally:
        conn.close()

def login_employee(email: str, haslo: str) -> Optional[int]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, haslo_hash, aktywny
            FROM pracownicy
            WHERE email = %s
        """, (email,))
        row = cur.fetchone()
        if not row:
            return None
        if row["aktywny"] == 0:
            return None
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
            WHERE id = %s
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
            SET email = %s, imie = %s, nazwisko = %s, aktywny = %s
            WHERE id = %s
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
            SET haslo_hash = %s
            WHERE id = %s
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
            SET aktywny = %s
            WHERE id = %s
        """, (1 if active else 0, pracownik_id))
        conn.commit()
    finally:
        conn.close()

def delete_employee(pracownik_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM pracownik_firma WHERE pracownik_id = %s", (pracownik_id,))
        cur.execute("DELETE FROM pracownicy WHERE id = %s", (pracownik_id,))
        conn.commit()
    finally:
        conn.close()

# Konta email i przypisania
def get_email_accounts_for_employee(pracownik_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                id,
                pracownik_id,
                email,
                serwer_imap,
                CAST(port_imap AS INTEGER) AS port_imap,
                ssl_imap,
                serwer_smtp,
                CAST(port_smtp AS INTEGER) AS port_smtp,
                ssl_smtp,
                haslo_enc,
                aktywne
            FROM konta_email
            WHERE pracownik_id = %s AND COALESCE(systemowe, 0) = 0
            ORDER BY id
        """, (pracownik_id,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_all_email_accounts() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                id,
                pracownik_id,
                email,
                serwer_imap,
                CAST(port_imap AS INTEGER) AS port_imap,
                ssl_imap,
                serwer_smtp,
                CAST(port_smtp AS INTEGER) AS port_smtp,
                ssl_smtp,
                haslo_enc,
                aktywne
            FROM konta_email
            WHERE COALESCE(systemowe, 0) = 0
            ORDER BY id
        """)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def add_email_account_for_employee(pracownik_id: Optional[int],
                                   email: str,
                                   imap_server: str,
                                   imap_port: int,
                                   smtp_server: str,
                                   smtp_port: int,
                                   encrypted_password: str,
                                   imap_ssl: Optional[str] = None,
                                   smtp_ssl: Optional[str] = None,
                                   aktywne: int = 1) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO konta_email
            (pracownik_id, email, serwer_imap, port_imap, ssl_imap,
             serwer_smtp, port_smtp, ssl_smtp, haslo_enc, aktywne)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (
            pracownik_id,
            email,
            imap_server,
            int(imap_port) if imap_port is not None else None,
            imap_ssl,
            smtp_server,
            int(smtp_port) if smtp_port is not None else None,
            smtp_ssl,
            encrypted_password,
            int(aktywne),
        ))
        _new_id = cur.fetchone()[0]
        konto_id = _new_id
        conn.commit()
        return konto_id
    finally:
        conn.close()

def delete_email_account(konto_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM konta_email WHERE id = %s", (konto_id,))
        cur.execute("DELETE FROM pracownik_konto_email WHERE konto_id = %s", (konto_id,))
        conn.commit()
    finally:
        conn.close()

def get_email_assignments() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT pke.konto_id, ke.email, pke.pracownik_id, pr.email AS pracownik_email
            FROM pracownik_konto_email pke
            JOIN konta_email ke ON pke.konto_id = ke.id
            JOIN pracownicy pr ON pke.pracownik_id = pr.id
            ORDER BY ke.email, pr.email
        """)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def assign_email_to_employee(konto_id: int, pracownik_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pracownik_konto_email (konto_id, pracownik_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """, (konto_id, pracownik_id))
        conn.commit()
    finally:
        conn.close()

def remove_email_assignment(konto_id: int, pracownik_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM pracownik_konto_email
            WHERE konto_id = %s AND pracownik_id = %s
        """, (konto_id, pracownik_id))
        conn.commit()
    finally:
        conn.close()

# Reguły filtrów
def get_email_filters_for_employee(pracownik_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT rf.id, rf.pracownik_id, rf.konto_id, rf.od, rf."do", rf.nadawca_zawiera,
                   rf.temat_zawiera, rf.tresc_zawiera, rf.folder_docelowy, rf.oznacz_jako_przeczytane,
                   rf.usun, rf.aktywna
            FROM reguly_filtrow rf
            JOIN konta_email ke ON rf.konto_id = ke.id
            WHERE ke.pracownik_id = %s
            ORDER BY rf.id
        """, (pracownik_id,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def add_email_filter(konto_id: int,
                     od: Optional[str],
                     do: Optional[str],
                     nadawca_zawiera: Optional[str],
                     temat_zawiera: Optional[str],
                     tresc_zawiera: Optional[str],
                     folder_docelowy: str,
                     oznacz_jako_przeczytane: int = 0,
                     usun: int = 0,
                     aktywna: int = 1) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reguly_filtrow (
                konto_id, od, "do", nadawca_zawiera, temat_zawiera, tresc_zawiera,
                folder_docelowy, oznacz_jako_przeczytane, usun, aktywna
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (
            konto_id,
            od,
            do,
            nadawca_zawiera,
            temat_zawiera,
            tresc_zawiera,
            folder_docelowy,
            int(oznacz_jako_przeczytane),
            int(usun),
            int(aktywna),
        ))
        _new_id = cur.fetchone()[0]
        fid = _new_id
        conn.commit()
        return fid
    finally:
        conn.close()

def delete_email_filter(filter_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM reguly_filtrow WHERE id = %s", (filter_id,))
        conn.commit()
    finally:
        conn.close()
