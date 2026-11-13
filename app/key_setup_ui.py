"""Explicit shared-key choice on the first desktop launch."""
from pathlib import Path
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
                             QFileDialog, QMessageBox)
from app.utils.env_config import install_encryption_key


class KeySetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Biuro — pierwszy start: wspólny klucz szyfrowania')
        self.resize(580, 300)
        layout = QVBoxLayout(self)
        note = QLabel('Istniejące biuro: wybierz klucz wyeksportowany z działającego komputera. '
                      'Nowe biuro: wygeneruj jeden klucz i przekaż go wszystkim stanowiskom oraz workerowi. '
                      'Nowy klucz nie odszyfruje haseł zapisanych wcześniej.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('Wspólny BIO_ENCRYPTION_KEY')
        layout.addWidget(self.key)
        for label, action in [('Wybierz klucz z pliku', self.choose),
                              ('Wygeneruj klucz dla nowego biura', self.generate),
                              ('Zapisz klucz i kontynuuj', self.save)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Wybierz wspólny klucz', '', 'Klucz (*.key);;Wszystkie pliki (*)')
        if path:
            try:
                self.key.setText(Path(path).read_text(encoding='utf-8').strip())
            except OSError:
                QMessageBox.warning(self, 'Klucz', 'Nie udało się odczytać pliku klucza.')

    def generate(self):
        if QMessageBox.question(self, 'Nowe biuro', 'Wygenerować nowy klucz? '
                'Użyj tej opcji tylko wtedy, gdy nie masz jeszcze haseł zaszyfrowanych w Biuro.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            from cryptography.fernet import Fernet
            self.key.setText(Fernet.generate_key().decode())

    def save(self):
        try:
            install_encryption_key(self.key.text())
            self.key.clear()
            self.accept()
        except Exception:
            QMessageBox.warning(self, 'Klucz', 'Nie zapisano klucza. Sprawdź poprawność klucza i uprawnienia do folderu Biuro.')
