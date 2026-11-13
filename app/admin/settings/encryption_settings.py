"""Explicit local provisioning of one office key; the key never enters PostgreSQL."""
import hashlib
import os
from pathlib import Path
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton,
                             QMessageBox, QFileDialog)
from app.utils.env_config import KEY_NAME, install_encryption_key, active_env_path


class EncryptionSettings(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Tylko administrator może konfigurować klucz.')
        layout = QVBoxLayout(self)
        info = QLabel('Wszystkie komputery i worker muszą używać tego samego klucza. '
                      'Na działającym komputerze wyeksportuj klucz, a następnie zaimportuj go na pozostałych. '
                      'Plik klucza przekazuj bezpiecznie; nie wysyłaj go w czacie. '
                      'Zmiana klucza lokalnego nie szyfruje ponownie danych zapisanych innymi kluczami.')
        info.setWordWrap(True)
        layout.addWidget(info)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('Wspólny klucz BIO_ENCRYPTION_KEY')
        layout.addWidget(self.key)
        for label, action in [('Importuj klucz z pliku', self.import_key),
                              ('Zapisz klucz na tym komputerze', self.save),
                              ('Eksportuj obecny klucz do pliku', self.export),
                              ('Utwórz klucz dla nowego biura', self.create)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
        layout.addStretch()
        self.refresh()

    def refresh(self):
        key = os.environ.get(KEY_NAME, '')
        fingerprint = hashlib.sha256(key.encode()).hexdigest()[:16] if key else 'brak'
        self.status.setText(f'Odcisk klucza: {fingerprint}\nPlik ustawień: {active_env_path()}')

    def import_key(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Importuj wspólny klucz', '', 'Klucz (*.key);;Wszystkie pliki (*)')
        if path:
            try:
                self.key.setText(Path(path).read_text(encoding='utf-8').strip())
                self.save()
            except Exception:
                QMessageBox.warning(self, 'Klucz', 'Nie udało się zaimportować pliku klucza.')

    def save(self):
        from cryptography.fernet import Fernet, InvalidToken
        from app.db.dao_conn import get_conn
        candidate = self.key.text().strip()
        try:
            cipher = Fernet(candidate.encode())
            with get_conn() as conn:
                tokens = [row[0] for row in conn.execute("SELECT haslo_enc FROM konta_email WHERE NULLIF(haslo_enc,'') IS NOT NULL").fetchall()]
            readable = 0
            for token in tokens:
                try:
                    cipher.decrypt(token.encode())
                    readable += 1
                except InvalidToken:
                    pass
            if tokens and not readable:
                raise ValueError('Ten klucz nie odczytuje żadnego zapisanego hasła poczty. Użyj klucza z działającego komputera.')
            install_encryption_key(candidate)
        except Exception as exc:
            QMessageBox.warning(self, 'Klucz', str(exc) if isinstance(exc, ValueError) else 'Nie udało się zapisać klucza.')
            return
        self.key.clear()
        self.refresh()
        message = 'Zapisano wspólny klucz na tym komputerze. Uruchom ponownie aplikację.'
        if readable < len(tokens):
            message += ' Część haseł używa innych kluczy; wpisz je ponownie po ujednoliceniu kluczy.'
        QMessageBox.information(self, 'Klucz', message)

    def export(self):
        from app.utils.env_config import ensure_encryption_key
        try:
            key = ensure_encryption_key()
            path, _ = QFileDialog.getSaveFileName(self, 'Eksportuj wspólny klucz', 'biuro.key', 'Klucz (*.key)')
            if path:
                Path(path).write_text(key + '\n', encoding='utf-8')
                Path(path).chmod(0o600)
        except Exception:
            QMessageBox.warning(self, 'Klucz', 'Nie udało się wyeksportować klucza.')

    def create(self):
        from app.db.dao_conn import get_conn
        from cryptography.fernet import Fernet
        with get_conn() as conn:
            occupied = conn.execute("SELECT 1 FROM konta_email WHERE NULLIF(haslo_enc,'') IS NOT NULL LIMIT 1").fetchone()
            other_secrets = conn.execute("SELECT 1 FROM app_settings WHERE klucz IN ('ezla_service','enova_connection','ksef_cert_pem_enc','ksef_key_pem_enc','ksef_token_enc','google_shared_refresh_token','google_client_secret','ksef_key_pass_enc','caldav_haslo_enc') OR klucz LIKE 'ezla_report_%' LIMIT 1").fetchone()
        if occupied or other_secrets or os.environ.get(KEY_NAME):
            QMessageBox.warning(self, 'Klucz', 'Biuro ma już klucz lub zapisane sekrety. Zaimportuj istniejący klucz zamiast tworzyć nowy.')
            return
        self.key.setText(Fernet.generate_key().decode())
        self.save()
