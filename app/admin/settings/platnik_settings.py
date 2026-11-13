"""Separate read-only Płatnik SQL connection, shared with the server worker."""
from datetime import datetime
from zoneinfo import ZoneInfo
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QLineEdit,QCheckBox,
    QComboBox,QPushButton,QLabel,QSpinBox,QMessageBox,QProgressBar)
from PyQt6.QtCore import Qt,pyqtSignal
from app.integrations.platnik.holidays import load,save,check_connection,import_year,PlatnikSqlError
from app.integrations.enova.odbc_support import SUPPORTED_DRIVERS
from app.utils.background import TaskRunner


class PlatnikSettings(QWidget):
    import_progress = pyqtSignal(int,str)

    def __init__(self,actor_id,parent=None):
        super().__init__(parent)
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Tylko administrator konfiguruje Płatnika.')
        self.actor_id = actor_id
        self.runner = TaskRunner(self)
        self.error_diagnostic = None
        self._import_token = 0
        self._import_running = False
        self.import_progress.connect(self._show_import_progress)
        config = load()
        layout = QVBoxLayout(self)
        note = QLabel('Osobne połączenie Microsoft SQL Server programu Płatnik. '
                      'Podaj instancję, np. platnik-srv\\SQLEXPRESS, bazę platnik i użytkownika SQL z prawem SELECT. '
                      'To nie konto PostgreSQL ani login użytkownika Płatnika. '
                      'Adapter korzysta ze standardowej tabeli wakacji składkowych z Waszego eksportu; nie wymaga własnego pliku SQL.')
        note.setWordWrap(True)
        layout.addWidget(note)
        driver_note = QLabel('Sterownik ODBC 17 lub 18 musi być zainstalowany na komputerze workera. '
                             'Obsługują SQL Server 2017, 2019 i 2022; numery wersji serwera i sterownika nie muszą być takie same.')
        driver_note.setWordWrap(True)
        layout.addWidget(driver_note)
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
        self.query_timeout = QSpinBox()
        self.query_timeout.setRange(30,600)
        self.query_timeout.setSuffix(' s')
        self.query_timeout.setValue(config.get('query_timeout',120))
        self.query_timeout.setToolTip('Limit pojedynczego zapytania SQL; logowanie ma osobny limit 15 sekund.')
        form.addRow('Limit zapytania SQL',self.query_timeout)
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
        self.import_bar = QProgressBar()
        self.import_bar.setRange(0,0)
        self.import_bar.hide()
        layout.addWidget(self.import_bar)
        self.buttons = []
        for title,action in (('Zapisz połączenie Płatnika',self.save),
                             ('Sprawdź połączenie SQL (bez importu)',self.check),
                             ('Pobierz wybrany rok — zapisane ustawienia',self.download)):
            button = QPushButton(title)
            button.clicked.connect(action)
            self.buttons.append(button)
            layout.addWidget(button)
        self.error_button = QPushButton('Pokaż szczegóły błędu SQL…')
        self.error_button.clicked.connect(self.show_error_details)
        self.error_button.hide()
        layout.addWidget(self.error_button)
        layout.addStretch()

    def config(self):
        value = dict(load())
        value.update({key:field.text().strip() for key,field in self.fields.items()})
        value.update(driver=self.driver.currentData(),enabled=self.enabled.isChecked(),
                     trust_server_certificate=self.trust.isChecked(),query_timeout=self.query_timeout.value())
        return value

    def save(self):
        try:
            save(self.config(),self.actor_id,self.password.text())
            self.password.clear()
            self.error_diagnostic = None
            self.error_button.hide()
            self.status.setText('Zapisano wspólne połączenie Płatnika. Worker użyje go przy następnym przebiegu zadania.')
        except Exception as error:
            self.failed(error)

    def busy(self,value):
        if value:
            self.error_diagnostic = None
            self.error_button.hide()
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
        self.error_diagnostic = None
        self.error_button.hide()
        self.status.setText('Połączenie SQL Płatnika poprawne. Test nie odczytywał ani nie importował dokumentów.')

    def download(self):
        saved = load()
        config = self.config()
        keys = ('server','database','username','driver','trust_server_certificate','enabled','query_timeout')
        if self.password.text() or any(config.get(key)!=saved.get(key) for key in keys):
            self.failed(ValueError('Masz niezapisane zmiany połączenia. Kliknij „Zapisz połączenie Płatnika”, a następnie pobierz rok. Test SQL używa pól formularza; import używa ustawień zapisanych.'))
            return
        year = self.year.value()
        self._import_token += 1
        token = self._import_token
        self._import_running = True
        self.import_bar.show()
        self.busy(True)
        self.status.setText(f'Odczyt roku {year} z Płatnika…')
        def progress(message):
            try:
                self.import_progress.emit(token,message)
            except RuntimeError:
                pass
        self.runner.submit(lambda:import_year(year,self.actor_id,progress=progress),self.downloaded,self.failed)

    def _show_import_progress(self,token,message):
        if self._import_running and token==self._import_token:
            self.status.setText(message)

    def downloaded(self,result):
        self._import_running = False
        self.import_bar.hide()
        self.busy(False)
        if result.get('mode')=='disabled':
            self.status.setText('Odczyt wyłączony. Włącz i zapisz połączenie Płatnika.')
        elif result.get('mode')=='busy':
            self.status.setText('Ten rok jest już pobierany przez inne zadanie. Odśwież zestawienie po zakończeniu.')
        else:
            self.status.setText(f"Zapisano rok {result['year']}; wpisy RWS: {result['rows']}. Zestawienie jest dostępne w Kadry → Sprawdzanie.")

    def failed(self,error):
        self._import_running = False
        self.import_bar.hide()
        self.busy(False)
        self.error_diagnostic = error.diagnostic if isinstance(error,PlatnikSqlError) else None
        self.error_button.setVisible(self.error_diagnostic is not None)
        if self.error_diagnostic:
            self.status.setText(str(error)+' Rozwiń szczegóły błędu SQL. Poprzednie dane pozostawiono.')
            return
        self.status.setText(str(error) if isinstance(error,(ValueError,PermissionError)) else
                            'Operacja nie powiodła się. Sprawdź instancję, bazę platnik, konto SQL, sterownik, certyfikat i wspólny klucz. '
                            'Nie nadpisano poprzednich danych błędnym odczytem.')

    def show_error_details(self):
        if not self.error_diagnostic:
            return
        error = self.error_diagnostic
        box = QMessageBox(self)
        box.setWindowTitle('Płatnik — szczegóły błędu SQL')
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f"{error['operation']} [{error['sqlstate'] or 'brak SQLSTATE'}]\n{error['hint']}")
        box.setDetailedText(f"Serwer / instancja: {error['server']}\nBaza: {error['database']}\n"
                            f"Sterownik: {error['driver']}\n\n{error['details']}")
        box.exec()
