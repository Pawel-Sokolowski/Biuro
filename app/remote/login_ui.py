"""Employee connection UI: server address, employee login, optional OS-held session."""
import os
from pathlib import Path
import sys
from PyQt6.QtCore import QSettings,QTimer
from PyQt6.QtWidgets import (QCheckBox,QDialog,QFormLayout,QHBoxLayout,QLabel,QLineEdit,
                             QMessageBox,QPushButton,QVBoxLayout,QInputDialog)
from app.remote.client import OfficeClient,normalize_server
from app.remote import credential_store
from app.remote.state import configure
from app.utils.background import CommandRunner


def _initial_server_address(settings: QSettings) -> str:
    saved = settings.value('office_server')
    if saved is not None:
        return str(saved)
    configured = os.getenv('BIO_SERVER_URL')
    if configured or not getattr(sys, 'frozen', False):
        return configured or ''
    installed_path = Path(sys.executable).parent / 'office-server.ini'
    defaults = QSettings(str(installed_path), QSettings.Format.IniFormat)
    address = defaults.value('Office/ServerUrl', '', type=str)
    try:
        return normalize_server(address) if address else ''
    except ValueError:
        return ''


class ServerLoginDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle('Biuro — połączenie z serwerem')
        self.setMinimumWidth(490)
        self.pracownik_id = None
        self.connection = None
        self.runner = CommandRunner(self)
        self.settings = QSettings('Biuro','OfficeManager')
        # Retire the legacy saved-password mechanism without ever decrypting it.
        self.settings.remove('saved_password')
        layout = QVBoxLayout(self)
        intro = QLabel('Połącz się z serwerem swojego biura. Dane dostępu do usług pozostają na serwerze.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.server = QLineEdit(_initial_server_address(self.settings))
        self.server.setPlaceholderText('https://serwer.biura:8443')
        self.email = QLineEdit(str(self.settings.value('saved_email','') or ''))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox('Zapamiętaj logowanie na tym komputerze')
        self.remember.setEnabled(os.name=='nt')
        self.remember.setChecked(os.name=='nt')
        if os.name!='nt':
            self.remember.setToolTip('Zapamiętywanie sesji wymaga Menedżera poświadczeń Windows.')
        form.addRow('Serwer biura:',self.server)
        form.addRow('E-mail:',self.email)
        form.addRow('Hasło:',self.password)
        form.addRow(self.remember)
        layout.addLayout(form)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.connect_button = QPushButton('Połącz i zaloguj')
        self.connect_button.clicked.connect(self.connect_office)
        close = QPushButton('Zamknij')
        close.clicked.connect(self.reject)
        actions.addWidget(self.connect_button)
        actions.addWidget(close)
        layout.addLayout(actions)
        reset = QPushButton('Nie pamiętam hasła / mam kod resetowania')
        reset.clicked.connect(self.reset_password)
        layout.addWidget(reset)
        self.password.returnPressed.connect(self.connect_office)
        if self.server.text():
            QTimer.singleShot(0,self.resume)

    def busy(self,value):
        for widget in (self.server,self.email,self.password,self.connect_button,self.remember):
            widget.setEnabled(not value and (widget is not self.remember or os.name=='nt'))

    def resume(self):
        try:
            server = normalize_server(self.server.text())
            token = credential_store.read(server)
        except (ValueError,OSError):
            return
        if not token:
            return
        self.busy(True)
        self.status.setText('Łączenie z serwerem biura…')
        connection = OfficeClient(server)
        def work():
            connection.resume(token)
            return connection
        self.runner.submit(work,lambda result:self.connected(result,False),self.resume_failed)

    def resume_failed(self,error):
        self.busy(False)
        self.status.setText('Zaloguj się ponownie, aby połączyć się z biurem.')
        if isinstance(error,PermissionError):
            try:
                credential_store.delete(normalize_server(self.server.text()))
            except (ValueError,OSError):
                pass

    def connect_office(self):
        try:
            connection = OfficeClient(self.server.text())
            email,password,remember = self.email.text().strip(),self.password.text(),self.remember.isChecked()
            if not email or not password:
                raise ValueError('Podaj e-mail i hasło pracownika.')
        except ValueError as error:
            QMessageBox.warning(self,'Logowanie',str(error))
            return
        self.busy(True)
        self.status.setText('Sprawdzanie logowania na serwerze…')
        def work():
            connection.login(email,password,remember)
            return connection
        self.runner.submit(work,lambda result:self.connected(result,True),self.failed)

    def connected(self,connection,store):
        self.connection = connection
        self.pracownik_id = connection.employee_id
        self.settings.setValue('office_server',connection.server)
        self.settings.setValue('saved_email',self.email.text().strip())
        if store:
            try:
                if self.remember.isChecked():
                    credential_store.save(connection.server,connection.token)
                else:
                    credential_store.delete(connection.server)
            except OSError:
                QMessageBox.information(self,'Logowanie','Połączono. Windows nie pozwolił zapamiętać sesji; po zamknięciu programu zaloguj się ponownie.')
        self.settings.sync()
        self.password.clear()
        configure(connection)
        self.accept()

    def reset_password(self):
        try:
            connection = OfficeClient(self.server.text())
            email = self.email.text().strip()
            if not email:
                raise ValueError('Najpierw wpisz adres e-mail pracownika.')
        except ValueError as error:
            QMessageBox.warning(self,'Reset hasła',str(error))
            return
        action,ok = QInputDialog.getItem(self,'Reset hasła','Wybierz:',
                                        ['Wyślij kod na e-mail','Mam kod — ustaw nowe hasło'],0,False)
        if not ok:
            return
        payload = {'email':email}
        route = 'reset'
        if action.startswith('Mam kod'):
            code,ok = QInputDialog.getText(self,'Kod resetowania','Jednorazowy kod z wiadomości:')
            if not ok:
                return
            password,ok = QInputDialog.getText(self,'Nowe hasło','Co najmniej 10 znaków:',QLineEdit.EchoMode.Password)
            if not ok:
                return
            route = 'redeem'
            payload.update(code=code.strip(),password=password)
        self.busy(True)
        def done(result):
            self.busy(False)
            self.status.setText(result['message'])
        self.runner.submit(lambda:connection._request(route,payload,authenticated=False),done,self.failed)

    def failed(self,error):
        self.busy(False)
        self.status.setText(str(error) if isinstance(error,(ValueError,PermissionError)) else 'Nie udało się połączyć z serwerem biura.')
