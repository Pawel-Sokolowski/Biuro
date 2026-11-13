"""Separate read-only Płatnik SQL connection, shared with the server worker."""
from datetime import datetime
from zoneinfo import ZoneInfo
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QLineEdit,QCheckBox,
    QComboBox,QPushButton,QLabel,QSpinBox)
from PyQt6.QtCore import Qt
from app.integrations.platnik.holidays import load,save,check_connection,import_year
from app.integrations.enova.odbc_support import SUPPORTED_DRIVERS
from app.utils.background import TaskRunner


class PlatnikSettings(QWidget):
    def __init__(self,actor_id,parent=None):
        super().__init__(parent)
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Tylko administrator konfiguruje Płatnika.')
        self.actor_id = actor_id
        self.runner = TaskRunner(self)
        config = load()
        layout = QVBoxLayout(self)
        note = QLabel('Osobne połączenie Microsoft SQL Server programu Płatnik. '
                      'Podaj instancję, np. platnik-srv\\SQLEXPRESS, bazę platnik i użytkownika SQL z prawem SELECT. '
                      'To nie konto PostgreSQL ani login użytkownika Płatnika. '
                      'Adapter korzysta ze standardowej tabeli wakacji składkowych z Waszego eksportu; nie wymaga własnego pliku SQL.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.fields = {}
        for key,label in (('server','Serwer / instancja'),('database','Baza Płatnika'),('username','Login SQL')):
            field = QLineEdit(config[key])
            self.fields[key] = field
            form.addRow(label,field)
        self.fields['server'].setPlaceholderText('platnik-srv\\SQLEXPRESS')
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Puste zachowuje zapisane hasło')
        form.addRow('Hasło SQL',self.password)
        self.driver = QComboBox()
        self.driver.addItem('Automatycznie','auto')
        for value in SUPPORTED_DRIVERS:
            self.driver.addItem(value,value)
        self.driver.setCurrentIndex(max(0,self.driver.findData(config['driver'])))
        form.addRow('Sterownik SQL Server',self.driver)
        self.trust = QCheckBox('Ufaj własnemu certyfikatowi serwera (połączenie pozostaje szyfrowane)')
        self.trust.setChecked(config['trust_server_certificate'])
        form.addRow(self.trust)
        self.enabled = QCheckBox('Włącz odczyt wakacji składkowych z Płatnika')
        self.enabled.setChecked(config['enabled'])
        form.addRow(self.enabled)
        self.year = QSpinBox()
        self.year.setRange(2024,2100)
        self.year.setValue(datetime.now(ZoneInfo('Europe/Warsaw')).year)
        form.addRow('Rok do pobrania teraz',self.year)
        layout.addLayout(form)
        self.status = QLabel('Worker odczytuje bieżący i poprzedni rok. Włącz również zadanie Płatnik w zakładce Zadania. '
                             'Zestawienie: Kadry → Sprawdzanie → Wakacje składkowe. '
                             'Odczyt nie wysyła dokumentów do ZUS i nie zmienia bazy Płatnika.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.buttons = []
        for title,action in (('Zapisz połączenie Płatnika',self.save),
                             ('Sprawdź połączenie SQL (bez importu)',self.check),
                             ('Pobierz wybrany rok — zapisane ustawienia',self.download)):
            button = QPushButton(title)
            button.clicked.connect(action)
            self.buttons.append(button)
            layout.addWidget(button)
        layout.addStretch()

    def config(self):
        value = load()
        value.update({key:field.text().strip() for key,field in self.fields.items()})
        value.update(driver=self.driver.currentData(),enabled=self.enabled.isChecked(),
                     trust_server_certificate=self.trust.isChecked())
        return value

    def save(self):
        try:
            save(self.config(),self.actor_id,self.password.text())
            self.password.clear()
            self.status.setText('Zapisano wspólne połączenie Płatnika. Worker użyje go przy następnym przebiegu zadania.')
        except Exception as error:
            self.failed(error)

    def busy(self,value):
        for button in self.buttons:
            button.setEnabled(not value)

    def check(self):
        try:
            from app.utils.crypto import encrypt_password
            from app.security.authorization import is_administrator
            config = self.config()
            if self.password.text():
                config['password_enc'] = encrypt_password(self.password.text())
            def work():
                if not is_administrator(self.actor_id):
                    raise PermissionError('Utracono uprawnienia administratora.')
                return check_connection(config)
            self.busy(True)
            self.runner.submit(work,self.checked,self.failed)
        except Exception as error:
            self.failed(error)

    def checked(self,_):
        self.busy(False)
        self.status.setText('Połączenie SQL Płatnika poprawne. Test nie odczytywał ani nie importował dokumentów.')

    def download(self):
        year = self.year.value()
        self.busy(True)
        self.status.setText(f'Odczyt roku {year} z Płatnika…')
        self.runner.submit(lambda:import_year(year,self.actor_id),self.downloaded,self.failed)

    def downloaded(self,result):
        self.busy(False)
        if result.get('mode')=='disabled':
            self.status.setText('Odczyt wyłączony. Włącz i zapisz połączenie Płatnika.')
        elif result.get('mode')=='busy':
            self.status.setText('Ten rok jest już pobierany przez inne zadanie. Odśwież zestawienie po zakończeniu.')
        else:
            self.status.setText(f"Zapisano rok {result['year']}; wpisy RWS: {result['rows']}. Zestawienie jest dostępne w Kadry → Sprawdzanie.")

    def failed(self,error):
        self.busy(False)
        self.status.setText(str(error) if isinstance(error,(ValueError,PermissionError)) else
                            'Operacja nie powiodła się. Sprawdź instancję, bazę platnik, konto SQL, sterownik, certyfikat i wspólny klucz. '
                            'Nie nadpisano poprzednich danych błędnym odczytem.')
