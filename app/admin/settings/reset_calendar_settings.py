import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from PyQt6.QtGui import QFont

from app.db.dao_companies import get_employees_list
from app.kalendarz import caldav_sync, google_sync
from app.utils.system_mailbox import get_system_account, save_system_account

SSL_MODES = ("SSL/TLS", "STARTTLS", "Brak")


class CalendarAndResetSettings(QWidget):
    """Ukryte konto systemowe (reset haseł, logi kopii) oraz dane klienta Google Calendar."""

    _test_done = pyqtSignal(bool, str)
    _google_done = pyqtSignal(bool, str)

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

        gtitle = QLabel("Dwustronna synchronizacja z Google Calendar")
        gtitle.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(gtitle)

        client_id, client_secret = google_sync.get_client_credentials()
        self.google_client_id = QLineEdit(client_id)
        self.google_client_secret = QLineEdit()
        self.google_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.google_client_secret.setPlaceholderText("(zapisany)" if client_secret else "Client secret")
        gform = QFormLayout()
        gform.addRow("Google OAuth Client ID:", self.google_client_id)
        gform.addRow("Google OAuth Client Secret:", self.google_client_secret)
        layout.addLayout(gform)
        layout.addWidget(QLabel(
            "W Google Cloud utwórz klienta OAuth typu „Aplikacja na komputer”, "
            "włącz Google Calendar API i wpisz tutaj jego dane. Następnie połącz "
            "jedno firmowe konto Google i przypisz pracowników do kalendarzy."
        ))

        gbtns = QHBoxLayout()
        self.btn_google_connect = QPushButton("Połącz konto Google")
        self.btn_google_disconnect = QPushButton("Odłącz konto")
        self.btn_google_test = QPushButton("Testuj połączenie Google")
        self.btn_google_connect.clicked.connect(self._connect_google)
        self.btn_google_disconnect.clicked.connect(self._disconnect_google)
        self.btn_google_test.clicked.connect(self._test_google)
        for b in (self.btn_google_connect, self.btn_google_disconnect, self.btn_google_test):
            gbtns.addWidget(b)
        layout.addLayout(gbtns)
        self.google_status = QLabel("")
        self.google_status.setWordWrap(True)
        layout.addWidget(self.google_status)
        self._google_done.connect(self._show_google_result)

        layout.addWidget(QLabel("Dostęp pracowników do kalendarzy Google (zaznacz uprawnionych):"))
        self.access_tree = QTreeWidget()
        self.access_tree.setHeaderHidden(True)
        self.access_tree.setMinimumHeight(180)
        layout.addWidget(self.access_tree)
        self._fill_access_tree(
            [{"id": cid, "nazwa": info["nazwa"]} for cid, info in google_sync.get_access().items()]
        )
        self._update_google_buttons()

        cfg = caldav_sync.get_config()
        caldav_title = QLabel("Inny serwer CalDAV (opcjonalnie)")
        caldav_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(caldav_title)
        self.caldav_url = QLineEdit(cfg["url"])
        self.caldav_url.setPlaceholderText("Adres kolekcji CalDAV (nie adres iCal)")
        self.caldav_login = QLineEdit(cfg["login"])
        self.caldav_password = QLineEdit()
        self.caldav_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.caldav_password.setPlaceholderText("(zapisane)" if cfg["haslo"] else "Hasło")
        caldav_form = QFormLayout()
        caldav_form.addRow("Adres URL serwera CalDAV:", self.caldav_url)
        caldav_form.addRow("Login:", self.caldav_login)
        caldav_form.addRow("Hasło:", self.caldav_password)
        layout.addLayout(caldav_form)
        self.caldav_status = QLabel("")
        self.caldav_status.setWordWrap(True)
        self.btn_test_caldav = QPushButton("Testuj połączenie CalDAV")
        self.btn_test_caldav.clicked.connect(self._test_caldav)
        self._test_done.connect(self._show_test_result)
        layout.addWidget(self.btn_test_caldav)
        layout.addWidget(self.caldav_status)

        btn = QPushButton("Zapisz")
        btn.clicked.connect(self._save)
        layout.addWidget(btn)
        layout.addStretch()

    def _update_google_buttons(self):
        connected = google_sync.is_account_connected()
        self.btn_google_connect.setEnabled(not connected)
        self.btn_google_disconnect.setEnabled(connected)
        self.btn_google_test.setEnabled(True)
        if connected and not self.google_status.text():
            self.google_status.setText("Konto Google połączone.")

    def _fill_access_tree(self, calendars):
        access = google_sync.get_access()
        self.access_tree.clear()
        employees = get_employees_list()
        for cal in calendars:
            granted = set(access.get(cal["id"], {}).get("pracownicy", []))
            top = QTreeWidgetItem(self.access_tree, [cal["nazwa"]])
            top.setData(0, Qt.ItemDataRole.UserRole, cal["id"])
            for emp in employees:
                child = QTreeWidgetItem(top, [f"{emp['imie']} {emp['nazwisko']}"])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(
                    0, Qt.CheckState.Checked if emp["id"] in granted else Qt.CheckState.Unchecked
                )
                child.setData(0, Qt.ItemDataRole.UserRole, emp["id"])
            top.setExpanded(True)

    def _collect_access(self):
        result = {}
        for i in range(self.access_tree.topLevelItemCount()):
            top = self.access_tree.topLevelItem(i)
            ids = [
                top.child(j).data(0, Qt.ItemDataRole.UserRole)
                for j in range(top.childCount())
                if top.child(j).checkState(0) == Qt.CheckState.Checked
            ]
            result[top.data(0, Qt.ItemDataRole.UserRole)] = {"nazwa": top.text(0), "pracownicy": ids}
        return result

    def _save_google_credentials(self):
        google_sync.save_client_credentials(
            self.google_client_id.text(), self.google_client_secret.text()
        )

    def _run_google(self, work, busy_text):
        for b in (self.btn_google_connect, self.btn_google_disconnect, self.btn_google_test):
            b.setEnabled(False)
        self.google_status.setStyleSheet("")
        self.google_status.setText(busy_text)

        def target():
            try:
                self._google_done.emit(True, work())
            except Exception as exc:
                self._google_done.emit(False, str(exc))

        threading.Thread(target=target, daemon=True).start()

    def _connect_google(self):
        try:
            self._save_google_credentials()
        except Exception as exc:
            QMessageBox.critical(self, "Błąd", str(exc))
            return

        def work():
            google_sync.connect()
            self._pending_calendars = google_sync.list_account_calendars()
            return "Konto Google połączone. Przypisz pracowników do kalendarzy i kliknij „Zapisz”."

        self._run_google(work, "Otwieranie logowania Google w przeglądarce…")

    def _disconnect_google(self):
        google_sync.disconnect()
        self.google_status.setStyleSheet("")
        self.google_status.setText("Konto Google odłączone.")
        self._update_google_buttons()

    def _test_google(self):
        try:
            self._save_google_credentials()
        except Exception as exc:
            QMessageBox.critical(self, "Błąd", str(exc))
            return

        def work():
            message = google_sync.test_connection()
            self._pending_calendars = google_sync.list_account_calendars()
            return message

        self._run_google(work, "Łączenie z Google…")

    def _show_google_result(self, ok: bool, message: str):
        self.google_status.setText(message)
        self.google_status.setStyleSheet("color: #2e7d32;" if ok else "color: #c62828;")
        calendars = getattr(self, "_pending_calendars", None)
        if ok and calendars is not None:
            current = self._collect_access()
            self._fill_access_tree(calendars)
            for i in range(self.access_tree.topLevelItemCount()):
                top = self.access_tree.topLevelItem(i)
                prev = current.get(top.data(0, Qt.ItemDataRole.UserRole))
                if prev:
                    for j in range(top.childCount()):
                        if top.child(j).data(0, Qt.ItemDataRole.UserRole) in prev["pracownicy"]:
                            top.child(j).setCheckState(0, Qt.CheckState.Checked)
            self._pending_calendars = None
        self._update_google_buttons()

    def _test_caldav(self):
        url, login = self.caldav_url.text(), self.caldav_login.text()
        password = self.caldav_password.text() or caldav_sync.get_config()["haslo"]
        self.btn_test_caldav.setEnabled(False)
        self.caldav_status.setText("Łączenie…")

        def work():
            try:
                self._test_done.emit(True, caldav_sync.test_connection(url, login, password))
            except Exception as exc:
                self._test_done.emit(False, caldav_sync.friendly_error(exc))

        threading.Thread(target=work, daemon=True).start()

    def _show_test_result(self, ok: bool, message: str):
        self.btn_test_caldav.setEnabled(True)
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
            google_sync.save_client_credentials(
                self.google_client_id.text(), self.google_client_secret.text()
            )
            if self.access_tree.topLevelItemCount() or google_sync.get_access():
                google_sync.save_access(self._collect_access())
            caldav_sync.save_config(
                self.caldav_url.text(), self.caldav_login.text(), self.caldav_password.text()
            )
            if self.google_client_secret.text():
                self.google_client_secret.clear()
                self.google_client_secret.setPlaceholderText("(zapisany)")
            if self.caldav_password.text():
                self.caldav_password.clear()
                self.caldav_password.setPlaceholderText("(zapisane)")
            QMessageBox.information(self, "OK", "Zapisano ustawienia.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")
