from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel, QGroupBox, QVBoxLayout, QPushButton
from PyQt6.QtCore import Qt

from app.db.dao_conn import get_conn


class DashboardPage(QWidget):
    """Small operational dashboard with live database-backed counters."""

    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.cards: dict[str, QLabel] = {}
        layout = QVBoxLayout(self)
        title = QLabel("Dashboard")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        grid = QGridLayout()
        layout.addLayout(grid)
        for index, (key, caption) in enumerate(
            (
                ("companies", "Aktywne firmy"),
                ("unread", "Nieprzeczytane emaile"),
                ("tasks", "Otwarte zadania"),
                ("timers", "Aktywne czasomierze"),
                ("documents", "Dokumenty w bieżącym miesiącu"),
            )
        ):
            box = QGroupBox(caption)
            box_layout = QVBoxLayout(box)
            value = QLabel("0")
            value.setStyleSheet("font-size: 26px; font-weight: bold;")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box_layout.addWidget(value)
            self.cards[key] = value
            grid.addWidget(box, index // 3, index % 3)

        refresh = QPushButton("Odśwież")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        layout.addStretch()
        self.refresh()

    def refresh(self):
        conn = get_conn()
        try:
            queries = {
                "companies": "SELECT COUNT(*) FROM firmy",
                "unread": "SELECT COUNT(*) FROM wiadomosci_email WHERE przeczytana = 0",
                "tasks": "SELECT COUNT(*) FROM zadania WHERE status NOT IN ('Zakończone', 'Zamknięte')",
                "timers": "SELECT COUNT(*) FROM czasomierze WHERE data_zakonczenia IS NULL",
                "documents": (
                    "SELECT COUNT(*) FROM dokumenty "
                    "WHERE data_wyst >= date('now', 'start of month') "
                    "AND data_wyst < date('now', 'start of month', '+1 month')"
                ),
            }
            for key, query in queries.items():
                try:
                    value = conn.execute(query).fetchone()[0]
                except Exception:
                    value = 0
                self.cards[key].setText(str(value or 0))
        finally:
            conn.close()
