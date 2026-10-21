from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from app.utils.import_all_wizard import STEPS, run_import_wizard


class ImportAllSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("Import danych")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        steps = "\n".join(f"{i}. {what}" for i, (what, *_rest) in enumerate(STEPS, start=1))
        layout.addWidget(QLabel(
            "Kreator poprowadzi przez kolejne pliki (każdy krok można pominąć):\n" + steps
            + "\n\nKolejność ma znaczenie: firmy muszą być wczytane przed czasomierzem i fakturami."
        ))

        btn = QPushButton("Importuj dane")
        btn.setStyleSheet("font-weight: bold; padding: 8px 16px;")
        btn.clicked.connect(lambda: run_import_wizard(self))
        layout.addWidget(btn)
        layout.addStretch()
