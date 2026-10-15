# app/admin/settings/email_settings.py

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.email.email_connection import (
    DEFAULT_PORTS, SECURITY_CHOICES, check_imap, check_smtp, normalize_security,
)


def _run_test(parent, account: dict) -> None:
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        ok_i, msg_i = check_imap(account)
        ok_s, msg_s = check_smtp(account)
    finally:
        QApplication.restoreOverrideCursor()

    text = (
        f"Odbieranie (IMAP): {'OK' if ok_i else 'BŁĄD'}\n{msg_i}\n\n"
        f"Wysyłanie (SMTP): {'OK' if ok_s else 'BŁĄD'}\n{msg_s}"
    )
    if ok_i and ok_s:
        QMessageBox.information(parent, "Test połączenia", text)
    else:
        QMessageBox.warning(parent, "Test połączenia", text)


class AccountDialog(QDialog):
    """Dodawanie i edycja konta e-mail (hasło przy edycji opcjonalne)."""

    def __init__(self, parent=None, acc=None):
        super().__init__(parent)
        self.editing = acc is not None
        self.setWindowTitle("Edytuj konto e-mail" if acc else "Dodaj konto e-mail")
        self.setMinimumWidth(460)
        form = QFormLayout(self)

        self.e_email = QLineEdit(acc["email"] if acc else "")
        self.e_haslo = QLineEdit()
        self.e_haslo.setEchoMode(QLineEdit.EchoMode.Password)
        if acc:
            self.e_haslo.setPlaceholderText("Zostaw puste, aby nie zmieniać")

        self.e_imap = QLineEdit(acc["serwer_imap"] if acc else "")
        self.e_imap_port = QLineEdit(str(acc["port_imap"]) if acc else "993")
        self.cb_imap = QComboBox()
        self.cb_imap.addItems(SECURITY_CHOICES)
        self.cb_imap.setCurrentText(normalize_security(acc["ssl_imap"] if acc else "SSL/TLS"))

        self.e_smtp = QLineEdit(acc["serwer_smtp"] if acc else "")
        self.e_smtp_port = QLineEdit(str(acc["port_smtp"]) if acc else "465")
        self.cb_smtp = QComboBox()
        self.cb_smtp.addItems(SECURITY_CHOICES)
        self.cb_smtp.setCurrentText(normalize_security(acc["ssl_smtp"] if acc else "SSL/TLS"))

        self.cb_imap.currentTextChanged.connect(
            lambda t: self.e_imap_port.setText(str(DEFAULT_PORTS["imap"][t]))
        )
        self.cb_smtp.currentTextChanged.connect(
            lambda t: self.e_smtp_port.setText(str(DEFAULT_PORTS["smtp"][t]))
        )
        self.e_email.editingFinished.connect(self._guess_servers)

        form.addRow("Adres e-mail:", self.e_email)
        form.addRow("Hasło:", self.e_haslo)
        form.addRow(QLabel("<b>Poczta przychodząca (IMAP)</b>"))
        form.addRow("Serwer:", self.e_imap)
        form.addRow("Port:", self.e_imap_port)
        form.addRow("Zabezpieczenia połączenia:", self.cb_imap)
        form.addRow(QLabel("<b>Poczta wychodząca (SMTP)</b>"))
        form.addRow("Serwer:", self.e_smtp)
        form.addRow("Port:", self.e_smtp_port)
        form.addRow("Zabezpieczenia połączenia:", self.cb_smtp)

        hint = QLabel(
            "Gmail, Outlook i część innych dostawców wymagają hasła aplikacji, "
            "a nie zwykłego hasła do konta."
        )
        hint.setWordWrap(True)
        form.addRow(hint)

        btn_test = QPushButton("Testuj połączenie")
        btn_test.clicked.connect(self._test)
        form.addRow(btn_test)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._acc = acc

    def _guess_servers(self):
        if self.editing or self.e_imap.text() or self.e_smtp.text():
            return
        email = self.e_email.text().strip()
        if "@" not in email:
            return
        domain = email.split("@", 1)[1].lower()
        self.e_imap.setText(f"imap.{domain}")
        self.e_smtp.setText(f"smtp.{domain}")

    def values(self) -> dict:
        return {
            "email": self.e_email.text().strip(),
            "haslo": self.e_haslo.text(),
            "serwer_imap": self.e_imap.text().strip(),
            "port_imap": int(self.e_imap_port.text() or 0),
            "ssl_imap": self.cb_imap.currentText(),
            "serwer_smtp": self.e_smtp.text().strip(),
            "port_smtp": int(self.e_smtp_port.text() or 0),
            "ssl_smtp": self.cb_smtp.currentText(),
        }

    def _test(self):
        try:
            data = self.values()
        except ValueError:
            QMessageBox.warning(self, "Błąd", "Port musi być liczbą.")
            return
        if not data["haslo"] and self._acc:
            data["haslo_enc"] = self._acc["haslo_enc"]
        _run_test(self, data)


