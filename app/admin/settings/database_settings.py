# app/admin/settings/database_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QHBoxLayout, QFileDialog
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic


class DatabaseSettings(QWidget):
    """Wybór pliku bazy danych (lokalnie lub NAS)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Ustawienia bazy danych")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.path_edit = QLineEdit()
        self.path_edit.setText(self.logic.get_setting("db_path") or "")
        layout.addWidget(self.path_edit)

        btn_row = QHBoxLayout()

        btn_choose = QPushButton("Wybierz plik bazy")
        btn_choose.clicked.connect(self._choose_file)
        btn_row.addWidget(btn_choose)

        btn_save = QPushButton("Zapisz")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz bazę danych",
            "",
            "SQLite (*.db *.sqlite *.sqlite3);;Wszystkie pliki (*)"
        )
        if path:
            self.path_edit.setText(path)

    def _save(self):
        path = self.path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Baza", "Podaj ścieżkę do bazy.")
            return

        try:
            self.logic.set_setting("db_path", path)
            QMessageBox.information(self, "OK", "Zapisano ścieżkę bazy danych.\nRestart aplikacji wymagany.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")
