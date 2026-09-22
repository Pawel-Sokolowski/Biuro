# app/admin/global_settings_logic.py
"""
Kompletny, rozszerzony GlobalSettingsLogic używany przez UI.
Zawiera: dostęp do DB, ustawienia aplikacji, role, użytkownicy, konta email,
cenniki, kadry, usługi dodatkowe, firmy, faktury (CRUD + pozycje), tagi,
załączniki, logi, backup oraz pomocnicze migracje schematu.
"""

import csv
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

import openpyxl
import sqlite3

from app.db.dao_conn import get_conn
from app.db.dao_companies import list_companies
from app.db.dao_employees import (
    list_employees,
    add_employee,
    update_employee,
    delete_employee,
    set_employee_active,
    change_employee_password,
)
from app.db.dao_app_settings import (
    get_setting as dao_get_setting,
    set_setting as dao_set_setting,
    delete_setting as dao_delete_setting,
    list_settings as dao_list_settings,
)


def safe_float(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s == "" or s.lower() == "none":
        return 0.0
    try:
        return float(s.replace(",", "."))
    except Exception:
        return 0.0


def now_iso() -> str:
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def _row_to_dict_safe(row):
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        d = {}
        for k in row.keys():
            d[k] = row[k]
        return d


class GlobalSettingsLogic:
    def __init__(self):
        try:
            self.ensure_schema_minimum()
        except Exception:
            pass

    def _get_conn(self) -> sqlite3.Connection:
        conn = get_conn()
        try:
            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        return conn

    # -------------------------
    # Użytkownicy (delegacja do DAO)
    # -------------------------
    def get_users(self) -> List[sqlite3.Row]:
        return list_employees()

    def create_user(self, email: str, imie: str, nazwisko: str, haslo: str) -> int:
        return add_employee(email=email, haslo=haslo, imie=imie, nazwisko=nazwisko)

    def add_user(self, email: str, imie: str, nazwisko: str, haslo: str) -> int:
        return self.create_user(email, imie, nazwisko, haslo)

    def update_user(
        self,
        pracownik_id: int,
        email: str,
        imie: str,
        nazwisko: str,
        aktywny: Optional[bool] = None,
    ):
        update_employee(
            pracownik_id,
            email,
            imie,
            nazwisko,
            1 if (aktywny is None or aktywny) else 0,
        )

    def delete_user(self, pracownik_id: int):
        delete_employee(pracownik_id)

    def set_user_active(self, pracownik_id: int, active: bool):
        set_employee_active(pracownik_id, active)

    def toggle_user_active(self, pracownik_id: int):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT aktywny FROM pracownicy WHERE id = ?", (pracownik_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return
        new_val = 0 if row["aktywny"] else 1
        cur.execute(
            "UPDATE pracownicy SET aktywny = ? WHERE id = ?",
            (new_val, pracownik_id),
        )
        conn.commit()
        conn.close()

    def reset_user_password(self, pracownik_id: int, new_password: str):
        change_employee_password(pracownik_id, new_password)

    def reset_password(self, uid: int, new_pass: str):
        self.reset_user_password(uid, new_pass)

    # -------------------------
    # Role + uprawnienia
    # -------------------------
    def get_roles(self) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, nazwa,
                   COALESCE(can_view_invoices, 0) AS can_view_invoices,
                   COALESCE(can_edit_companies, 0) AS can_edit_companies,
                   COALESCE(can_manage_users, 0) AS can_manage_users,
                   COALESCE(can_view_finances, 0) AS can_view_finances,
                   COALESCE(can_export_data, 0) AS can_export_data,
                   COALESCE(can_access_admin_settings, 0) AS can_access_admin_settings
            FROM role
            ORDER BY id
            """
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_role(self, rid: int) -> Optional[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, nazwa,
                   COALESCE(can_view_invoices, 0) AS can_view_invoices,
                   COALESCE(can_edit_companies, 0) AS can_edit_companies,
                   COALESCE(can_manage_users, 0) AS can_manage_users,
                   COALESCE(can_view_finances, 0) AS can_view_finances,
                   COALESCE(can_export_data, 0) AS can_export_data,
                   COALESCE(can_access_admin_settings, 0) AS can_access_admin_settings
            FROM role
            WHERE id = ?
            """,
            (rid,),
        )
        row = cur.fetchone()
        conn.close()
        return row

    def add_role(
        self,
        nazwa: str,
        can_view_invoices: bool = False,
        can_edit_companies: bool = False,
        can_manage_users: bool = False,
        can_view_finances: bool = False,
        can_export_data: bool = False,
        can_access_admin_settings: bool = False,
    ) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO role (nazwa, can_view_invoices, can_edit_companies, can_manage_users,
                              can_view_finances, can_export_data, can_access_admin_settings)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nazwa,
                int(can_view_invoices),
                int(can_edit_companies),
                int(can_manage_users),
                int(can_view_finances),
                int(can_export_data),
                int(can_access_admin_settings),
            ),
        )
        rid = cur.lastrowid
        conn.commit()
        conn.close()
        return rid

    def update_role(self, rid: int, nazwa: str, **perms):
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE role
                SET nazwa = ?,
                    can_view_invoices = COALESCE(?, can_view_invoices),
                    can_edit_companies = COALESCE(?, can_edit_companies),
                    can_manage_users = COALESCE(?, can_manage_users),
                    can_view_finances = COALESCE(?, can_view_finances),
                    can_export_data = COALESCE(?, can_export_data),
                    can_access_admin_settings = COALESCE(?, can_access_admin_settings)
                WHERE id = ?
                """,
                (
                    nazwa,
                    int(perms.get("can_view_invoices", 0)),
                    int(perms.get("can_edit_companies", 0)),
                    int(perms.get("can_manage_users", 0)),
                    int(perms.get("can_view_finances", 0)),
                    int(perms.get("can_export_data", 0)),
                    int(perms.get("can_access_admin_settings", 0)),
                    rid,
                ),
            )
        except sqlite3.OperationalError:
            cur.execute("UPDATE role SET nazwa = ? WHERE id = ?", (nazwa, rid))
        conn.commit()
        conn.close()

    def delete_role(self, rola_id: int):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM role WHERE id = ?", (rola_id,))
        conn.commit()
        conn.close()

    def get_user_permissions(self, pracownik_id: int) -> Dict[str, bool]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                MAX(COALESCE(r.can_view_invoices, 0)) AS can_view_invoices,
                MAX(COALESCE(r.can_access_admin_settings, 0)) AS can_access_admin_settings,
                MAX(COALESCE(r.can_manage_users, 0)) AS can_manage_users
            FROM pracownik_firma pf
            JOIN role r ON pf.rola_id = r.id
            WHERE pf.pracownik_id = ?
            """,
            (pracownik_id,),
        )
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

    def assign_role(self, uid: int, rid: int, firma_id: int = 1):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO pracownik_firma (pracownik_id, firma_id, rola_id)
            VALUES (?, ?, ?)
            """,
            (uid, firma_id, rid),
        )
        conn.commit()
        conn.close()

    def get_user_role(self, uid: int) -> Optional[int]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT rola_id FROM pracownik_firma WHERE pracownik_id = ?",
            (uid,),
        )
        row = cur.fetchone()
        conn.close()
        return row["rola_id"] if row else None

    # -------------------------
    # App settings (app_settings)
    # -------------------------
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return dao_get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        dao_set_setting(key, value)

    def delete_setting(self, key: str) -> None:
        dao_delete_setting(key)

    def list_settings(self) -> Dict[str, str]:
        return dao_list_settings()
    # -------------------------
    # Konta email i przypisania
    # -------------------------
    def get_email_accounts(self) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id,
                pracownik_id,
                email,
                serwer_imap, port_imap, ssl_imap,
                serwer_smtp, port_smtp, ssl_smtp,
                haslo_enc, aktywne
            FROM konta_email
            ORDER BY id
            """
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_email_account(self, acc_id: int) -> Optional[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM konta_email WHERE id = ?", (acc_id,))
        row = cur.fetchone()
        conn.close()
        return row

    def add_email_account(
        self,
        email: str,
        imap_server: str,
        smtp_server: str,
        password: str,
        imap_port: int = 993,
        smtp_port: int = 465,
        imap_ssl: str = "SSL/TLS",
        smtp_ssl: str = "SSL/TLS",
    ) -> int:
        from app.utils.crypto import encrypt_password

        encrypted_pwd = encrypt_password(password)
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO konta_email (
                email, haslo_enc,
                serwer_imap, port_imap, ssl_imap,
                serwer_smtp, port_smtp, ssl_smtp,
                aktywne
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                email,
                encrypted_pwd,
                imap_server,
                imap_port,
                imap_ssl,
                smtp_server,
                smtp_port,
                smtp_ssl,
            ),
        )
        konto_id = cur.lastrowid
        conn.commit()
        conn.close()
        return konto_id

    def update_email_account(
        self,
        acc_id: int,
        email: str,
        imap: str,
        imap_port: int,
        smtp: str,
        smtp_port: int,
        ssl_imap: str,
        ssl_smtp: str,
    ):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE konta_email
            SET email=?, serwer_imap=?, port_imap=?, serwer_smtp=?, port_smtp=?, ssl_imap=?, ssl_smtp=?
            WHERE id=?
            """,
            (email, imap, imap_port, smtp, smtp_port, ssl_imap, ssl_smtp, acc_id),
        )
        conn.commit()
        conn.close()

    def toggle_email_account_active(self, acc_id: int):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT aktywne FROM konta_email WHERE id=?", (acc_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return
        new_val = 0 if row["aktywne"] else 1
        cur.execute(
            "UPDATE konta_email SET aktywne=? WHERE id=?",
            (new_val, acc_id),
        )
        conn.commit()
        conn.close()

    def delete_email_account(self, konto_id: int):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM konta_email WHERE id = ?", (konto_id,))
        cur.execute(
            "DELETE FROM pracownik_konto_email WHERE konto_id = ?",
            (konto_id,),
        )
        conn.commit()
        conn.close()

    def get_email_assignments(self) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pke.konto_id, ke.email, pke.pracownik_id, pr.email AS pracownik_email
            FROM pracownik_konto_email pke
            JOIN konta_email ke ON pke.konto_id = ke.id
            JOIN pracownicy pr ON pke.pracownik_id = pr.id
            ORDER BY ke.email, pr.email
            """
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def assign_email_to_employee(self, konto_id: int, pracownik_id: int):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO pracownik_konto_email (konto_id, pracownik_id)
            VALUES (?, ?)
            """,
            (konto_id, pracownik_id),
        )
        conn.commit()
        conn.close()

    def remove_email_assignment(self, konto_id: int, pracownik_id: int):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM pracownik_konto_email
            WHERE konto_id = ? AND pracownik_id = ?
            """,
            (konto_id, pracownik_id),
        )
        conn.commit()
        conn.close()

    # -------------------------
    # Cennik księgowość (globalny)
    # -------------------------
    def get_global_pricing(self) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, klucz, cena_netto_ksiegowosc, cena_za_dokument_ponad_limit,
                   zdrowotna, netto, brutto, updated_at
            FROM cenniki
            ORDER BY klucz
            """
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def import_global_pricing_from_csv(self, csv_path: str):
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(csv_path)

        def _parse_limit_from_key(key: str) -> Optional[int]:
            try:
                parts = re.split(r"[_\-]", key)
                for p in reversed(parts):
                    if p.isdigit():
                        return int(p)
                return None
            except Exception:
                return None

        if path.suffix.lower() == ".csv":
            conn = self._get_conn()
            cur = conn.cursor()
            with path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    klucz = (row.get("Klucz") or row.get("klucz") or "").strip()
                    if not klucz:
                        continue
                    cena_netto_ksiegowosc = safe_float(
                        row.get("cena netto 2024") or row.get("cena_netto")
                    )
                    cena_za_dokument = safe_float(
                        row.get("cena za dokument") or row.get("ponad_limit")
                    )
                    zdrowotna = safe_float(row.get("zdrowotna"))
                    netto = cena_netto_ksiegowosc + zdrowotna
                    brutto = round(netto * 1.23, 2)
                    cur.execute(
                        """
                        INSERT INTO cenniki (
                            klucz,
                            cena_netto_ksiegowosc,
                            cena_za_dokument_ponad_limit,
                            zdrowotna,
                            netto,
                            brutto
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(klucz) DO UPDATE SET
                            cena_netto_ksiegowosc = excluded.cena_netto_ksiegowosc,
                            cena_za_dokument_ponad_limit = excluded.cena_za_dokument_ponad_limit,
                            zdrowotna = excluded.zdrowotna,
                            netto = excluded.netto,
                            brutto = excluded.brutto,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            klucz,
                            cena_netto_ksiegowosc,
                            cena_za_dokument,
                            zdrowotna,
                            netto,
                            brutto,
                        ),
                    )
            conn.commit()
            conn.close()
            return

        if path.suffix.lower() in (".xlsx", ".xls"):
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
            header = [
                str(h).strip().lower()
                for h in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
            ]
            idx = {h: i for i, h in enumerate(header)}
            conn = self._get_conn()
            cur = conn.cursor()
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not any(row):
                    continue

                def col(*names):
                    for n in names:
                        if n in idx:
                            return idx[n]
                    return None

                i_klucz = col("klucz")
                if i_klucz is None:
                    continue
                klucz = str(row[i_klucz]).strip()
                cena_netto_ksiegowosc = safe_float(
                    row[col("cena netto 2024", "cena_netto")]
                )
                cena_za_dokument = safe_float(
                    row[col("cena za dokument", "ponad_limit")]
                )
                zdrowotna = safe_float(row[col("zdrowotna")])
                netto = cena_netto_ksiegowosc + zdrowotna
                brutto = round(netto * 1.23, 2)
                cur.execute(
                    """
                    INSERT INTO cenniki (
                        klucz,
                        cena_netto_ksiegowosc,
                        cena_za_dokument_ponad_limit,
                        zdrowotna,
                        netto,
                        brutto
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(klucz) DO UPDATE SET
                        cena_netto_ksiegowosc = excluded.cena_netto_ksiegowosc,
                        cena_za_dokument_ponad_limit = excluded.cena_za_dokument_ponad_limit,
                        zdrowotna = excluded.zdrowotna,
                        netto = excluded.netto,
                        brutto = excluded.brutto,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        klucz,
                        cena_netto_ksiegowosc,
                        cena_za_dokument,
                        zdrowotna,
                        netto,
                        brutto,
                    ),
                )
            conn.commit()
            conn.close()
            return

        raise ValueError("Obsługiwane formaty: CSV, XLSX, XLS")
    # ============================
    # CENNIK KADRY
    # ============================
    def get_pricing_kadry(self) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, klucz, cena_netto_kadry, cena_brutto_kadry, opis, updated_at
            FROM cenniki_kadry
            ORDER BY klucz
            """
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def import_pricing_kadry_from_csv(self, csv_path: str):
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(csv_path)

        if path.suffix.lower() == ".csv":
            conn = self._get_conn()
            cur = conn.cursor()
            with path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    klucz = (row.get("Klucz") or row.get("klucz") or "").strip()
                    if not klucz:
                        continue
                    cena_netto_kadry = safe_float(
                        row.get("cena netto kadry") or row.get("cena_netto_kadry")
                    )
                    netto = safe_float(row.get("Netto") or row.get("netto"))
                    brutto = safe_float(row.get("Brutto") or row.get("brutto"))
                    cur.execute(
                        """
                        INSERT INTO cenniki_kadry (klucz, cena_netto_kadry, cena_brutto_kadry, opis)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(klucz) DO UPDATE SET
                            cena_netto_kadry = excluded.cena_netto_kadry,
                            cena_brutto_kadry = excluded.cena_brutto_kadry,
                            opis = excluded.opis,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            klucz,
                            cena_netto_kadry,
                            brutto,
                            row.get("Opis") or row.get("opis") or "",
                        ),
                    )
            conn.commit()
            conn.close()
            return

        if path.suffix.lower() in (".xlsx", ".xls"):
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
            header = [
                str(h).strip().lower()
                for h in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
            ]
            idx = {h: i for i, h in enumerate(header)}
            conn = self._get_conn()
            cur = conn.cursor()
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not any(row):
                    continue
                klucz = str(row[idx["klucz"]]).strip()
                cena_netto_kadry = safe_float(
                    row[idx.get("cena netto kadry", idx.get("cena_netto_kadry"))]
                )
                brutto = safe_float(
                    row[idx.get("brutto", idx.get("cena_brutto_kadry"))]
                )
                opis = ""
                if "opis" in idx:
                    opis = str(row[idx["opis"]] or "")
                cur.execute(
                    """
                    INSERT INTO cenniki_kadry (klucz, cena_netto_kadry, cena_brutto_kadry, opis)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(klucz) DO UPDATE SET
                        cena_netto_kadry = excluded.cena_netto_kadry,
                        cena_brutto_kadry = excluded.cena_brutto_kadry,
                        opis = excluded.opis,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (klucz, cena_netto_kadry, brutto, opis),
                )
            conn.commit()
            conn.close()
            return

        raise ValueError("Obsługiwane formaty: CSV, XLSX, XLS")

    # ============================
    # USŁUGI DODATKOWE
    # ============================
    def get_extra_services(self) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, nazwa, cena_netto, stawka_vat, COALESCE(kategoria, '') AS kategoria
            FROM uslugi_dodatkowe
            ORDER BY nazwa
            """
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_extra_service(
        self,
        nazwa: str,
        cena_netto: float,
        stawka_vat: str,
        kategoria: str,
    ):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat, kategoria)
            VALUES (?, ?, ?, ?)
            """,
            (nazwa, cena_netto, stawka_vat, kategoria),
        )
        conn.commit()
        conn.close()

    def delete_extra_service(self, usluga_id: int):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM uslugi_dodatkowe WHERE id = ?", (usluga_id,))
        conn.commit()
        conn.close()

    # -------------------------
    # Firmy
    # -------------------------
    def get_all_companies(self) -> List[sqlite3.Row]:
        return list_companies()

    def get_companies(self) -> List[sqlite3.Row]:
        return self.get_all_companies()

    def get_company(self, firma_id: int) -> Optional[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM firmy WHERE id = ?", (firma_id,))
        row = cur.fetchone()
        conn.close()
        return row

    def update_company(
        self,
        fid: int,
        nazwa: str,
        nip: str,
        regon: str,
        miasto: str,
        ulica: str,
        kod: str,
        vat_czynny: int,
        fakturowac: int,
        forma: str,
        opodatkowanie: str,
        limit: int,
        zdrowotna: float,
        cennik: str,
    ):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE firmy
            SET nazwa=?, nip=?, regon=?, miasto=?, ulica=?, kod_pocztowy=?,
                vat_czynny=?, fakturowac=?, forma_dzialalnosci=?,
                forma_opodatkowania=?, limit_dokumentow=?, zdrowotna=?,
                glowny_cennik=?
            WHERE id=?
            """,
            (
                nazwa,
                nip,
                regon,
                miasto,
                ulica,
                kod,
                vat_czynny,
                fakturowac,
                forma,
                opodatkowanie,
                limit,
                zdrowotna,
                cennik,
                fid,
            ),
        )
        conn.commit()
        conn.close()

    def delete_company(self, firma_id: int):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM firmy WHERE id = ?", (firma_id,))
        conn.commit()
        conn.close()

    # -------------------------
    # Faktury (CRUD + pozycje)
    # -------------------------
    def list_invoices_for_company(self, firma_id: int) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, firma_id, miesiac, kwota_netto, kwota_brutto, data_wystawienia, eksportowana
            FROM faktury
            WHERE firma_id = ?
            ORDER BY data_wystawienia DESC, id DESC
            """,
            (firma_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_invoice(self, invoice_id: int) -> Optional[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM faktury WHERE id = ?", (invoice_id,))
        row = cur.fetchone()
        conn.close()
        return row

    def create_invoice(
        self,
        firma_id: int,
        miesiac: str,
        kwota_netto: float,
        kwota_brutto: float,
        data_wystawienia: Optional[str] = None,
        data_sprzedazy: Optional[str] = None,
        termin_platnosci: Optional[str] = None,
        nabywca_pelna: Optional[str] = None,
        nabywca_nip: Optional[str] = None,
        pozycje: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        pozycje_json = json.dumps(pozycje or [])
        cur.execute(
            """
            INSERT INTO faktury (
                firma_id, miesiac, kwota_netto, kwota_brutto,
                data_wystawienia, data_sprzedazy, termin_platnosci,
                nabywca_pelna, nabywca_nip, pozycje_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                firma_id,
                miesiac,
                kwota_netto,
                kwota_brutto,
                data_wystawienia,
                data_sprzedazy,
                termin_platnosci,
                nabywca_pelna,
                nabywca_nip,
                pozycje_json,
            ),
        )
        fid = cur.lastrowid
        conn.commit()
        conn.close()
        return fid

    def update_invoice(self, invoice_id: int, **fields) -> None:
        allowed = {
            "kwota_netto",
            "kwota_brutto",
            "data_wystawienia",
            "data_sprzedazy",
            "termin_platnosci",
            "nabywca_pelna",
            "nabywca_nip",
            "pozycje_json",
            "nabywca_skrocona",
            "odbiorca_pelna",
            "odbiorca_nip",
            "wystawca",
            "konto_bankowe",
            "gtu",
            "eksportowana",
        }
        updates = []
        params = []
        for k, v in fields.items():
            if k in allowed:
                updates.append(f"{k} = ?")
                params.append(v)
        if not updates:
            return
        params.append(invoice_id)
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE faktury SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            params,
        )
        conn.commit()
        conn.close()

    def delete_invoice(self, invoice_id: int) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM faktury WHERE id = ?", (invoice_id,))
        conn.commit()
        conn.close()

    def list_invoice_positions(self, invoice_id: int) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM faktury_pozycje WHERE faktura_id = ? ORDER BY id",
            (invoice_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_invoice_position(
        self,
        invoice_id: int,
        opis: str,
        ilosc: float,
        jednostka: str,
        cena_netto: float,
        cena_brutto: float,
        stawka_vat: str = "",
        pkwiu: str = "",
        gtu: str = "",
    ) -> int:
        wartosc_netto = round(ilosc * cena_netto, 2)
        wartosc_brutto = round(ilosc * cena_brutto, 2)
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO faktury_pozycje (
                faktura_id, opis, ilosc, jednostka, cena_netto, cena_brutto,
                stawka_vat, pkwiu, gtu, wartosc_netto, wartosc_brutto, generated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                invoice_id,
                opis,
                ilosc,
                jednostka,
                cena_netto,
                cena_brutto,
                stawka_vat,
                pkwiu,
                gtu,
                wartosc_netto,
                wartosc_brutto,
            ),
        )
        pid = cur.lastrowid
        conn.commit()
        conn.close()
        return pid

    def update_invoice_position(self, position_id: int, **fields) -> None:
        allowed = {
            "opis",
            "ilosc",
            "jednostka",
            "cena_netto",
            "cena_brutto",
            "stawka_vat",
            "pkwiu",
            "gtu",
        }
        updates = []
        params = []
        for k, v in fields.items():
            if k in allowed:
                updates.append(f"{k} = ?")
                params.append(v)
        if not updates:
            return
        params.append(position_id)
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE faktury_pozycje SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        conn.close()

    def delete_invoice_position(self, position_id: int) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM faktury_pozycje WHERE id = ?", (position_id,))
        conn.commit()
        conn.close()

    # -------------------------
    # Tags i mapowanie tagów
    # -------------------------
    def list_tags(self) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa FROM tags ORDER BY nazwa")
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_tag(self, nazwa: str) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO tags (nazwa) VALUES (?)", (nazwa,))
        conn.commit()
        cur.execute("SELECT id FROM tags WHERE nazwa = ?", (nazwa,))
        row = cur.fetchone()
        conn.close()
        return row["id"] if row else 0

    def map_tag(self, tag_id: int, tabela: str, rekord_id: int) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tag_map (tag_id, tabela, rekord_id) VALUES (?, ?, ?)",
            (tag_id, tabela, rekord_id),
        )
        tm_id = cur.lastrowid
        conn.commit()
        conn.close()
        return tm_id

    def unmap_tag(self, tag_map_id: int) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM tag_map WHERE id = ?", (tag_map_id,))
        conn.commit()
        conn.close()

    # -------------------------
    # Załączniki do dokumentów i emaili
    # -------------------------
    def add_document_attachment(
        self,
        dokument_id: int,
        filename: str,
        path_str: str,
        mime: str,
    ) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO dokumenty_attachments (dokument_id, filename, path, mime)
            VALUES (?, ?, ?, ?)
            """,
            (dokument_id, filename, path_str, mime),
        )
        aid = cur.lastrowid
        conn.commit()
        conn.close()
        return aid

    def list_document_attachments(
        self,
        dokument_id: int,
    ) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, filename, path, mime, data_dodania
            FROM dokumenty_attachments
            WHERE dokument_id = ?
            ORDER BY id
            """,
            (dokument_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def delete_document_attachment(self, attachment_id: int) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM dokumenty_attachments WHERE id = ?",
            (attachment_id,),
        )
        conn.commit()
        conn.close()

    # -------------------------
    # Logi / narzędzia
    # -------------------------
    def get_logs(self, limit: int = 500) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM system_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_log(self, poziom: str, modul: str, wiadomosc: str) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_logs (poziom, modul, wiadomosc, data)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (poziom, modul, wiadomosc),
        )
        conn.commit()
        conn.close()

    def clear_logs(self):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM system_logs")
        conn.commit()
        conn.close()

    def backup_database(self, target_path: str):
        try:
            conn = self._get_conn()
            dest = sqlite3.connect(target_path)
            conn.backup(dest)
            dest.close()
            conn.close()
        except Exception:
            src = Path(get_conn().__dict__.get("database", "") or "")
            if src and src.exists():
                import shutil

                shutil.copy(src, Path(target_path))
            else:
                raise

    def cleanup_old_emails(self, older_than_days: int):
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
        cutoff_str = cutoff.isoformat(sep=" ", timespec="seconds")
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM wiadomosci_email WHERE data < ?",
            (cutoff_str,),
        )
        conn.commit()
        conn.close()

    # -------------------------
    # Narzędzia pomocnicze / migracje
    # -------------------------
    def ensure_role_columns(self):
        conn = self._get_conn()
        cur = conn.cursor()
        cols = {
            "can_view_invoices": "INTEGER DEFAULT 0",
            "can_edit_companies": "INTEGER DEFAULT 0",
            "can_manage_users": "INTEGER DEFAULT 0",
            "can_view_finances": "INTEGER DEFAULT 0",
            "can_export_data": "INTEGER DEFAULT 0",
            "can_access_admin_settings": "INTEGER DEFAULT 0",
        }
        cur.execute("PRAGMA table_info(role)")
        existing = [r["name"] for r in cur.fetchall()]
        for col, spec in cols.items():
            if col not in existing:
                cur.execute(f"ALTER TABLE role ADD COLUMN {col} {spec}")
        conn.commit()
        conn.close()

    def ensure_schema_minimum(self):
        try:
            self.ensure_role_columns()
        except Exception:
            pass
