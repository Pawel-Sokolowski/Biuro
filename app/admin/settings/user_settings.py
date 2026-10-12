# app/admin/settings/users_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QMessageBox, QHBoxLayout,
    QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.admin.settings.user_companies_dialog import UserCompaniesDialog

class UsersSettings(QWidget):
    """Użytkownicy: podgląd, dodawanie, edycja, reset hasła, aktywacja."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Użytkownicy systemu")
        title.setFont(
            QFont(
                "Arial",
                12,
                QFont.Weight.Bold,
            )
        )
        layout.addWidget(title)

        self.table = QTableWidget(0, 5)

        self.table.setHorizontalHeaderLabels(
            [
                "ID",
                "Email",
                "Imię",
                "Nazwisko",
                "Aktywny",
            ]
        )

        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        layout.addWidget(self.table)

        btn_row = QHBoxLayout()

        btn_refresh = QPushButton(
            "Odśwież"
        )
        btn_refresh.clicked.connect(
            self._refresh
        )
        btn_row.addWidget(
            btn_refresh
        )

        btn_add = QPushButton(
            "Dodaj użytkownika"
        )
        btn_add.clicked.connect(
            self._add
        )
        btn_row.addWidget(
            btn_add
        )

        btn_edit = QPushButton(
            "Edytuj wybranego"
        )
        btn_edit.clicked.connect(
            self._edit_selected
        )
        btn_row.addWidget(
            btn_edit
        )

        btn_reset = QPushButton(
            "Reset hasła"
        )
        btn_reset.clicked.connect(
            self._reset_password
        )
        btn_row.addWidget(
            btn_reset
        )

        btn_companies = QPushButton(
            "Przypisz firmy"
        )
        btn_companies.clicked.connect(
            self._assign_companies
        )
        btn_row.addWidget(
            btn_companies
        )

        btn_toggle = QPushButton(
            "Aktywuj / Dezaktywuj"
        )
        btn_toggle.clicked.connect(
            self._toggle_active
        )
        btn_row.addWidget(
            btn_toggle
        )

        btn_row.addStretch()

        layout.addLayout(
            btn_row
        )

        self._refresh()

    def _refresh(self):
        rows = self.logic.get_users()
        self.table.setRowCount(0)

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(str(r.get("id"))))
            self.table.setItem(row, 1, QTableWidgetItem(r.get("email", "")))
            self.table.setItem(row, 2, QTableWidgetItem(r.get("imie", "")))
            self.table.setItem(row, 3, QTableWidgetItem(r.get("nazwisko", "")))
            self.table.setItem(row, 4, QTableWidgetItem("Tak" if r.get("aktywny") else "Nie"))

    def _add(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj użytkownika")
        form = QFormLayout(dlg)

        e_email = QLineEdit()
        e_imie = QLineEdit()
        e_nazw = QLineEdit()
        e_haslo = QLineEdit()
        e_haslo.setEchoMode(QLineEdit.EchoMode.Password)

        cb_rola = QComboBox()
        roles = self.logic.get_roles()
        for r in roles:
            cb_rola.addItem(r["nazwa"], r["id"])

        form.addRow("Email:", e_email)
        form.addRow("Imię:", e_imie)
        form.addRow("Nazwisko:", e_nazw)
        form.addRow("Hasło:", e_haslo)
        form.addRow("Rola:", cb_rola)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                uid = self.logic.add_user(
                    email=e_email.text().strip(),
                    imie=e_imie.text().strip(),
                    nazwisko=e_nazw.text().strip(),
                    haslo=e_haslo.text().strip()
                )
                rola_id = cb_rola.currentData()
                self.logic.assign_role(uid, rola_id)

                self._refresh()
                QMessageBox.information(self, "OK", "Dodano użytkownika.")

            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać: {e}")

    def _edit_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Użytkownicy", "Wybierz użytkownika.")
            return

        uid = int(self.table.item(row, 0).text())
        user = self.logic.get_user(uid)
        if not user:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono użytkownika.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edytuj użytkownika: {user['email']}")
        form = QFormLayout(dlg)

        e_email = QLineEdit(user["email"])
        e_imie = QLineEdit(user["imie"])
        e_nazw = QLineEdit(user["nazwisko"])

        cb_rola = QComboBox()
        roles = self.logic.get_roles()
        for r in roles:
            cb_rola.addItem(r["nazwa"], r["id"])

        current_role = self.logic.get_user_role(uid)
        if current_role:
            cb_rola.setCurrentIndex(cb_rola.findData(current_role))

        form.addRow("Email:", e_email)
        form.addRow("Imię:", e_imie)
        form.addRow("Nazwisko:", e_nazw)
        form.addRow("Rola:", cb_rola)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.update_user(
                    uid,
                    email=e_email.text().strip(),
                    imie=e_imie.text().strip(),
                    nazwisko=e_nazw.text().strip()
                )
                self.logic.assign_role(uid, cb_rola.currentData())

                self._refresh()
                QMessageBox.information(self, "OK", "Zapisano zmiany.")

            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")

    def _reset_password(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Użytkownicy", "Wybierz użytkownika.")
            return

        uid = int(self.table.item(row, 0).text())

        dlg = QDialog(self)
        dlg.setWindowTitle("Reset hasła")
        form = QFormLayout(dlg)

        e_haslo = QLineEdit()
        e_haslo.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Nowe hasło:", e_haslo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.reset_password(uid, e_haslo.text().strip())
                QMessageBox.information(self, "OK", "Hasło zresetowane.")
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zresetować: {e}")

    def _toggle_active(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Użytkownicy", "Wybierz użytkownika.")
            return

        uid = int(self.table.item(row, 0).text())
        try:
            self.logic.toggle_user_active(uid)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zmienić statusu: {e}")

    def _assign_companies(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Użytkownicy", "Wybierz użytkownika.")
            return

        uid = int(self.table.item(row, 0).text())
        dlg = UserCompaniesDialog(uid, parent=self)
        dlg.exec()