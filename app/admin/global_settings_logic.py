# app/logic/global_settings_logic.py
from typing import List, Dict, Any, Optional
import sqlite3

from app.db.dao_conn import get_conn
from app.db.dao_employees import (
    add_employee,
    list_employees,
    update_employee,
    delete_employee,
    set_employee_active,
    change_employee_password,
)
from app.db.dao_companies import (
    list_companies,
    get_company,
    save_company,
    get_company_shareholders,
    save_company_shareholders,
    get_month_status,
    save_month_status,
    count_documents_for_month,
    wybierz_cennik,
)


class GlobalSettingsLogic:
    """
    Logika panelu admina – bez PyQt, tylko operacje na bazie.
    """

    # ==========================
    # UŻYTKOWNICY
    # ==========================

    def get_users(self) -> List[sqlite3.Row]:
        return list_employees()

    def create_user(self, email: str, imie: str, nazwisko: str, haslo: str) -> int:
        return add_employee(email=email, haslo=haslo, imie=imie, nazwisko=nazwisko)

    def update_user(self, pracownik_id: int, email: str, imie: str, nazwisko: str, aktywny: bool):
        update_employee(pracownik_id, email, imie, nazwisko, 1 if aktywny else 0)

    def delete_user(self, pracownik_id: int):
        delete_employee(pracownik_id)

    def set_user_active(self, pracownik_id: int, active: bool):
        set_employee_active(pracownik_id, active)

    def reset_user_password(self, pracownik_id: int, new_password: str):
        change_employee_password(pracownik_id, new_password)

    # ==========================
    # ROLE
    # ==========================

    def get_roles(self) -> List[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa FROM role ORDER BY id")
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_role(self, nazwa: str) -> int:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO role (nazwa) VALUES (?)", (nazwa,))
        rid = cur.lastrowid
        conn.commit()
        conn.close()
        return rid

    def delete_role(self, rola_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM role WHERE id = ?", (rola_id,))
        conn.commit()
        conn.close()

    # ==========================
    # KONTA EMAIL
    # ==========================

    def get_email_accounts(self) -> List[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, pracownik_id, email, serwer_imap, serwer_smtp
            FROM konta_email
            ORDER BY id
        """)
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_email_account(self, pracownik_id: int, email: str, imap: str, smtp: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO konta_email (pracownik_id, email, serwer_imap, serwer_smtp, haslo_enc)
            VALUES (?, ?, ?, ?, '')
        """, (pracownik_id, email, imap, smtp))
        conn.commit()
        conn.close()

    def delete_email_account(self, konto_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM konta_email WHERE id = ?", (konto_id,))
        conn.commit()
        conn.close()

    # ==========================
    # CENNIK GLOBALNY
    # ==========================

    def get_global_pricing(self) -> List[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT klucz, cena_netto_ksiegowosc, cena_za_dokument_ponad_limit,
                   zdrowotna, netto, brutto
            FROM cenniki
            ORDER BY klucz
        """)
        rows = cur.fetchall()
        conn.close()
        return rows

    # ==========================
    # USŁUGI DODATKOWE
    # ==========================

    def get_extra_services(self) -> List[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nazwa, cena_netto, stawka_vat
            FROM uslugi_dodatkowe
            ORDER BY nazwa
        """)
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_extra_service(self, nazwa: str, cena_netto: float, stawka_vat: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat)
            VALUES (?, ?, ?)
        """, (nazwa, cena_netto, stawka_vat))
        conn.commit()
        conn.close()

    def delete_extra_service(self, usluga_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM uslugi_dodatkowe WHERE id = ?", (usluga_id,))
        conn.commit()
        conn.close()

    # ==========================
    # FIRMY
    # ==========================

    def get_all_companies(self) -> List[sqlite3.Row]:
        return list_companies()

    def delete_company(self, firma_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM firmy WHERE id = ?", (firma_id,))
        conn.commit()
        conn.close()

    # ==========================
    # LOGI (prosty wariant)
    # ==========================

    def get_login_logs(self) -> List[sqlite3.Row]:
        # jeśli dodasz tabelę logów, tu ją obsłużysz
        return []

    # ==========================
    # SYSTEM (backup / czyszczenie – stub)
    # ==========================

    def backup_database(self, target_path: str):
        # tu możesz zrobić kopię pliku DB_NAME do target_path
        pass

    def cleanup_old_emails(self, older_than_days: int):
        # tu możesz usunąć stare wiadomości z tabeli wiadomosci
        pass
