# app/email/email_storage.py
import re
import logging
import sqlite3
from typing import Optional
from PyQt6.QtWidgets import QTableWidgetItem, QPushButton, QMessageBox
from app.db.dao_conn import get_conn
from app.utils.crypto import encrypt_password as default_encrypt_password
from email.message import Message
logger = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"[^@]+@[^@]+\.[^@]+")


def refresh_email_accounts(self):
    self.table_email_accounts.setRowCount(0)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, pracownik_id, email, serwer_imap, port_imap, ssl_imap,
                   serwer_smtp, port_smtp, ssl_smtp, haslo_enc, aktywne
            FROM konta_email
            WHERE pracownik_id = ?
            ORDER BY id ASC
        """, (self.pracownik_id,))
        rows = cur.fetchall()

        for row_idx, row in enumerate(rows):
            self.table_email_accounts.insertRow(row_idx)
            row_dict = dict(row) if not isinstance(row, dict) else row

            konto_id = row_dict.get("id", "")
            email_addr = row_dict.get("email", "")
            imap_server = row_dict.get("serwer_imap", "")
            aktywna = row_dict.get("aktywne", 0)

            self.table_email_accounts.setItem(row_idx, 0, QTableWidgetItem(str(konto_id)))
            self.table_email_accounts.setItem(row_idx, 1, QTableWidgetItem(email_addr or ""))
            self.table_email_accounts.setItem(row_idx, 2, QTableWidgetItem(imap_server or ""))
            self.table_email_accounts.setItem(row_idx, 3, QTableWidgetItem("Tak" if aktywna else "Nie"))

            btn_delete = QPushButton("Usuń")
            btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
            btn_delete.clicked.connect(lambda checked, kid=konto_id: self.delete_email_account(kid))
            self.table_email_accounts.setCellWidget(row_idx, 4, btn_delete)

        try:
            self._refresh_email_account_combos()
        except Exception:
            logger.exception("refresh_email_accounts: _refresh_email_account_combos failed")
        self.table_email_accounts.resizeColumnsToContents()
    finally:
        conn.close()


def refresh_email_filters(self):
    self.table_email_filters.setRowCount(0)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT rf.id, rf.od, rf.temat_zawiera, rf.folder_docelowy
            FROM reguly_filtrow rf
            JOIN konta_email ke ON rf.konto_id = ke.id
            WHERE ke.pracownik_id = ?
            ORDER BY rf.id
        """, (self.pracownik_id,))
        rows = cur.fetchall()

        for row_idx, row in enumerate(rows):
            self.table_email_filters.insertRow(row_idx)
            row_dict = dict(row) if not isinstance(row, dict) else row

            filter_id = row_dict.get("id", "")
            od = row_dict.get("od", None)
            temat = row_dict.get("temat_zawiera", None)
            folder = row_dict.get("folder_docelowy", None)

            self.table_email_filters.setItem(row_idx, 0, QTableWidgetItem(str(filter_id)))
            self.table_email_filters.setItem(row_idx, 1, QTableWidgetItem(od or "Dowolny"))
            self.table_email_filters.setItem(row_idx, 2, QTableWidgetItem(temat or "Dowolny"))
            self.table_email_filters.setItem(row_idx, 3, QTableWidgetItem(folder or "N/A"))

            btn_delete = QPushButton("Usuń")
            btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
            btn_delete.clicked.connect(lambda checked, fid=filter_id: self.delete_email_filter(fid))
            self.table_email_filters.setCellWidget(row_idx, 4, btn_delete)
    finally:
        conn.close()


