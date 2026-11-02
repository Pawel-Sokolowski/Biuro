from __future__ import annotations

import base64
from typing import Callable, Tuple
from PyQt6.QtCore import QBuffer, QIODevice, Qt
from PyQt6.QtGui import QImage, QTextImageFormat


from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from app.email import signature as sig


class SignatureTextEdit(QTextEdit):
    """Store pasted/file images inside HTML, never as machine-local file paths."""
    def insert_image(self, image):
        if not isinstance(image, QImage):
            image = image.toImage()
        if image.isNull():
            raise ValueError("Nie udało się odczytać obrazu.")
        if image.width() > 800 or image.height() > 400:
            image = image.scaled(800, 400, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise ValueError("Nie udało się zapisać obrazu.")
        raw = bytes(buffer.data())
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Obraz jest zbyt duży (maks. 2 MB).")
        url = 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')
        fmt = QTextImageFormat()
        fmt.setName(url)
        fmt.setWidth(min(image.width(), 400))
        fmt.setHeight(image.height() * min(image.width(), 400) / image.width())
        self.textCursor().insertImage(fmt)

    def canInsertFromMimeData(self, source):
        return source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage():
            try:
                self.insert_image(source.imageData())
            except (ValueError, AttributeError) as exc:
                QMessageBox.warning(self, "Obraz stopki", str(exc))
        else:
            super().insertFromMimeData(source)


class SignatureEditor(QWidget):
    """Edytor stopki (tekst sformatowany) z importem z pliku i wstawianiem szablonu domyślnego."""

    def __init__(self, name_provider: Callable[[], Tuple[str, str]], parent=None):
        super().__init__(parent)
        self._name_provider = name_provider
        self.edit = SignatureTextEdit()
        self.edit.setAcceptRichText(True)
        self.edit.setMinimumHeight(200)

        btn_import = QPushButton("Importuj z pliku")
        btn_import.clicked.connect(self.import_from_file)
        btn_default = QPushButton("Wstaw domyślną")
        btn_default.clicked.connect(self.insert_default)
        btn_clear = QPushButton("Wyczyść")
        btn_clear.clicked.connect(self.edit.clear)

        row = QHBoxLayout()
        btn_image = QPushButton("Dodaj zdjęcie / logo…")
        btn_image.clicked.connect(self.add_image)
        row.addWidget(btn_image)
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

    def add_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Dodaj zdjęcie lub logo", "", "Obrazy (*.png *.jpg *.jpeg *.gif *.webp *.bmp)")
        if path:
            try:
                self.edit.insert_image(QImage(path))
            except ValueError as exc:
                QMessageBox.warning(self, "Obraz stopki", str(exc))

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
        text, html = sig.build_default_signature(imie, nazwisko, template=sig.DEFAULT_TEMPLATE, telefon="{telefon}")
        self.set_signature(html, text)


class SignatureDialog(QDialog):
    """Personal phone and a preview of the shared signature template."""
    def __init__(self, pracownik_id: int, imie: str, nazwisko: str, parent=None):
        super().__init__(parent)
        from app.db.dao_employees import get_employee_info
        self.pracownik_id = pracownik_id
        info = get_employee_info(pracownik_id) or {}
        self.name = (info.get('imie', imie), info.get('nazwisko', nazwisko))
        self.template = sig.get_template()
        self.setWindowTitle("Moja stopka")
        self.resize(620, 500)
        layout = QVBoxLayout(self)
        info_label = QLabel("Wspólny wzór biura jest uzupełniany Twoim imieniem, nazwiskiem i telefonem. "
                            "Stopka jest widoczna podczas pisania wiadomości. Wzór i zdjęcia zmienia administrator w ustawieniach poczty.")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        form = QFormLayout()
        form.addRow("Autor wiadomości:", QLabel(' '.join(self.name)))
        self.phone = QLineEdit(info.get('telefon') or '')
        form.addRow("Twój numer telefonu:", self.phone)
        layout.addLayout(form)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.phone.textChanged.connect(self._preview)
        self._preview()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Zapisz")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _preview(self):
        _, html = sig.build_default_signature(*self.name, self.template, telefon=self.phone.text().strip())
        self.preview.setHtml(html)

    def _save(self):
        sig.save_employee_phone(self.pracownik_id, self.phone.text())
        self.accept()
