# app/admin/settings/ksef_settings.py

from pathlib import Path

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.db.dao_app_settings import get_setting
from app.invoices import ksef_credentials as creds
from app.invoices.ksef_client import ENVIRONMENTS
from app.invoices.ksef_sync import ENV_KEY, NIP_KEY, TOKEN_KEY, save_ksef_credentials

ENV_LABELS = {"prod": "Produkcyjne", "demo": "Demo", "test": "Testowe"}


class KsefSettings(QWidget):
    """Dane dostępowe KSeF: NIP, token, środowisko oraz import certyfikatu i klucza."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("KSeF – dane dostępowe")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        form = QFormLayout()
        self.nip = QLineEdit(get_setting(NIP_KEY) or "")
        self.env = QComboBox()
        for key in ENVIRONMENTS:
            self.env.addItem(ENV_LABELS.get(key, key), key)
        idx = self.env.findData(get_setting(ENV_KEY) or "prod")
        self.env.setCurrentIndex(max(idx, 0))
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText(
            "Token zapisany – wpisz nowy, aby zmienić" if get_setting(TOKEN_KEY) else "Token KSeF"
        )
        form.addRow("NIP:", self.nip)
        form.addRow("Środowisko:", self.env)
        form.addRow("Token KSeF:", self.token)
        layout.addLayout(form)

        btn_save = QPushButton("Zapisz NIP, środowisko i token")
        btn_save.clicked.connect(self._save_basic)
        layout.addWidget(btn_save)

        layout.addSpacing(16)
        layout.addWidget(QLabel("<b>Certyfikat i klucz prywatny</b>"))
        self.cert_info = QLabel()
        self.cert_info.setWordWrap(True)
        layout.addWidget(self.cert_info)

        row = QHBoxLayout()
        b_p12 = QPushButton("Importuj PFX / P12…")
        b_p12.clicked.connect(self._import_p12)
        b_pair = QPushButton("Importuj certyfikat + klucz…")
        b_pair.clicked.connect(self._import_pair)
        b_clear = QPushButton("Usuń certyfikat")
        b_clear.clicked.connect(self._clear)
        for b in (b_p12, b_pair, b_clear):
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)

        note = QLabel(
            "Certyfikat i klucz są szyfrowane w bazie. Jeśli certyfikat jest zaimportowany, "
            "logowanie do KSeF odbywa się podpisem XAdES; w przeciwnym razie używany jest token."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

        self._refresh_info()

    def _refresh_info(self):
        try:
            info = creds.get_certificate_info()
        except Exception:
            info = None
        if not info:
            self.cert_info.setText("Brak zaimportowanego certyfikatu.")
            return
        self.cert_info.setText(
            f"Certyfikat: {info['subject']}\nWystawca: {info['issuer']}\n"
            f"Ważny do: {info['valid_to']:%Y-%m-%d}   Klucz prywatny: "
            f"{'tak' if info['has_key'] else 'nie'}"
        )

    def _save_basic(self):
        try:
            save_ksef_credentials(self.nip.text(), self.token.text() or None, self.env.currentData())
        except Exception as e:
            QMessageBox.critical(self, "Błąd", str(e))
            return
        self.token.clear()
        self.token.setPlaceholderText("Token zapisany – wpisz nowy, aby zmienić")
        QMessageBox.information(self, "OK", "Zapisano dane KSeF.")

    def _ask_password(self, label):
        from PyQt6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self, "Hasło", label, QLineEdit.EchoMode.Password
        )
        return ok, text

    def _read(self, caption, flt):
        path, _ = QFileDialog.getOpenFileName(self, caption, "", flt)
        return Path(path).read_bytes() if path else None

    def _import_p12(self):
        data = self._read("Wybierz plik PFX / P12", "Certyfikat (*.pfx *.p12);;Wszystkie pliki (*)")
        if data is None:
            return
        ok, pwd = self._ask_password("Hasło do pliku PFX / P12 (puste, jeśli brak):")
        if ok:
            self._run(lambda: creds.import_pkcs12(data, pwd or None))

    def _import_pair(self):
        cert = self._read("Wybierz certyfikat", "Certyfikat (*.crt *.cer *.pem);;Wszystkie pliki (*)")
        if cert is None:
            return
        key = self._read("Wybierz klucz prywatny", "Klucz (*.key *.pem);;Wszystkie pliki (*)")
        if key is None:
            return
        ok, pwd = self._ask_password("Hasło do klucza prywatnego (puste, jeśli brak):")
        if ok:
            self._run(lambda: creds.import_cert_and_key(cert, key, pwd or None))

    def _run(self, action):
        try:
            action()
        except creds.KsefCertError as e:
            QMessageBox.warning(self, "Import certyfikatu", str(e))
            return
        self._refresh_info()
        QMessageBox.information(self, "OK", "Certyfikat i klucz zaimportowane.")

    def _clear(self):
        if QMessageBox.question(self, "Usuń", "Usunąć certyfikat i klucz?") == QMessageBox.StandardButton.Yes:
            creds.clear_certificate()
            self._refresh_info()