def add_email_filter(self):
    konto_id = self.filter_konto_combo.currentData()
    od = self.filter_od.text().strip() or None
    temat = self.filter_temat.text().strip() or None
    folder = self.filter_folder.text().strip()

    if not konto_id or not folder:
        QMessageBox.warning(self, "Błąd", "Wybierz konto i folder docelowy")
        return

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reguly_filtrow (konto_id, od, temat_zawiera, folder_docelowy, aktywna)
            VALUES (?, ?, ?, ?, 1)
        """, (konto_id, od, temat, folder))
        conn.commit()
        QMessageBox.information(self, "Sukces", "Filtr został dodany")
        self.filter_od.clear()
        self.filter_temat.clear()
        self.filter_folder.clear()
        refresh_email_filters(self)
    except Exception as e:
        logger.exception("add_email_filter failed")
        QMessageBox.critical(self, "Błąd", f"Błąd dodawania filtru: {str(e)}")
    finally:
        conn.close()


def delete_email_filter(self, filter_id):
    reply = QMessageBox.question(
        self,
        "Potwierdzenie",
        "Usunąć ten filtr?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.Yes:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            cur.execute("DELETE FROM reguly_filtrow WHERE id = ?", (filter_id,))
            conn.commit()
            refresh_email_filters(self)
        except Exception as e:
            conn.rollback()
            logger.exception("delete_email_filter failed")
            QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć filtru: {e}")
        finally:
            conn.close()


def add_email_account(self, encrypt_password=None):
    email_addr = self.email_input.text().strip()
    imap_srv = self.imap_server.text().strip()
    try:
        imap_p = int(self.imap_port.value()) if hasattr(self.imap_port, "value") else int(self.imap_port.text().strip())
    except Exception:
        imap_p = 993
    smtp_srv = self.smtp_server.text().strip()
    try:
        smtp_p = int(self.smtp_port.value()) if hasattr(self.smtp_port, "value") else int(self.smtp_port.text().strip())
    except Exception:
        smtp_p = 465
    password = self.email_password.text()
    ssl_imap = 1 if getattr(self, "imap_ssl_checkbox", None) and self.imap_ssl_checkbox.isChecked() else 1
    ssl_smtp = 1 if getattr(self, "smtp_ssl_checkbox", None) and self.smtp_ssl_checkbox.isChecked() else 1
    aktywne = 1

    if not all([email_addr, imap_srv, smtp_srv, password]):
        QMessageBox.warning(self, "Błąd", "Uzupełnij wszystkie pola")
        return

    if not EMAIL_RE.match(email_addr):
        QMessageBox.warning(self, "Błąd", "Nieprawidłowy adres e-mail")
        return

    if encrypt_password is None:
        encrypt_password = default_encrypt_password

    try:
        encrypted_pwd = encrypt_password(password)
    except Exception as e:
        logger.exception("encrypt_password failed")
        QMessageBox.critical(self, "Błąd", f"Nie udało się zaszyfrować hasła: {e}")
        return

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO konta_email
            (pracownik_id, email, serwer_imap, port_imap, ssl_imap,
             serwer_smtp, port_smtp, ssl_smtp, haslo_enc, aktywne)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            getattr(self, "pracownik_id", None),
            email_addr,
            imap_srv,
            int(imap_p) if imap_p is not None else None,
            ssl_imap,
            smtp_srv,
            int(smtp_p) if smtp_p is not None else None,
            ssl_smtp,
            encrypted_pwd,
            aktywne,
        ))
        conn.commit()
        QMessageBox.information(self, "Sukces", "Konto email zostało dodane!")
        self.email_input.clear()
        self.imap_server.clear()
        self.smtp_server.clear()
        self.email_password.clear()
        refresh_email_accounts(self)
    except Exception as e:
        logger.exception("add_email_account failed")
        QMessageBox.critical(self, "Błąd", f"Błąd dodawania konta: {str(e)}")
    finally:
        conn.close()


def delete_email_account(self, konto_id):
    reply = QMessageBox.question(
        self,
        "Potwierdzenie",
        "Usunąć to konto i wszystkie powiązane dane?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute("DELETE FROM wiadomosci_email WHERE konto_id = ?", (konto_id,))
        cur.execute("DELETE FROM foldery_email WHERE konto_id = ?", (konto_id,))
        cur.execute("DELETE FROM pracownik_konto_email WHERE konto_id = ?", (konto_id,))
        cur.execute("DELETE FROM konta_email WHERE id = ?", (konto_id,))
        conn.commit()
        refresh_email_accounts(self)
    except Exception as e:
        conn.rollback()
        logger.exception("delete_email_account failed")
        QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć konta: {e}")
    finally:
        conn.close()

def get_email_accounts_for_employee(pracownik_id: int):
    """
    Zwraca listę kont email przypisanych do pracownika.
    Obsługuje multi-assign (pracownik_konto_email).
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT ke.id, ke.email, ke.serwer_imap, ke.port_imap, ke.ssl_imap,
                   ke.serwer_smtp, ke.port_smtp, ke.ssl_smtp, ke.haslo_enc, ke.aktywne
            FROM konta_email ke
            JOIN pracownik_konto_email pke ON pke.konto_id = ke.id
            WHERE pke.pracownik_id = ?
            ORDER BY ke.id
        """, (pracownik_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def get_folders_for_account(account_id: int) -> list[str]:
    """
    Pobiera listę folderów IMAP zapisanych w bazie dla danego konta.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT nazwa
        FROM foldery_email
        WHERE konto_id = ?
        ORDER BY nazwa ASC
    """, (account_id,))

    rows = cur.fetchall()
    conn.close()

    return [r["nazwa"] for r in rows]

def add_folder_for_account(account_id: int, folder_name: str) -> None:
    """
    Dodaje folder do bazy, jeśli jeszcze go nie ma.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO foldery_email (konto_id, nazwa)
        VALUES (?, ?)
    """, (account_id, folder_name))

    conn.commit()
    conn.close()


def remove_folder_for_account(account_id: int, folder_name: str) -> None:
    """
    Usuwa folder z bazy.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM foldery_email
        WHERE konto_id = ? AND nazwa = ?
    """, (account_id, folder_name))

    conn.commit()
    conn.close()
