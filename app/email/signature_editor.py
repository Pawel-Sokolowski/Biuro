from __future__ import annotations

from typing import Callable, Optional, Tuple

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from app.email import signature as sig


class SignatureEditor(QWidget):
    """Edytor stopki (tekst sformatowany) z importem z pliku i wstawianiem szablonu domyślnego."""

    def __init__(self, name_provider: Callable[[], Tuple[str, str]], parent=None):
        super().__init__(parent)
        self._name_provider = name_provider
        self.edit = QTextEdit()
        self.edit.setAcceptRichText(True)
        self.edit.setMinimumHeight(200)

        btn_import = QPushButton("Importuj z pliku")
        btn_import.clicked.connect(self.import_from_file)
        btn_default = QPushButton("Wstaw domyślną")
        btn_default.clicked.connect(self.insert_default)
        btn_clear = QPushButton("Wyczyść")
        btn_clear.clicked.connect(self.edit.clear)

        row = QHBoxLayout()
        row.addWidget(btn_import)
        row.addWidget(btn_default)
        row.addWidget(btn_clear)
        row.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)
        layout.addLayout(row)

    def set_signature(self, html: str, text: str = "") -> None:
        if html.strip():
            self.edit.setHtml(html)
        else:
            self.edit.setPlainText(text)

    def signature(self) -> Tuple[str, str]:
        """(tekst, html); puste pole daje dwa puste łańcuchy."""
        text = self.edit.toPlainText().strip()
        return (text, self.edit.toHtml()) if text else ("", "")

    def import_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importuj stopkę", "", "Stopka (*.html *.htm *.txt);;Wszystkie pliki (*)"
        )
        if not path:
            return
        try:
            text, html = sig.load_signature_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "Stopka", f"Nie udało się wczytać pliku:\n{exc}")
            return
        self.set_signature(html, text)

    def insert_default(self) -> None:
        imie, nazwisko = self._name_provider()
        text, html = sig.build_default_signature(imie, nazwisko)
        self.set_signature(html, text)


class SignatureDialog(QDialog):
    """Okno 'Moja stopka' - pracownik edytuje własną stopkę."""

    def __init__(self, pracownik_id: int, imie: str, nazwisko: str, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.setWindowTitle("Moja stopka")
        self.resize(560, 460)
        self.editor = SignatureEditor(lambda: (imie, nazwisko))
        text, html = sig.get_employee_signature(pracownik_id)
        self.editor.set_signature(html, text)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Zapisz")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Stopka dodawana automatycznie do nowych wiadomości, odpowiedzi i przekazań:"))
        layout.addWidget(self.editor)
        layout.addWidget(buttons)

    def _save(self) -> None:
        text, html = self.editor.signature()
        sig.save_employee_signature(self.pracownik_id, text, html)
        self.accept()
