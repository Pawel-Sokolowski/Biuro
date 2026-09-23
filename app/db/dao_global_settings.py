from __future__ import annotations

import sqlite3
from typing import Optional, List

from app.db.dao_conn import get_conn

def get_roles() -> List[sqlite3.Row]:
    conn = get_conn()
    try:
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
        return cur.fetchall()
    finally:
        conn.close()


def get_role(rid: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    try:
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
        return cur.fetchone()
    finally:
        conn.close()


def add_role(
    nazwa: str,
    can_view_invoices: bool = False,
    can_edit_companies: bool = False,
    can_manage_users: bool = False,
    can_view_finances: bool = False,
    can_export_data: bool = False,
    can_access_admin_settings: bool = False,
) -> int:
    conn = get_conn()
    try:
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
        return rid
    finally:
        conn.close()


def update_role(rid: int, nazwa: str, **perms) -> None:
    conn = get_conn()
    try:
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
    finally:
        conn.close()


def delete_role(rola_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM role WHERE id = ?", (rola_id,))
        conn.commit()
    finally:
        conn.close()


def get_user_permissions(pracownik_id: int) -> sqlite3.Row:
    conn = get_conn()
    try:
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
        return cur.fetchone()
    finally:
        conn.close()


def assign_role(uid: int, rid: int, firma_id: int = 1) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO pracownik_firma (pracownik_id, firma_id, rola_id)
            VALUES (?, ?, ?)
            """,
            (uid, firma_id, rid),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_role(uid: int) -> Optional[int]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT rola_id FROM pracownik_firma WHERE pracownik_id = ?",
            (uid,),
        )
        row = cur.fetchone()
        return row["rola_id"] if row else None
    finally:
        conn.close()


# -------------------------
# Konta email i przypisania
# -------------------------
def get_email_accounts() -> List[sqlite3.Row]:
    conn = get_conn()
    try:
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
        return cur.fetchall()
    finally:
        conn.close()


def get_email_account(acc_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM konta_email WHERE id = ?", (acc_id,))
        return cur.fetchone()
    finally:
        conn.close()


def add_email_account(
    email: str,
    imap_server: str,
    smtp_server: str,
    haslo_enc: str,
    imap_port: int = 993,
    smtp_port: int = 465,
    ssl_imap: str = "SSL/TLS",
    ssl_smtp: str = "SSL/TLS",
    pracownik_id: Optional[int] = None,
) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO konta_email (
                pracownik_id, email,
                serwer_imap, port_imap, ssl_imap,
                serwer_smtp, port_smtp, ssl_smtp,
                haslo_enc, aktywne
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                pracownik_id,
                email,
                imap_server,
                int(imap_port),
                ssl_imap,
                smtp_server,
                int(smtp_port),
                ssl_smtp,
                haslo_enc,
            ),
        )
        new_id = cur.lastrowid
        conn.commit()
        return new_id
    finally:
        conn.close()


def update_email_account(
    acc_id: int,
    email: str,
    imap_server: str,
    smtp_server: str,
    haslo_enc: str,
) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE konta_email
            SET email = ?, serwer_imap = ?, serwer_smtp = ?, haslo_enc = ?
            WHERE id = ?
            """,
            (email, imap_server, smtp_server, haslo_enc, acc_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_email_account_connection(
    acc_id: int,
    email: str,
    imap: str,
    imap_port: int,
    smtp: str,
    smtp_port: int,
    ssl_imap: str,
    ssl_smtp: str,
) -> None:
    conn = get_conn()
    try:
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
    finally:
        conn.close()


def toggle_email_account_active(acc_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT aktywne FROM konta_email WHERE id=?", (acc_id,))
        row = cur.fetchone()
        if not row:
            return
        new_val = 0 if row["aktywne"] else 1
        cur.execute("UPDATE konta_email SET aktywne=? WHERE id=?", (new_val, acc_id))
        conn.commit()
    finally:
        conn.close()


def delete_email_account(konto_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM konta_email WHERE id = ?", (konto_id,))
        cur.execute("DELETE FROM pracownik_konto_email WHERE konto_id = ?", (konto_id,))
        conn.commit()
    finally:
        conn.close()


def get_email_assignments() -> List[sqlite3.Row]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pke.konto_id, ke.email, pke.pracownik_id, pr.email AS pracownik_email
            FROM pracownik_konto_email pke
            JOIN konta_email ke ON ke.id = pke.konto_id
            JOIN pracownicy pr ON pr.id = pke.pracownik_id
            ORDER BY ke.email, pr.email
            """
        )
        return cur.fetchall()
    finally:
        conn.close()


def assign_email_to_employee(konto_id: int, pracownik_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO pracownik_konto_email (konto_id, pracownik_id)
            VALUES (?, ?)
            """,
            (konto_id, pracownik_id),
        )
        conn.commit()
    finally:
        conn.close()


def remove_email_assignment(konto_id: int, pracownik_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM pracownik_konto_email
            WHERE konto_id = ? AND pracownik_id = ?
            """,
            (konto_id, pracownik_id),
        )
        conn.commit()
    finally:
        conn.close()


# -------------------------
# Cenniki i usługi
# -------------------------
def get_global_pricing() -> List[sqlite3.Row]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, klucz, cena_netto_ksiegowosc, cena_za_dokument_ponad_limit,
                   zdrowotna, netto, brutto, updated_at
            FROM cenniki
            ORDER BY klucz
            """
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_pricing_kadry() -> List[sqlite3.Row]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, klucz, cena_netto_kadry, cena_brutto_kadry, opis, updated_at
            FROM cenniki_kadry
            ORDER BY klucz
            """
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_extra_services() -> List[sqlite3.Row]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, nazwa, cena_netto, stawka_vat, COALESCE(kategoria, '') AS kategoria
            FROM uslugi_dodatkowe
            ORDER BY nazwa
            """
        )
        return cur.fetchall()
    finally:
        conn.close()


def update_global_pricing_item(
    klucz: str,
    cena_netto_ksiegowosc: float,
    cena_za_dokument_ponad_limit: float,
    zdrowotna: float,
    netto: float,
    brutto: float,
) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE cenniki
            SET cena_netto_ksiegowosc = ?,
                cena_za_dokument_ponad_limit = ?,
                zdrowotna = ?,
                netto = ?,
                brutto = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE klucz = ?
            """,
            (cena_netto_ksiegowosc, cena_za_dokument_ponad_limit, zdrowotna, netto, brutto, klucz),
        )
        conn.commit()
    finally:
        conn.close()


def update_pricing_kadry_item(
    klucz: str,
    cena_netto_kadry: float,
    cena_brutto_kadry: float,
    opis: str,
) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE cenniki_kadry
            SET cena_netto_kadry = ?,
                cena_brutto_kadry = ?,
                opis = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE klucz = ?
            """,
            (cena_netto_kadry, cena_brutto_kadry, opis, klucz),
        )
        conn.commit()
    finally:
        conn.close()


def add_extra_service(nazwa: str, cena_netto: float, stawka_vat: str, kategoria: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat, kategoria)
            VALUES (?, ?, ?, ?)
            """,
            (nazwa, cena_netto, stawka_vat, kategoria),
        )
        conn.commit()
    finally:
        conn.close()


def delete_extra_service(usluga_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM uslugi_dodatkowe WHERE id = ?", (usluga_id,))
        conn.commit()
    finally:
        conn.close()


def update_extra_service_by_name(
    old_name: str,
    nazwa: str,
    cena_netto: float,
    stawka_vat: str,
    kategoria: str,
) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE uslugi_dodatkowe
            SET nazwa = ?, cena_netto = ?, stawka_vat = ?, kategoria = ?
            WHERE nazwa = ?
            """,
            (nazwa, cena_netto, stawka_vat, kategoria, old_name),
        )
        conn.commit()
    finally:
        conn.close()
