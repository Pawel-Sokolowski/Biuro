# app/email/email_storage.py
import sqlite3
from PyQt6.QtWidgets import (
    QTableWidgetItem,
    QTableWidget,
    QPushButton,
    QMessageBox,
)

from app.db.dao_companies import get_conn
from app.utils.crypto import encrypt_password as default_encrypt_password


def refresh_email_accounts(self):
    """Odśwież listę kont email w UI."""
    self.table_email_accounts.setRowCount(0)
    conn = get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT id, email, serwer_imap, aktywna
            FROM konta_email WHERE pracownik_id = ?
            ORDER BY id ASC
        """, (self.pracownik_id,))

        for row_idx, row in enumerate(cursor.fetchall()):
            self.table_email_accounts.insertRow(row_idx)
            konto_id, email_addr, imap_server, aktywna = row

            self.table_email_accounts.setItem(row_idx, 0, QTableWidgetItem(str(konto_id)))
            self.table_email_accounts.setItem(row_idx, 1, QTableWidgetItem(email_addr or ""))
            self.table_email_accounts.setItem(row_idx, 2, QTableWidgetItem(imap_server or ""))
            self.table_email_accounts.setItem(row_idx, 3, QTableWidgetItem("Tak" if aktywna else "Nie"))

            # Akcje: Usuń
            btn_delete = QPushButton("Usuń")
            btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
            btn_delete.clicked.connect(lambda checked, kid=konto_id: self.delete_email_account(kid))
            self.table_email_accounts.setCellWidget(row_idx, 4, btn_delete)

        # odśwież combosy i kolumny
        try:
            self._refresh_email_account_combos()
        except Exception:
            pass
        self.table_email_accounts.resizeColumnsToContents()
    finally:
        conn.close()


def refresh_email_filters(self):
    """Odśwież listę filtrów email w UI."""
    self.table_email_filters.setRowCount(0)
    conn = get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT rf.id, rf.od, rf.temat_zawiera, rf.folder_docelowy
            FROM reguly_filtrow rf
            JOIN konta_email ke ON rf.konto_id = ke.id
            WHERE ke.pracownik_id = ?
        """, (self.pracownik_id,))

        for row_idx, row in enumerate(cursor.fetchall()):
            self.table_email_filters.insertRow(row_idx)
            filter_id, od, temat, folder = row

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
    """Dodaj nowy filtr dla konta email."""
    konto_id = self.filter_konto_combo.currentData()
    od = self.filter_od.text().strip() or None
    temat = self.filter_temat.text().strip() or None
    folder = self.filter_folder.text().strip()

    if not konto_id or not folder:
        QMessageBox.warning(self, "Błąd", "Wybierz konto i folder docelowy")
        return

    conn = get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute("""
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
        QMessageBox.critical(self, "Błąd", f"Błąd dodawania filtru: {str(e)}")
    finally:
        conn.close()


def delete_email_filter(self, filter_id):
    """Usuń filtr po id (z potwierdzeniem)."""
    reply = QMessageBox.question(
        self,
        "Potwierdzenie",
        "Usunąć ten filtr?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.Yes:
        conn = get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM reguly_filtrow WHERE id = ?", (filter_id,))
            conn.commit()
            refresh_email_filters(self)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć filtru: {e}")
        finally:
            conn.close()


def add_email_account(self, encrypt_password=None):
    """
    Dodaj konto email.
    Jeśli nie podano encrypt_password, użyj domyślnej funkcji z app.utils.crypto.
    """
    email_addr = self.email_input.text().strip()
    imap_srv = self.imap_server.text().strip()
    imap_p = self.imap_port.value()
    smtp_srv = self.smtp_server.text().strip()
    smtp_p = self.smtp_port.value()
    password = self.email_password.text()

    if not all([email_addr, imap_srv, smtp_srv, password]):
        QMessageBox.warning(self, "Błąd", "Uzupełnij wszystkie pola")
        return

    if encrypt_password is None:
        encrypt_password = default_encrypt_password

    try:
        encrypted_pwd = encrypt_password(password)
    except Exception as e:
        QMessageBox.critical(self, "Błąd", f"Nie udało się zaszyfrować hasła: {e}")
        return

    conn = get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO konta_email
            (pracownik_id, email, serwer_imap, port_imap,
             serwer_smtp, port_smtp, haslo_enc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            self.pracownik_id,
            email_addr,
            imap_srv,
            imap_p,
            smtp_srv,
            smtp_p,
            encrypted_pwd,
        ))
        conn.commit()
        QMessageBox.information(self, "Sukces", "Konto email zostało dodane!")

        self.email_input.clear()
        self.imap_server.clear()
        self.smtp_server.clear()
        self.email_password.clear()

        refresh_email_accounts(self)
    except Exception as e:
        QMessageBox.critical(self, "Błąd", f"Błąd dodawania konta: {str(e)}")
    finally:
        conn.close()


def delete_email_account(self, konto_id):
    """Usuń konto email (z potwierdzeniem)."""
    reply = QMessageBox.question(
        self,
        "Potwierdzenie",
        "Usunąć to konto?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.Yes:
        conn = get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM konta_email WHERE id = ?", (konto_id,))
            conn.commit()
            refresh_email_accounts(self)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć konta: {e}")
        finally:
            conn.close()
