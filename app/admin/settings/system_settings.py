# app/admin/settings/system_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QMessageBox,
    QHBoxLayout, QLineEdit, QComboBox
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic


class SystemSettings(QWidget):
    """Ustawienia systemowe."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Ustawienia systemowe")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.company_name = QLineEdit(self.logic.get_setting("system_company_name") or "")
        self.company_email = QLineEdit(self.logic.get_setting("system_company_email") or "")
        self.company_phone = QLineEdit(self.logic.get_setting("system_company_phone") or "")

        layout.addWidget(QLabel("Nazwa firmy:"))
        layout.addWidget(self.company_name)

        layout.addWidget(QLabel("Email kontaktowy:"))
        layout.addWidget(self.company_email)

        layout.addWidget(QLabel("Telefon kontaktowy:"))
        layout.addWidget(self.company_phone)

        self.theme = QComboBox()
        self.theme.addItems(["Jasny", "Ciemny"])
        current_theme = self.logic.get_setting("system_theme") or "Jasny"
        self.theme.setCurrentText(current_theme)
        layout.addWidget(QLabel("Motyw aplikacji:"))
        layout.addWidget(self.theme)

        btn_row = QHBoxLayout()

        btn_save = QPushButton("Zapisz ustawienia")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _save(self):
        try:
            self.logic.set_setting("system_company_name", self.company_name.text().strip())
            self.logic.set_setting("system_company_email", self.company_email.text().strip())
            self.logic.set_setting("system_company_phone", self.company_phone.text().strip())
            self.logic.set_setting("system_theme", self.theme.currentText())

            QMessageBox.information(self, "OK", "Zapisano ustawienia systemowe.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")
