# app/utils/import_wizard.py
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton
)
from typing import List, Dict, Any

from app.companies.company_form import CompanyForm
from app.utils.importers import save_imported_payload


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
        self.resize(900, 700)

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

        payload = self.payloads[self.index]
        self.label.setText(
            f"Firma {self.index + 1} z {len(self.payloads)}\n"
            f"{payload.get('nazwa', '')} | NIP: {payload.get('nip', '') or 'brak'}\n"
            "Dane możesz zweryfikować i poprawić w formularzu."
        )

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
                reviewed_payload = dlg._collect_payload()
                # Keep parsed pricing and assignment metadata from the source
                # row; CompanyForm exposes the normal company fields only.
                for key in (
                    "cennik_ksiegowy",
                    "cennik_kadrowy",
                    "cennik_czasowy",
                    "cennik_ksiegowy_parsed",
                    "cennik_kadrowy_parsed",
                    "cennik_czasowy_parsed",
                    "assigned_raw",
                ):
                    if key in payload:
                        reviewed_payload[key] = payload[key]
                result = save_imported_payload(reviewed_payload, row_number=self.index + 2)
                if result.get("warnings"):
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self,
                        "Ostrzeżenia zapisu",
                        "\n".join(result["warnings"]),
                    )
            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Błąd zapisu", str(e))

        self.index += 1
        self._update_label()
