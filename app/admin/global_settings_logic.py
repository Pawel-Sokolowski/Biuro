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
import psycopg

from app.db.dao_conn import get_conn, Row
from app.utils.importers import build_pricing_key
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
from app.db import dao_global_settings
import logging

logger = logging.getLogger(__name__)


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
            logger.warning("Zignorowany błąd", exc_info=True)

    def _get_conn(self) -> psycopg.Connection:
        return get_conn()

    # -------------------------
    # Użytkownicy (delegacja do DAO)
    # -------------------------
    def get_users(self) -> List[Row]:
        return list_employees()

    def get_user(self, pracownik_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()

        try:
            row = conn.execute(
                """
                SELECT
                    id,
                    email,
                    imie,
                    nazwisko,
                    aktywny
                FROM pracownicy
                WHERE id = %s
                """,
                (pracownik_id,),
            ).fetchone()

            return dict(row) if row else None

        finally:
            conn.close()

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
        cur.execute("SELECT aktywny FROM pracownicy WHERE id = %s", (pracownik_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return
        new_val = 0 if row["aktywny"] else 1
        cur.execute(
            "UPDATE pracownicy SET aktywny = %s WHERE id = %s",
            (new_val, pracownik_id),
        )
        conn.commit()
        conn.close()

    def reset_user_password(self, pracownik_id: int, new_password: str):
        change_employee_password(pracownik_id, new_password)

    def reset_password(self, uid: int, new_pass: str):
        self.reset_user_password(uid, new_pass)

    def get_companies_for_user(
            self,
            pracownik_id: int,
    ):
        conn = self._get_conn()

        try:
            rows = conn.execute(
                """
                SELECT firma_id
                FROM pracownik_firma
                WHERE pracownik_id = %s
                """,
                (pracownik_id,),
            ).fetchall()
            return [
                int(r["firma_id"])
                for r in rows
            ]
        finally:
            conn.close()

    def save_companies_for_user(
            self,
            pracownik_id: int,
            company_ids: list[int],
    ):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            wanted = {int(c) for c in company_ids}
            existing = {r[0] for r in cur.execute(
                "SELECT firma_id FROM pracownik_firma WHERE pracownik_id = %s", (pracownik_id,))}
            for firma_id in existing - wanted:
                cur.execute(
                    "DELETE FROM pracownik_firma WHERE pracownik_id = %s AND firma_id = %s",
                    (pracownik_id, firma_id),
                )
            for firma_id in wanted - existing:
                cur.execute(
                    """
                    INSERT INTO pracownik_firma (
                        pracownik_id,
                        firma_id
                    )
                    VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
                    (
                        pracownik_id,
                        firma_id,),
                )
            conn.commit()
        finally:
            conn.close()

    # -------------------------
    # Role + uprawnienia
    # -------------------------
    def get_roles(self) -> List[Row]:
        return dao_global_settings.get_roles()

    def get_role(self, rid: int) -> Optional[Row]:
        return dao_global_settings.get_role(rid)

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
        return dao_global_settings.add_role(
            nazwa=nazwa,
            can_view_invoices=can_view_invoices,
            can_edit_companies=can_edit_companies,
            can_manage_users=can_manage_users,
            can_view_finances=can_view_finances,
            can_export_data=can_export_data,
            can_access_admin_settings=can_access_admin_settings,
        )

    def update_role(self, rid: int, nazwa: str, **perms):
        dao_global_settings.update_role(rid, nazwa, **perms)

    def delete_role(self, rola_id: int):
        dao_global_settings.delete_role(rola_id)

    def get_user_permissions(self, pracownik_id: int) -> Dict[str, bool]:
        row = dao_global_settings.get_user_permissions(pracownik_id)
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
        dao_global_settings.assign_role(uid, rid, firma_id)

    def get_user_role(self, uid: int) -> Optional[int]:
        return dao_global_settings.get_user_role(uid)

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
    def get_email_accounts(self) -> List[Row]:
        return dao_global_settings.get_email_accounts()

    def get_email_account(self, acc_id: int) -> Optional[Row]:
        return dao_global_settings.get_email_account(acc_id)

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
        return dao_global_settings.add_email_account(
            email=email,
            imap_server=imap_server,
            smtp_server=smtp_server,
            haslo_enc=encrypted_pwd,
            imap_port=imap_port,
            smtp_port=smtp_port,
            ssl_imap=imap_ssl,
            ssl_smtp=smtp_ssl,
            pracownik_id=None,
        )

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
        dao_global_settings.update_email_account_connection(
            acc_id=acc_id,
            email=email,
            imap=imap,
            imap_port=imap_port,
            smtp=smtp,
            smtp_port=smtp_port,
            ssl_imap=ssl_imap,
            ssl_smtp=ssl_smtp,
        )

    def update_email_password(self, acc_id: int, password: str):
        from app.utils.crypto import encrypt_password

        dao_global_settings.update_email_account_password(
            acc_id, encrypt_password(password)
        )

    def get_account_employee_ids(self, konto_id: int) -> List[int]:
        return dao_global_settings.get_account_employee_ids(konto_id)

    def set_account_employees(self, konto_id: int, employee_ids: List[int]):
        dao_global_settings.set_account_employees(konto_id, employee_ids)

    def list_employees(self):
        from app.db.dao_employees import list_employees

        return list_employees()

    def toggle_email_account_active(self, acc_id: int):
        dao_global_settings.toggle_email_account_active(acc_id)

    def delete_email_account(self, konto_id: int):
        dao_global_settings.delete_email_account(konto_id)

    def get_email_assignments(self) -> List[Row]:
        return dao_global_settings.get_email_assignments()

    def assign_email_to_employee(self, konto_id: int, pracownik_id: int):
        dao_global_settings.assign_email_to_employee(konto_id, pracownik_id)

    def remove_email_assignment(self, konto_id: int, pracownik_id: int):
        dao_global_settings.remove_email_assignment(konto_id, pracownik_id)

    # -------------------------
    # Cennik księgowość (globalny)
    # -------------------------
    def get_global_pricing(self) -> List[Row]:
        return dao_global_settings.get_global_pricing()

    def update_global_pricing_item(
        self,
        klucz: str,
        cena_netto_ksiegowosc: float,
        cena_za_dokument_ponad_limit: float,
        zdrowotna: float,
        netto: float,
        brutto: float,
    ) -> None:
        dao_global_settings.update_global_pricing_item(
            klucz=klucz,
            cena_netto_ksiegowosc=cena_netto_ksiegowosc,
            cena_za_dokument_ponad_limit=cena_za_dokument_ponad_limit,
            zdrowotna=zdrowotna,
            netto=netto,
            brutto=brutto,
        )

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
                    klucz = build_pricing_key(klucz, cena_netto_ksiegowosc)
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
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT(klucz) DO UPDATE SET
                            cena_netto_ksiegowosc = excluded.cena_netto_ksiegowosc,
                            cena_za_dokument_ponad_limit = excluded.cena_za_dokument_ponad_limit,
                            zdrowotna = excluded.zdrowotna,
                            netto = excluded.netto,
                            brutto = excluded.brutto,
                            updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
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
                klucz = build_pricing_key(klucz, cena_netto_ksiegowosc)
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
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT(klucz) DO UPDATE SET
                        cena_netto_ksiegowosc = excluded.cena_netto_ksiegowosc,
                        cena_za_dokument_ponad_limit = excluded.cena_za_dokument_ponad_limit,
                        zdrowotna = excluded.zdrowotna,
                        netto = excluded.netto,
                        brutto = excluded.brutto,
                        updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
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
    def get_pricing_kadry(self) -> List[Row]:
        return dao_global_settings.get_pricing_kadry()

    def update_pricing_kadry_item(
        self,
        klucz: str,
        cena_netto_kadry: float,
        cena_brutto_kadry: float,
        opis: str,
    ) -> None:
        dao_global_settings.update_pricing_kadry_item(
            klucz=klucz,
            cena_netto_kadry=cena_netto_kadry,
            cena_brutto_kadry=cena_brutto_kadry,
            opis=opis,
        )

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
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT(klucz) DO UPDATE SET
                            cena_netto_kadry = excluded.cena_netto_kadry,
                            cena_brutto_kadry = excluded.cena_brutto_kadry,
                            opis = excluded.opis,
                            updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
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
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT(klucz) DO UPDATE SET
                        cena_netto_kadry = excluded.cena_netto_kadry,
                        cena_brutto_kadry = excluded.cena_brutto_kadry,
                        opis = excluded.opis,
                        updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
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
    def get_extra_services(self) -> List[Row]:
        return dao_global_settings.get_extra_services()

    def add_extra_service(
        self,
        nazwa: str,
        cena_netto: float,
        stawka_vat: str,
        kategoria: str,
    ):
        dao_global_settings.add_extra_service(
            nazwa=nazwa,
            cena_netto=cena_netto,
            stawka_vat=stawka_vat,
            kategoria=kategoria,
        )

    def delete_extra_service(self, usluga_id: int):
        dao_global_settings.delete_extra_service(usluga_id)

    def update_extra_service_by_name(
        self,
        old_name: str,
        nazwa: str,
        cena_netto: float,
        stawka_vat: str,
        kategoria: str,
    ) -> None:
        dao_global_settings.update_extra_service_by_name(
            old_name=old_name,
            nazwa=nazwa,
            cena_netto=cena_netto,
            stawka_vat=stawka_vat,
            kategoria=kategoria,
        )

    # -------------------------
    # Firmy
    # -------------------------
    def get_all_companies(self) -> List[Row]:
        return list_companies()

    def get_companies(self) -> List[Row]:
        return self.get_all_companies()

    def get_company(self, firma_id: int) -> Optional[Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM firmy WHERE id = %s", (firma_id,))
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
            SET nazwa=%s, nip=%s, regon=%s, miasto=%s, ulica=%s, kod_pocztowy=%s,
                vat_czynny=%s, fakturowac=%s, forma_dzialalnosci=%s,
                forma_opodatkowania=%s, limit_dokumentow=%s, zdrowotna=%s,
                glowny_cennik=%s
            WHERE id=%s
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
        cur.execute("DELETE FROM firmy WHERE id = %s", (firma_id,))
        conn.commit()
        conn.close()

    # -------------------------
    # Faktury (CRUD + pozycje)
    # -------------------------
    def list_invoices_for_company(self, firma_id: int) -> List[Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, firma_id, miesiac, kwota_netto, kwota_brutto, data_wystawienia, eksportowana
            FROM faktury
            WHERE firma_id = %s
            ORDER BY data_wystawienia DESC, id DESC
            """,
            (firma_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_invoice(self, invoice_id: int) -> Optional[Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM faktury WHERE id = %s", (invoice_id,))
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
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, to_char(now(), 'YYYY-MM-DD HH24:MI:SS'), to_char(now(), 'YYYY-MM-DD HH24:MI:SS')) RETURNING id
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
        _new_id = cur.fetchone()[0]
        fid = _new_id
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
                updates.append(f"{k} = %s")
                params.append(v)
        if not updates:
            return
        params.append(invoice_id)
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE faktury SET {', '.join(updates)}, updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = %s",
            params,
        )
        conn.commit()
        conn.close()

    def delete_invoice(self, invoice_id: int) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM faktury WHERE id = %s", (invoice_id,))
        conn.commit()
        conn.close()

    def list_invoice_positions(self, invoice_id: int) -> List[Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM faktury_pozycje WHERE faktura_id = %s ORDER BY id",
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
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0) RETURNING id
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
        _new_id = cur.fetchone()[0]
        pid = _new_id
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
                updates.append(f"{k} = %s")
                params.append(v)
        if not updates:
            return
        params.append(position_id)
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE faktury_pozycje SET {', '.join(updates)} WHERE id = %s",
            params,
        )
        conn.commit()
        conn.close()

    def delete_invoice_position(self, position_id: int) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM faktury_pozycje WHERE id = %s", (position_id,))
        conn.commit()
        conn.close()

    # -------------------------
    # Tags i mapowanie tagów
    # -------------------------
    def list_tags(self) -> List[Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa FROM tags ORDER BY nazwa")
        rows = cur.fetchall()
        conn.close()
        return rows

    def add_tag(self, nazwa: str) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO tags (nazwa) VALUES (%s) ON CONFLICT DO NOTHING", (nazwa,))
        conn.commit()
        cur.execute("SELECT id FROM tags WHERE nazwa = %s", (nazwa,))
        row = cur.fetchone()
        conn.close()
        return row["id"] if row else 0

    def map_tag(self, tag_id: int, tabela: str, rekord_id: int) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tag_map (tag_id, tabela, rekord_id) VALUES (%s, %s, %s) RETURNING id",
            (tag_id, tabela, rekord_id),
        )
        _new_id = cur.fetchone()[0]
        tm_id = _new_id
        conn.commit()
        conn.close()
        return tm_id

    def unmap_tag(self, tag_map_id: int) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM tag_map WHERE id = %s", (tag_map_id,))
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
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (dokument_id, filename, path_str, mime),
        )
        _new_id = cur.fetchone()[0]
        aid = _new_id
        conn.commit()
        conn.close()
        return aid

    def list_document_attachments(
        self,
        dokument_id: int,
    ) -> List[Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, filename, path, mime, data_dodania
            FROM dokumenty_attachments
            WHERE dokument_id = %s
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
            "DELETE FROM dokumenty_attachments WHERE id = %s",
            (attachment_id,),
        )
        conn.commit()
        conn.close()

    # -------------------------
    # Logi / narzędzia
    # -------------------------
    def get_logs(self, limit: int = 500) -> List[Row]:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM system_logs ORDER BY id DESC LIMIT %s",
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
            VALUES (%s, %s, %s, to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
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
        from app.utils.pg_backup import dump_database

        dump_database(target_path)

    def cleanup_old_emails(self, older_than_days: int):
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
        cutoff_str = cutoff.isoformat(sep=" ", timespec="seconds")
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM wiadomosci_email WHERE data < %s",
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
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'role'"
        )
        existing = [r[0] for r in cur.fetchall()]
        for col, spec in cols.items():
            if col not in existing:
                cur.execute(f"ALTER TABLE role ADD COLUMN IF NOT EXISTS {col} {spec}")
        conn.commit()
        conn.close()

    def ensure_schema_minimum(self):
        try:
            self.ensure_role_columns()
        except Exception:
            logger.warning("Zignorowany błąd", exc_info=True)