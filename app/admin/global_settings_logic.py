from typing import List, Dict, Any, Optional
import sqlite3
import csv
from pathlib import Path

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

    # ROLE + UPRAWNIENIA

    def get_roles(self) -> List[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nazwa,
                   COALESCE(can_view_invoices, 0) AS can_view_invoices,
                   COALESCE(can_access_admin_settings, 0) AS can_access_admin_settings,
                   COALESCE(can_manage_users, 0) AS can_manage_users
            FROM role
            ORDER BY id
        """)
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_role(self, nazwa: str,
                 can_view_invoices: bool = False,
                 can_access_admin_settings: bool = False,
                 can_manage_users: bool = False) -> int:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO role (nazwa, can_view_invoices, can_access_admin_settings, can_manage_users)
            VALUES (?, ?, ?, ?)
        """, (nazwa, int(can_view_invoices), int(can_access_admin_settings), int(can_manage_users)))
        rid = cur.lastrowid
        conn.commit()
        conn.close()
        return rid

    def update_role_permissions(self, rola_id: int,
                                can_view_invoices: bool,
                                can_access_admin_settings: bool,
                                can_manage_users: bool):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE role
            SET can_view_invoices = ?, can_access_admin_settings = ?, can_manage_users = ?
            WHERE id = ?
        """, (int(can_view_invoices), int(can_access_admin_settings), int(can_manage_users), rola_id))
        conn.commit()
        conn.close()

    def delete_role(self, rola_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM role WHERE id = ?", (rola_id,))
        conn.commit()
        conn.close()

    def get_user_permissions(self, pracownik_id: int) -> Dict[str, bool]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                MAX(COALESCE(r.can_view_invoices, 0)) AS can_view_invoices,
                MAX(COALESCE(r.can_access_admin_settings, 0)) AS can_access_admin_settings,
                MAX(COALESCE(r.can_manage_users, 0)) AS can_manage_users
            FROM pracownik_firma pf
            JOIN role r ON pf.rola_id = r.id
            WHERE pf.pracownik_id = ?
        """, (pracownik_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return {
                "can_view_invoices": False,
                "can_access_admin_settings": False,
                "can_manage_users": False,
            }
        return {
            "can_view_invoices": bool(row["can_view_invoices"]),
            "can_access_admin_settings": bool(row["can_access_admin_settings"]),
            "can_manage_users": bool(row["can_manage_users"]),
        }

    # KONTA EMAIL + PRZYPISANIA

    def get_email_accounts(self) -> List[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                id,
                email,
                serwer_imap, port_imap, ssl_imap,
                serwer_smtp, port_smtp, ssl_smtp,
                haslo_enc
            FROM konta_email
            ORDER BY id
        """)
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_email_account(
            self,
            email: str,
            imap_server: str,
            smtp_server: str,
            password: str,
            imap_port: int,
            smtp_port: int,
            imap_ssl: str,
            smtp_ssl: str
    ) -> int:
        """Dodaje konto email i zwraca jego ID."""

        from app.utils.crypto import encrypt_password
        encrypted_pwd = encrypt_password(password)

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO konta_email (
                email,
                haslo_enc,
                serwer_imap, port_imap, ssl_imap,
                serwer_smtp, port_smtp, ssl_smtp,
                aktywne
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            email,
            encrypted_pwd,
            imap_server, imap_port, imap_ssl,
            smtp_server, smtp_port, smtp_ssl
        ))

        konto_id = cur.lastrowid
        conn.commit()
        conn.close()

        return konto_id

    def delete_email_account(self, konto_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM konta_email WHERE id = ?", (konto_id,))
        cur.execute("DELETE FROM pracownik_konto_email WHERE konto_id = ?", (konto_id,))
        conn.commit()
        conn.close()

    def get_email_assignments(self) -> List[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT pke.konto_id, ke.email, pke.pracownik_id, pr.email AS pracownik_email
            FROM pracownik_konto_email pke
            JOIN konta_email ke ON pke.konto_id = ke.id
            JOIN pracownicy pr ON pke.pracownik_id = pr.id
            ORDER BY ke.email, pr.email
        """)
        rows = cur.fetchall()
        conn.close()
        return rows

    def assign_email_to_employee(self, konto_id: int, pracownik_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO pracownik_konto_email (konto_id, pracownik_id)
            VALUES (?, ?)
        """, (konto_id, pracownik_id))
        conn.commit()
        conn.close()

    def remove_email_assignment(self, konto_id: int, pracownik_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM pracownik_konto_email
            WHERE konto_id = ? AND pracownik_id = ?
        """, (konto_id, pracownik_id))
        conn.commit()
        conn.close()

    # CENNIK GLOBALNY

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

    def import_global_pricing_from_csv(self, csv_path: str):
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(csv_path)

        conn = get_conn()
        cur = conn.cursor()

        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                klucz = row.get("Klucz")
                cena_netto_ksiegowosc = float(row.get("cena netto księgowość", "0").replace(",", "."))
                cena_za_dokument_ponad_limit = float(row.get("cena za dokument ponad limit", "0").replace(",", "."))
                zdrowotna = float(row.get("zdrowotna", "0").replace(",", "."))
                netto = float(row.get("Netto", "0").replace(",", "."))
                brutto = float(row.get("Brutto", "0").replace(",", "."))
                cur.execute("""
                    INSERT INTO cenniki (klucz, cena_netto_ksiegowosc, cena_za_dokument_ponad_limit,
                                         zdrowotna, netto, brutto)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(klucz) DO UPDATE SET
                        cena_netto_ksiegowosc = excluded.cena_netto_ksiegowosc,
                        cena_za_dokument_ponad_limit = excluded.cena_za_dokument_ponad_limit,
                        zdrowotna = excluded.zdrowotna,
                        netto = excluded.netto,
                        brutto = excluded.brutto
                """, (klucz, cena_netto_ksiegowosc, cena_za_dokument_ponad_limit,
                      zdrowotna, netto, brutto))

        conn.commit()
        conn.close()

    # USŁUGI DODATKOWE

    def get_extra_services(self) -> List[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nazwa, cena_netto, stawka_vat, COALESCE(kategoria, '') AS kategoria
            FROM uslugi_dodatkowe
            ORDER BY nazwa
        """)
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_extra_service(self, nazwa: str, cena_netto: float, stawka_vat: str, kategoria: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat, kategoria)
            VALUES (?, ?, ?, ?)
        """, (nazwa, cena_netto, stawka_vat, kategoria))
        conn.commit()
        conn.close()

    def delete_extra_service(self, usluga_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM uslugi_dodatkowe WHERE id = ?", (usluga_id,))
        conn.commit()
        conn.close()

    # FIRMY

    def get_all_companies(self) -> List[sqlite3.Row]:
        return list_companies()

    def delete_company(self, firma_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM firmy WHERE id = ?", (firma_id,))
        conn.commit()
        conn.close()

    # LOGI

    def get_login_logs(self) -> List[sqlite3.Row]:
        return []

    # SYSTEM

    def backup_database(self, target_path: str):
        pass

    def cleanup_old_emails(self, older_than_days: int):
        pass
