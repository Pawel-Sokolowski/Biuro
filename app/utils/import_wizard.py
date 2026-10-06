# app/utils/import_wizard.py
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton
)
from typing import List, Dict, Any

from app.companies.company_form import CompanyForm
from app.db.dao_companies import save_company


class ImportWizard(QDialog):
    """
    Kreator importu firm:
    - dostaje listę payloadów (każdy payload = jedna firma)
    - dla każdej firmy otwiera pełny CompanyForm
    - użytkownik może poprawić dane
    - po kliknięciu "Zapisz" -> zapis do DB
    - przechodzi do kolejnej firmy
    """

    def __init__(self, payloads: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.payloads = payloads
        self.index = 0

        self.setWindowTitle("Import firm — kreator")
        self.resize(500, 200)

        layout = QVBoxLayout(self)

        self.label = QLabel("")
        layout.addWidget(self.label)

        self.btn_next = QPushButton("Rozpocznij import")
        layout.addWidget(self.btn_next)

        self.btn_next.clicked.connect(self._next)

        self._update_label()

    def _update_label(self):
        if self.index >= len(self.payloads):
            self.label.setText("Import zakończony.")
            self.btn_next.setText("Zamknij")
            return

        self.label.setText(f"Firma {self.index + 1} z {len(self.payloads)}")

    def _next(self):
        if self.index >= len(self.payloads):
            self.accept()
            return

        payload = self.payloads[self.index]

        # otwieramy pełny CompanyForm
        dlg = CompanyForm(None, self)
        dlg._load_company_from_payload(payload)

        if dlg.exec():
            try:
                save_company(dlg._collect_payload())
            except Exception as e:
                print("Błąd zapisu:", e)

        self.index += 1
        self._update_label()
