from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from PyQt6.QtGui import QFont

from app.kalendarz.google_sync import get_client_credentials, save_client_credentials
from app.utils.system_mailbox import get_system_account, save_system_account

SSL_MODES = ("SSL/TLS", "STARTTLS", "Brak")


class CalendarAndResetSettings(QWidget):
    """Ukryte konto systemowe (reset haseł, logi kopii) oraz dane klienta Google Calendar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("Konto systemowe (ukryte) — reset hasła i logi kopii zapasowej")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        account = get_system_account() or {}
        self.email = QLineEdit(account.get("email") or "")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("(zapisane)" if account.get("haslo_enc") else "Hasło")
        self.imap_server = QLineEdit(account.get("serwer_imap") or "")
        self.imap_port = self._port(account.get("port_imap") or 993)
        self.imap_ssl = self._ssl(account.get("ssl_imap"))
        self.smtp_server = QLineEdit(account.get("serwer_smtp") or "")
        self.smtp_port = self._port(account.get("port_smtp") or 465)
        self.smtp_ssl = self._ssl(account.get("ssl_smtp"))

        form = QFormLayout()
        form.addRow("Adres e-mail:", self.email)
        form.addRow("Hasło:", self.password)
        form.addRow("Serwer IMAP (odbiór):", self.imap_server)
        form.addRow("Port IMAP:", self.imap_port)
        form.addRow("Szyfrowanie IMAP:", self.imap_ssl)
        form.addRow("Serwer SMTP (wysyłka):", self.smtp_server)
        form.addRow("Port SMTP:", self.smtp_port)
        form.addRow("Szyfrowanie SMTP:", self.smtp_ssl)
        layout.addLayout(form)
        layout.addWidget(QLabel(
            "To konto nie jest widoczne w module poczty. Wysyła nowe hasła, a na jego\n"
            "skrzynkę przychodzą logi kopii zapasowej pokazywane na Pulpicie."
        ))

        gtitle = QLabel("Google Calendar (OAuth)")
        gtitle.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(gtitle)

        client_id, client_secret = get_client_credentials()
        self.client_id = QLineEdit(client_id)
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret.setPlaceholderText(
            "(zapisany)" if client_secret else "Client secret"
        )
        gform = QFormLayout()
        gform.addRow("Identyfikator klienta (Client ID):", self.client_id)
        gform.addRow("Sekret klienta (Client secret):", self.client_secret)
        layout.addLayout(gform)
        layout.addWidget(QLabel(
            "Utwórz w Google Cloud Console dane OAuth typu „Aplikacja na komputer”\n"
            "i włącz Google Calendar API."
        ))

        btn = QPushButton("Zapisz")
        btn.clicked.connect(self._save)
        layout.addWidget(btn)
        layout.addStretch()

    @staticmethod
    def _port(value):
        box = QSpinBox()
        box.setRange(1, 65535)
        box.setValue(int(value))
        return box

    @staticmethod
    def _ssl(current):
        box = QComboBox()
        box.addItems(SSL_MODES)
        box.setCurrentIndex(max(box.findText(current or "SSL/TLS"), 0))
        return box

    def _save_account(self):
        if not self.email.text().strip() and not self.imap_server.text().strip():
            return
        save_system_account(
            self.email.text(), self.password.text(),
            self.imap_server.text(), self.smtp_server.text(),
            self.imap_port.value(), self.smtp_port.value(),
            self.imap_ssl.currentText(), self.smtp_ssl.currentText(),
        )
        self.password.clear()
        self.password.setPlaceholderText("(zapisane)")

    def _save(self):
        try:
            self._save_account()
            save_client_credentials(self.client_id.text(), self.client_secret.text())
            self.client_secret.clear()
            QMessageBox.information(self, "OK", "Zapisano ustawienia.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")