class EmployeeAccessDialog(QDialog):
    def __init__(self, parent, employees, selected_ids):
        super().__init__(parent)
        self.setWindowTitle("Dostęp pracowników do konta")
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Pracownicy, którzy widzą to konto w module Poczta:"))

        self.list = QListWidget()
        for e in employees:
            name = f"{e['imie'] or ''} {e['nazwisko'] or ''}".strip() or e["email"]
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, e["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if e["id"] in selected_ids else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        layout.addWidget(self.list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_ids(self):
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]


class EmailSettings(QWidget):
    """Konta e-mail: dodawanie, edycja, test połączenia i dostęp pracowników."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Konta e-mail")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["ID", "E-mail", "Serwer IMAP", "Serwer SMTP", "Aktywne", "Dostęp pracowników"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        for text, slot in (
            ("Odśwież", self._refresh),
            ("Dodaj konto", self._add),
            ("Edytuj wybrane", self._edit_selected),
            ("Testuj połączenie", self._test_selected),
            ("Dostęp pracowników", self._edit_access),
            ("Aktywuj / Dezaktywuj", self._toggle_active),
            ("Usuń", self._delete),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

    def _employee_names(self):
        names = {}
        for e in self.logic.list_employees():
            names[e["id"]] = (
                f"{e['imie'] or ''} {e['nazwisko'] or ''}".strip() or e["email"]
            )
        return names

    def _refresh(self):
        rows = self.logic.get_email_accounts()
        names = self._employee_names()
        self.table.setRowCount(0)

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            access = ", ".join(
                names.get(i, str(i)) for i in self.logic.get_account_employee_ids(r["id"])
            )
            values = [
                str(r["id"]), r["email"] or "", r["serwer_imap"] or "",
                r["serwer_smtp"] or "", "Tak" if r["aktywne"] else "Nie",
                access or "— nikt —",
            ]
            for col, val in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(val))

    def _selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "E-mail", "Wybierz konto.")
            return None
        return int(self.table.item(row, 0).text())

    def _add(self):
        dlg = AccountDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            v = dlg.values()
            if not v["email"] or not v["haslo"]:
                QMessageBox.warning(self, "Błąd", "Podaj adres e-mail i hasło.")
                return
            acc_id = self.logic.add_email_account(
                email=v["email"], imap_server=v["serwer_imap"],
                smtp_server=v["serwer_smtp"], password=v["haslo"],
                imap_port=v["port_imap"], smtp_port=v["port_smtp"],
                imap_ssl=v["ssl_imap"], smtp_ssl=v["ssl_smtp"],
            )
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się dodać: {e}")
            return

        self._refresh()
        self._edit_access(acc_id, after_add=True)

    def _edit_selected(self):
        acc_id = self._selected_id()
        if acc_id is None:
            return
        acc = self.logic.get_email_account(acc_id)
        if not acc:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono konta.")
            return

        dlg = AccountDialog(self, acc)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            v = dlg.values()
            self.logic.update_email_account(
                acc_id=acc_id, email=v["email"],
                imap=v["serwer_imap"], imap_port=v["port_imap"],
                smtp=v["serwer_smtp"], smtp_port=v["port_smtp"],
                ssl_imap=v["ssl_imap"], ssl_smtp=v["ssl_smtp"],
            )
            if v["haslo"]:
                self.logic.update_email_password(acc_id, v["haslo"])
            self._refresh()
            QMessageBox.information(self, "OK", "Zapisano zmiany.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")

    def _test_selected(self):
        acc_id = self._selected_id()
        if acc_id is None:
            return
        acc = self.logic.get_email_account(acc_id)
        if acc:
            _run_test(self, dict(acc))

    def _edit_access(self, acc_id=None, after_add=False):
        if not isinstance(acc_id, int):
            acc_id = self._selected_id()
        if acc_id is None:
            return
        dlg = EmployeeAccessDialog(
            self, self.logic.list_employees(), self.logic.get_account_employee_ids(acc_id)
        )
        if after_add:
            dlg.setWindowTitle("Kto ma mieć dostęp do nowego konta?")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.logic.set_account_employees(acc_id, dlg.selected_ids())
            self._refresh()

    def _toggle_active(self):
        acc_id = self._selected_id()
        if acc_id is None:
            return
        try:
            self.logic.toggle_email_account_active(acc_id)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zmienić statusu: {e}")

    def _delete(self):
        acc_id = self._selected_id()
        if acc_id is None:
            return
        if QMessageBox.question(
            self, "Usuń konto", "Usunąć konto wraz z przypisaniami?"
        ) == QMessageBox.StandardButton.Yes:
            self.logic.delete_email_account(acc_id)
            self._refresh()
