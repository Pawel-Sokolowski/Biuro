from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QPushButton,
    QHeaderView, QTabWidget,
)

from app.db.dao_audit import list_audit, list_import_history


class AuditSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.audit_table = QTableWidget(0, 6)
        self.audit_table.setHorizontalHeaderLabels(
            ["ID", "Data", "Użytkownik", "Tabela", "Akcja", "Szczegóły"]
        )
        self.audit_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabs.addTab(self.audit_table, "Audyt")

        self.import_table = QTableWidget(0, 8)
        self.import_table.setHorizontalHeaderLabels(
            ["ID", "Data", "Typ", "Plik", "Utworzono", "Zmieniono", "Ostrzeżenia", "Status"]
        )
        self.import_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabs.addTab(self.import_table, "Historia importów")

        refresh = QPushButton("Odśwież")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        self.refresh()

    def refresh(self):
        self.audit_table.setRowCount(0)
        for item in list_audit():
            row = self.audit_table.rowCount()
            self.audit_table.insertRow(row)
            values = [item["id"], item["data"], item["uzytkownik"], item["tabela"],
                      item["akcja"], item["szczegoly"] or ""]
            for col, value in enumerate(values):
                self.audit_table.setItem(row, col, QTableWidgetItem(str(value)))

        self.import_table.setRowCount(0)
        for item in list_import_history():
            row = self.import_table.rowCount()
            self.import_table.insertRow(row)
            values = [item["id"], item["started_at"], item["import_type"],
                      item["source_path"] or "", item["created_count"],
                      item["updated_count"], item["warning_count"], item["status"]]
            for col, value in enumerate(values):
                self.import_table.setItem(row, col, QTableWidgetItem(str(value)))
