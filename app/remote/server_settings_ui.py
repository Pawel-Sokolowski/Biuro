"""Local-only HTTPS service provisioning, beside the existing worker settings."""
import os
import ssl
from pathlib import Path
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QLabel,QLineEdit,QSpinBox,QCheckBox,QPushButton,QMessageBox


class ServerSettings(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        from app.remote.state import client
        if client() is not None:
            raise PermissionError('Konfigurację usługi wykonaj lokalnie na serwerze.')
        layout = QVBoxLayout(self)
        note = QLabel('Stanowiska pracowników łączą się z usługą biura. Hasła usług, baza i klucz szyfrowania '
                      'pozostają na serwerze. Użyj certyfikatu dla nazwy serwera, zaufanego na komputerach biura. '
                      'Zapisz ustawienia, a następnie uruchom ponownie worker.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.enabled = QCheckBox('Udostępniaj usługę biura przez worker')
        self.enabled.setChecked(os.getenv('BIO_API_ENABLED')=='1')
        self.allow_http = QCheckBox('Tymczasowo używaj HTTP bez certyfikatu')
        self.allow_http.setChecked(os.getenv('BIO_API_ALLOW_HTTP')=='1')
        self.allow_http.setToolTip('HTTP przesyła hasła, sesje i dane bez szyfrowania transportowego.')
        layout.addWidget(self.allow_http)
        self.bind = QLineEdit(os.getenv('BIO_API_BIND','0.0.0.0'))
        self.port = QSpinBox()
        self.port.setRange(1,65535)
        self.port.setValue(int(os.getenv('BIO_API_PORT','8443')))
        self.certificate = QLineEdit(os.getenv('BIO_API_CERT',''))
        self.private_key = QLineEdit(os.getenv('BIO_API_TLS_KEY',''))
        self.private_key.setPlaceholderText('Ścieżka do prywatnego pliku PEM na serwerze')
        for label,widget in [('Usługa',self.enabled),('Adres nasłuchu',self.bind),('Port usługi',self.port),
                             ('Certyfikat PEM',self.certificate),('Klucz TLS PEM',self.private_key)]:
            form.addRow(label,widget)
        def update_tls_fields():
            self.certificate.setEnabled(not self.allow_http.isChecked())
            self.private_key.setEnabled(not self.allow_http.isChecked())
        self.allow_http.toggled.connect(update_tls_fields)
        update_tls_fields()
        layout.addLayout(form)
        save = QPushButton('Zapisz ustawienia na serwerze')
        save.clicked.connect(self.save)
        layout.addWidget(save)
        layout.addStretch()

    def save(self):
        try:
            certificate,key = self.certificate.text().strip(),self.private_key.text().strip()
            if self.enabled.isChecked() and not self.allow_http.isChecked():
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(certificate,key)
            from app.services.worker_setup import save_local_config
            from app.config import get_dsn
            from app.utils.env_config import ensure_encryption_key
            values = {'BIO_API_ALLOW_HTTP':'1' if self.allow_http.isChecked() else '0',
                      'BIO_API_ENABLED':'1' if self.enabled.isChecked() else '0',
                      'BIO_API_BIND':self.bind.text().strip(),'BIO_API_PORT':str(self.port.value()),
                      'BIO_API_CERT':str(Path(certificate).resolve()) if certificate else '',
                      'BIO_API_TLS_KEY':str(Path(key).resolve()) if key else ''}
            if not values['BIO_API_BIND'] or any('\n' in value or '\r' in value for value in values.values()):
                raise ValueError('Nieprawidłowa konfiguracja nasłuchu.')
            save_local_config(get_dsn(),ensure_encryption_key(),api_settings=values)
        except (ValueError,OSError,ssl.SSLError):
            QMessageBox.warning(self,'Usługa biura','Nie zapisano. Sprawdź adres oraz poprawność i uprawnienia plików certyfikatu i klucza TLS.')
            return
        scheme = 'http' if self.allow_http.isChecked() else 'https'
        QMessageBox.information(self,'Usługa biura','Zapisano na serwerze. Uruchom ponownie worker. Stanowiskom podaj adres '+scheme+'://nazwa-serwera:'+str(self.port.value()))
