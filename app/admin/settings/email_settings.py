# app/admin/settings/email_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QMessageBox, QHBoxLayout,
    QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic


class EmailSettings(QWidget):
    """Konta email: podgląd, dodawanie, edycja, aktywacja."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Konta email")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "ID", "Email", "IMAP", "SMTP", "Aktywne", "Pracownik", "SSL"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)

        btn_add = QPushButton("Dodaj konto")
        btn_add.clicked.connect(self._add)
        btn_row.addWidget(btn_add)

        btn_edit = QPushButton("Edytuj wybrane")
        btn_edit.clicked.connect(self._edit_selected)
        btn_row.addWidget(btn_edit)

        btn_toggle = QPushButton("Aktywuj / Dezaktywuj")
        btn_toggle.clicked.connect(self._toggle_active)
        btn_row.addWidget(btn_toggle)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

    def _refresh(self):
        rows = self.logic.get_email_accounts()
        self.table.setRowCount(0)

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(str(r["id"])))
            self.table.setItem(row, 1, QTableWidgetItem(r["email"] or ""))
            self.table.setItem(row, 2, QTableWidgetItem(r["serwer_imap"] or ""))
            self.table.setItem(row, 3, QTableWidgetItem(r["serwer_smtp"] or ""))
            self.table.setItem(
                row, 4,
                QTableWidgetItem("Tak" if r["aktywne"] else "Nie")
            )
            self.table.setItem(
                row, 5,
                QTableWidgetItem(str(r["pracownik_id"] or ""))
            )
            self.table.setItem(row, 6, QTableWidgetItem(r["ssl_imap"] or ""))

    def _add(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj konto email")
        form = QFormLayout(dlg)

        e_email = QLineEdit()
        e_haslo = QLineEdit()
        e_haslo.setEchoMode(QLineEdit.EchoMode.Password)

        e_imap = QLineEdit("imap.gmail.com")
        e_imap_port = QLineEdit("993")

        e_smtp = QLineEdit("smtp.gmail.com")
        e_smtp_port = QLineEdit("465")

        cb_ssl = QComboBox()
        cb_ssl.addItems(["SSL/TLS", "STARTTLS"])

        form.addRow("Email:", e_email)
        form.addRow("Hasło:", e_haslo)
        form.addRow("IMAP serwer:", e_imap)
        form.addRow("IMAP port:", e_imap_port)
        form.addRow("SMTP serwer:", e_smtp)
        form.addRow("SMTP port:", e_smtp_port)
        form.addRow("SSL:", cb_ssl)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.add_email_account(
                    email=e_email.text().strip(),
                    password=e_haslo.text(),
                    imap_server=e_imap.text().strip(),
                    imap_port=int(e_imap_port.text()),
                    smtp_server=e_smtp.text().strip(),
                    smtp_port=int(e_smtp_port.text()),
                    imap_ssl=cb_ssl.currentText(),
                    smtp_ssl=cb_ssl.currentText(),
                )
                self._refresh()
                QMessageBox.information(self, "OK", "Dodano konto email.")
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać: {e}")

    def _edit_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Email", "Wybierz konto.")
            return

        acc_id = int(self.table.item(row, 0).text())
        acc = self.logic.get_email_account(acc_id)
        if not acc:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono konta.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edytuj konto: {acc['email']}")
        form = QFormLayout(dlg)

        e_email = QLineEdit(acc["email"])
        e_imap = QLineEdit(acc["serwer_imap"])
        e_imap_port = QLineEdit(str(acc["port_imap"]))
        e_smtp = QLineEdit(acc["serwer_smtp"])
        e_smtp_port = QLineEdit(str(acc["port_smtp"]))

        cb_ssl = QComboBox()
        cb_ssl.addItems(["SSL/TLS", "STARTTLS"])
        cb_ssl.setCurrentText(acc["ssl_imap"])

        form.addRow("Email:", e_email)
        form.addRow("IMAP serwer:", e_imap)
        form.addRow("IMAP port:", e_imap_port)
        form.addRow("SMTP serwer:", e_smtp)
        form.addRow("SMTP port:", e_smtp_port)
        form.addRow("SSL:", cb_ssl)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.update_email_account(
                    acc_id=acc_id,
                    email=e_email.text().strip(),
                    imap=e_imap.text().strip(),
                    imap_port=int(e_imap_port.text()),
                    smtp=e_smtp.text().strip(),
                    smtp_port=int(e_smtp_port.text()),
                    ssl_imap=cb_ssl.currentText(),
                    ssl_smtp=cb_ssl.currentText(),
                )
                self._refresh()
                QMessageBox.information(self, "OK", "Zapisano zmiany.")
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")

    def _toggle_active(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Email", "Wybierz konto.")
            return

        acc_id = int(self.table.item(row, 0).text())
        try:
            self.logic.toggle_email_account_active(acc_id)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zmienić statusu: {e}")
