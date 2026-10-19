# app/admin/settings/database_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QHBoxLayout, QFileDialog, QSpinBox
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.config import get_database


class DatabaseSettings(QWidget):
    """Wybór pliku bazy danych (lokalnie lub NAS)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Ustawienia bazy danych")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        layout.addWidget(QLabel(f"PostgreSQL: {get_database()}"))
        layout.addWidget(QLabel("Po??czenie jest ustawiane w pliku .env (DB_URL). Zmiana wymaga restartu aplikacji."))

        btn_row = QHBoxLayout()
        btn_dump = QPushButton("Zapisz kopi? bazy do pliku...")
        btn_dump.clicked.connect(self._dump)
        btn_row.addWidget(btn_dump)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        backup_title = QLabel("Kopia zapasowa — log z maila lub z pliku")
        backup_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(backup_title)
        layout.addWidget(QLabel("Log jest odbierany na ukryte konto systemowe (Ustawienia → Reset hasła i kalendarz)."))
        layout.addWidget(QLabel("Opcjonalnie: plik logu lub folder (ma pierwszeństwo przed mailem, zostaw puste dla maila):"))

        self.backup_path_edit = QLineEdit(self.logic.get_setting("backup_log_path") or "")
        layout.addWidget(self.backup_path_edit)

        age_row = QHBoxLayout()
        age_row.addWidget(QLabel("Ostrzegaj, gdy kopia starsza niż (godz.):"))
        self.backup_age = QSpinBox()
        self.backup_age.setRange(1, 720)
        self.backup_age.setValue(int(self.logic.get_setting("backup_max_age_hours") or 36))
        age_row.addWidget(self.backup_age)
        age_row.addStretch()
        layout.addLayout(age_row)

        backup_btns = QHBoxLayout()
        btn_log_file = QPushButton("Wybierz plik logu")
        btn_log_file.clicked.connect(self._choose_backup_log)
        btn_log_dir = QPushButton("Wybierz folder logów")
        btn_log_dir.clicked.connect(self._choose_backup_dir)
        btn_backup_save = QPushButton("Zapisz i sprawdź")
        btn_backup_save.clicked.connect(self._save_backup)
        for button in (btn_log_file, btn_log_dir, btn_backup_save):
            backup_btns.addWidget(button)
        backup_btns.addStretch()
        layout.addLayout(backup_btns)

        self.backup_status = QLabel()
        self.backup_status.setWordWrap(True)
        layout.addWidget(self.backup_status)
        layout.addStretch()
        self._show_backup_status()

    def _choose_backup_log(self):
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz plik logu", "", "Logi (*.log *.txt);;Wszystkie pliki (*)")
        if path:
            self.backup_path_edit.setText(path)

    def _choose_backup_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Wybierz folder z logami kopii")
        if path:
            self.backup_path_edit.setText(path)

    def _save_backup(self):
        try:
            self.logic.set_setting("backup_log_path", self.backup_path_edit.text().strip())
            self.logic.set_setting("backup_max_age_hours", str(self.backup_age.value()))
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")
            return
        self._show_backup_status()

    def _show_backup_status(self):
        from app.utils.backup_status import get_backup_status
        self.backup_status.setText(get_backup_status()["komunikat"])

    def _dump(self):
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz kopi? bazy", "biuro.dump", "Kopia PostgreSQL (*.dump);;Wszystkie pliki (*)")
        if not path:
            return
        try:
            self.logic.backup_database(path)
            QMessageBox.information(self, "OK", "Zapisano kopi? bazy.")
        except Exception as e:
            QMessageBox.critical(self, "B??d", f"Nie uda?o si? wykona? kopii: {e}")
