import threading

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from PyQt6.QtGui import QFont

from app.kalendarz import caldav_sync
from app.utils.system_mailbox import get_system_account, save_system_account

SSL_MODES = ("SSL/TLS", "STARTTLS", "Brak")


class CalendarAndResetSettings(QWidget):
    """Ukryte konto systemowe (reset haseł, logi kopii) oraz dane klienta Google Calendar."""

    _test_done = pyqtSignal(bool, str)

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

        gtitle = QLabel("Kalendarz CalDAV (Google Calendar lub inny serwer)")
        gtitle.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(gtitle)

        cfg = caldav_sync.get_config()
        self.caldav_url = QLineEdit(cfg["url"])
        self.caldav_url.setPlaceholderText(caldav_sync.GOOGLE_HINT)
        self.caldav_login = QLineEdit(cfg["login"])
        self.caldav_password = QLineEdit()
        self.caldav_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.caldav_password.setPlaceholderText("(zapisane)" if cfg["haslo"] else "Hasło lub hasło aplikacji")
        gform = QFormLayout()
        gform.addRow("Adres URL serwera CalDAV:", self.caldav_url)
        gform.addRow("Login:", self.caldav_login)
        gform.addRow("Hasło:", self.caldav_password)
        layout.addLayout(gform)
        layout.addWidget(QLabel(
            "Dla Google podaj adres w postaci\n"
            f"{caldav_sync.GOOGLE_HINT}\n"
            "(wymagane uwierzytelnienie obsługiwane przez serwer, np. hasło aplikacji)."
        ))
        self.caldav_status = QLabel("")
        self.caldav_status.setWordWrap(True)
        self.btn_test = QPushButton("Testuj połączenie")
        self.btn_test.clicked.connect(self._test_caldav)
        self._test_done.connect(self._show_test_result)
        layout.addWidget(self.btn_test)
        layout.addWidget(self.caldav_status)

        btn = QPushButton("Zapisz")
        btn.clicked.connect(self._save)
        layout.addWidget(btn)
        layout.addStretch()

    def _test_caldav(self):
        url, login = self.caldav_url.text(), self.caldav_login.text()
        password = self.caldav_password.text() or caldav_sync.get_config()["haslo"]
        self.btn_test.setEnabled(False)
        self.caldav_status.setText("Łączenie…")

        def work():
            try:
                self._test_done.emit(True, caldav_sync.test_connection(url, login, password))
            except Exception as exc:
                self._test_done.emit(False, caldav_sync.friendly_error(exc))

        threading.Thread(target=work, daemon=True).start()

    def _show_test_result(self, ok: bool, message: str):
        self.btn_test.setEnabled(True)
        self.caldav_status.setText(message)
        self.caldav_status.setStyleSheet("color: #2e7d32;" if ok else "color: #c62828;")

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
            caldav_sync.save_config(
                self.caldav_url.text(), self.caldav_login.text(), self.caldav_password.text()
            )
            if self.caldav_password.text():
                self.caldav_password.clear()
                self.caldav_password.setPlaceholderText("(zapisane)")
            QMessageBox.information(self, "OK", "Zapisano ustawienia.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")
