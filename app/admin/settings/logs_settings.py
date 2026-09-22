# app/admin/settings/logs_settings.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QMessageBox, QHBoxLayout
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic


class LogsSettings(QWidget):
    """Podgląd logów systemowych."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Logi systemowe")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            "ID", "Poziom", "Moduł", "Wiadomość"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)

        btn_clear = QPushButton("Wyczyść logi")
        btn_clear.clicked.connect(self._clear)
        btn_row.addWidget(btn_clear)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

    def _refresh(self):
        rows = self.logic.get_logs()
        self.table.setRowCount(0)

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(str(r["id"])))
            self.table.setItem(row, 1, QTableWidgetItem(r.get("poziom", "")))
            self.table.setItem(row, 2, QTableWidgetItem(r.get("modul", "")))
            self.table.setItem(row, 3, QTableWidgetItem(r.get("wiadomosc", "")))

    def _clear(self):
        try:
            self.logic.clear_logs()
            self._refresh()
            QMessageBox.information(self, "OK", "Wyczyszczono logi.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się wyczyścić: {e}")
