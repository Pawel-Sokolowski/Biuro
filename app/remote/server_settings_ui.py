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
        note = QLabel('Stanowiska pracowników łączą się przez HTTPS. Hasła usług, baza i klucz szyfrowania '
                      'pozostają na serwerze. Użyj certyfikatu dla nazwy serwera, zaufanego na komputerach biura. '
                      'Zapisz ustawienia, a następnie uruchom ponownie worker.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.enabled = QCheckBox('Udostępniaj usługę biura przez worker')
        self.enabled.setChecked(os.getenv('BIO_API_ENABLED')=='1')
        self.bind = QLineEdit(os.getenv('BIO_API_BIND','0.0.0.0'))
        self.port = QSpinBox()
        self.port.setRange(1,65535)
        self.port.setValue(int(os.getenv('BIO_API_PORT','8443')))
        self.certificate = QLineEdit(os.getenv('BIO_API_CERT',''))
        self.private_key = QLineEdit(os.getenv('BIO_API_TLS_KEY',''))
        self.private_key.setPlaceholderText('Ścieżka do prywatnego pliku PEM na serwerze')
        self.lan_http = QCheckBox('Zezwól na HTTP tylko w sieci lokalnej (bez certyfikatu)')
        self.lan_http.setChecked(os.getenv('BIO_API_ALLOW_PLAIN_HTTP')=='1')
        self.lan_http.setToolTip('Bez certyfikatu całe połączenie, łącznie z hasłami, jest jawne. Dozwolone '
                                 'wyłącznie na adresie LAN serwera, np. 192.168.1.116 — nie na 0.0.0.0. '
                                 'Do pracy zdalnej używaj HTTPS.')
        for label,widget in [('Usługa',self.enabled),('Adres nasłuchu',self.bind),('Port HTTPS',self.port),
                             ('Certyfikat PEM',self.certificate),('Klucz TLS PEM',self.private_key),
                             ('Sieć lokalna',self.lan_http)]:
            form.addRow(label,widget)
        layout.addLayout(form)
        save = QPushButton('Sprawdź certyfikat i zapisz na serwerze')
        save.clicked.connect(self.save)
        layout.addWidget(save)
        layout.addStretch()

    def save(self):
        try:
            certificate,key = self.certificate.text().strip(),self.private_key.text().strip()
            plain = self.lan_http.isChecked()
            bind = self.bind.text().strip()
            if plain and (certificate or key):
                raise ValueError('Certyfikat jest już ustawiony. Wyłącz próbę HTTP albo usuń ścieżki certyfikatu.')
            if plain:
                from app.remote.http_server import _private_bind
                if not _private_bind(bind):
                    raise ValueError('HTTP bez certyfikatu działa tylko w sieci lokalnej. Ustaw adres nasłuchu '
                                     'na adres LAN serwera (np. 192.168.1.116), nie 0.0.0.0.')
            elif self.enabled.isChecked():
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(certificate,key)
            from app.services.worker_setup import save_local_config
            from app.config import get_dsn
            from app.utils.env_config import ensure_encryption_key
            values = {'BIO_API_ENABLED':'1' if self.enabled.isChecked() else '0',
                      'BIO_API_BIND':bind,'BIO_API_PORT':str(self.port.value()),
                      'BIO_API_CERT':str(Path(certificate).resolve()) if certificate else '',
                      'BIO_API_TLS_KEY':str(Path(key).resolve()) if key else '',
                      'BIO_API_ALLOW_PLAIN_HTTP':'1' if plain else '0'}
            if not values['BIO_API_BIND'] or any('\n' in value or '\r' in value for value in values.values()):
                raise ValueError('Nieprawidłowa konfiguracja nasłuchu HTTPS.')
            save_local_config(get_dsn(),ensure_encryption_key(),api_settings=values)
        except (ValueError,OSError,ssl.SSLError):
            QMessageBox.warning(self,'Usługa biura','Nie zapisano. Sprawdź adres oraz poprawność i uprawnienia plików certyfikatu i klucza TLS.')
            return
        if plain:
            QMessageBox.information(self,'Usługa biura','Zapisano: HTTP tylko w sieci lokalnej, bez szyfrowania. '
                                    'Uruchom ponownie worker. Stanowiskom podaj adres http://' + bind
                                    + ':' + str(self.port.value()))
            return
        QMessageBox.information(self,'Usługa biura','Zapisano na serwerze. Uruchom ponownie worker. Stanowiskom podaj adres https://nazwa-serwera:'+str(self.port.value()))
