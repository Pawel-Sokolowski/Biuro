# app/monthly_view/note_edit_dialog.py

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QHBoxLayout,
    QPushButton, QMessageBox
)
from app.db.dao_notes import save_company_note, update_company_note


class NoteEditDialog(QDialog):
    def __init__(self, firma_id: int, pracownik_id: int, note=None, parent=None):
        super().__init__(parent)
        self.firma_id = firma_id
        self.pracownik_id = pracownik_id
        self.note = note

        self.setWindowTitle("Edytuj notatkę" if note else "Dodaj notatkę")
        self.resize(600, 400)

        layout = QVBoxLayout(self)

        self.text = QTextEdit()
        if note:
            self.text.setPlainText(note["tresc"])
        layout.addWidget(self.text)

        btns = QHBoxLayout()
        btn_ok = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_ok.clicked.connect(self._save)
        btn_cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)

        layout.addLayout(btns)

    def _save(self):
        tresc = self.text.toPlainText().strip()
        if not tresc:
            QMessageBox.warning(self, "Błąd", "Treść notatki nie może być pusta.")
            return

        if self.note:
            update_company_note(self.note["id"], tresc)
        else:
            save_company_note(self.firma_id, tresc, self.pracownik_id)

        self.accept()
