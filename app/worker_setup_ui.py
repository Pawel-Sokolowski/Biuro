"""First-run connection wizard and focused administration for the server worker."""
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QWidget, QMainWindow, QVBoxLayout, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QPushButton, QFileDialog, QMessageBox,
    QDialogButtonBox, QTabWidget, QScrollArea)
from app.utils.background import TaskRunner
from app.services.worker_setup import CredentialKeyError, database_url, probe_database, save_local_config, preflight, worker_env_path


class ConnectionDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Biuro — konfiguracja połączenia workera')
        self.resize(640, 500)
        self.runner = TaskRunner(self)
        self.tested = None
        self.key_error = None
        layout = QVBoxLayout(self)
        note = QLabel('Połącz worker z istniejącą bazą Biuro. Zaimportuj wspólny klucz z działającego '
                      'komputera: Ustawienia → Klucz szyfrowania → Eksportuj. '
                      'Nie twórz nowego klucza dla serwera. Konfiguracja zostanie zapisana dla bieżącego konta Windows.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        try:
            old = urlsplit(os.getenv('DB_URL', ''))
            port = old.port or 5432
        except ValueError:
            old, port = urlsplit(''), 5432
        self.host = QLineEdit(old.hostname or '')
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(port)
        self.database = QLineEdit(unquote(old.path.lstrip('/')))
        self.username = QLineEdit(unquote(old.username or ''))
        self.password = QLineEdit(unquote(old.password or ''))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.tls = QComboBox()
        for value in ('prefer', 'require', 'verify-full'):
            self.tls.addItem(value, value)
        self.tls.setCurrentIndex(max(0, self.tls.findData(parse_qs(old.query).get('sslmode', ['prefer'])[0])))
        self.key = QLineEdit(os.getenv('BIO_ENCRYPTION_KEY', ''))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        for label, field in (('Adres PostgreSQL', self.host), ('Port', self.port), ('Baza Biuro', self.database),
                             ('Użytkownik PostgreSQL', self.username), ('Hasło PostgreSQL', self.password),
                             ('TLS PostgreSQL', self.tls), ('Wspólny klucz szyfrowania', self.key)):
            form.addRow(label, field)
        layout.addLayout(form)
        key_button = QPushButton('Importuj wspólny klucz z pliku')
        key_button.clicked.connect(self.import_key)
        layout.addWidget(key_button)
        generate = QPushButton('Wygeneruj klucz dla nowego biura')
        generate.clicked.connect(self.generate_key)
        layout.addWidget(generate)
        export = QPushButton('Zapisz kopię klucza do pliku')
        export.clicked.connect(self.export_key)
        layout.addWidget(export)
        self.status = QLabel('Test sprawdza dostęp i wersję bazy. Nie pobiera raportów i nie wysyła wiadomości.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.test_button = QPushButton('Sprawdź połączenie')
        self.test_button.clicked.connect(self.check)
        layout.addWidget(self.test_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return (database_url(self.host.text(), self.port.value(), self.database.text(),
                             self.username.text(), self.password.text(), self.tls.currentData()), self.key.text().strip())

    def import_key(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Wspólny klucz', '', 'Klucz (*.key);;Wszystkie pliki (*)')
        if filename:
            try:
                self.key.setText(Path(filename).read_text(encoding='utf-8').strip())
            except OSError:
                self.status.setText('Nie udało się odczytać pliku klucza.')

    def generate_key(self):
        if QMessageBox.question(self, 'Nowe biuro',
                'Generować nowy wspólny klucz? Tylko dla nowego biura. '
                'Jeżeli w Biuro są już zapisane hasła, wybierz klucz z działającego komputera. '
                'Nowy klucz trzeba przekazać wszystkim stanowiskom.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            from cryptography.fernet import Fernet
            self.key.setText(Fernet.generate_key().decode())
            self.status.setText('Wygenerowano klucz. Zapisz jego kopię w bezpiecznym miejscu i użyj go na wszystkich stanowiskach.')

    def export_key(self):
        from cryptography.fernet import Fernet
        try:
            key = self.key.text().strip()
            Fernet(key.encode())
            filename, _ = QFileDialog.getSaveFileName(self, 'Zapisz kopię wspólnego klucza', 'biuro.key', 'Klucz (*.key)')
            if filename:
                # A new file uses restrictive permissions from the start on POSIX;
                # on Windows keep it in a private folder chosen by the administrator.
                with open(filename, 'w', encoding='utf-8', opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
                    handle.write(key + '\n')
                self.status.setText('Zapisano kopię klucza. Przechowuj ją prywatnie; nie przesyłaj w czacie ani e-mailu.')
        except Exception:
            self.status.setText('Nie udało się zapisać klucza. Wybierz poprawny klucz i dostępny prywatny folder.')

    def check(self):
        try:
            values = self.values()
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.test_button.setEnabled(False)
        self.tested = None
        self.key_error = None
        self.status.setText('Sprawdzanie PostgreSQL…')
        def done(_):
            self.tested = values
            self.test_button.setEnabled(True)
            self.status.setText('Połączenie i wersja bazy poprawne. Zapisz i zaloguj się jako administrator Biuro.')
        self.runner.submit(lambda: probe_database(*values), done, lambda error: self.failed(error, values))

    def failed(self, error, values=None):
        self.test_button.setEnabled(True)
        if isinstance(error, CredentialKeyError):
            self.tested = values
            self.key_error = error
            self.status.setText('Połączenie z bazą działa. ' + str(error) +
                                ' Możesz zapisać konfigurację i zalogować się do naprawy haseł; '
                                'uruchomienie workera pozostanie zablokowane do poprawienia odczytu.')
            return
        self.status.setText(str(error) if isinstance(error, ValueError) and not isinstance(error, UnicodeError) else
                            'Brak połączenia. Sprawdź adres, bazę, hasło, TLS i dostęp sieciowy PostgreSQL.')

    def save(self):
        try:
            values = self.values()
            if values != self.tested:
                self.status.setText('Sprawdź połączenie dla aktualnie wpisanych danych przed zapisaniem.')
                return
            if self.key_error and QMessageBox.question(self, 'Naprawa zapisanych haseł',
                    str(self.key_error) + '\nZapisać konfigurację lokalną i przejść do naprawy? '
                    'Żadne hasło w bazie nie zostanie usunięte ani zmienione.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            save_local_config(*values)
            self.password.clear()
            self.key.clear()
            self.accept()
        except Exception:
            self.status.setText('Nie udało się zapisać konfiguracji. Sprawdź uprawnienia do lokalnego folderu Biuro.')


class WorkerSetupWindow(QMainWindow):
    def __init__(self, actor_id):
        super().__init__()
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Konfiguracja wymaga administratora Biuro.')
        self.actor_id = actor_id
        self.setWindowTitle('Biuro — konfiguracja i panel workera')
        self.resize(1200, 850)
        self.runner = TaskRunner(self)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        start = QWidget()
        layout = QVBoxLayout(start)
        self.status = QLabel('1. Uzupełnij i zapisz ustawienia usług w odpowiednich zakładkach.\n'
                             '2. W zakładce Zadania wybierz usługi, odstępy i włącz worker.\n'
                             '3. Sprawdź konfigurację i zainstaluj autostart.\n'
                             'Ustawienia usług są wspólne z programem na komputerach pracowników. '
                             'Import e-ZLA do Enovy nie jest jeszcze podłączony.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        path = QLabel(f'Konfiguracja tego konta Windows: {worker_env_path()}')
        path.setWordWrap(True)
        layout.addWidget(path)
        check = QPushButton('Sprawdź bazę i odszyfrowanie zapisanych haseł — bez uruchamiania zadań')
        check.clicked.connect(self.check)
        layout.addWidget(check)
        self.install_button = QPushButton('Zainstaluj autostart i uruchom worker')
        self.install_button.clicked.connect(self.install)
        layout.addWidget(self.install_button)
        layout.addStretch()
        self.tabs.addTab(start, 'Instalacja')
        from app.admin.settings.worker_monitor import WorkerMonitor
        from app.admin.settings.worker_settings import WorkerSettings
        from app.admin.settings.ezla_settings import EzlaSettings
        from app.admin.settings.enova_connection_settings import EnovaConnectionSettings
        from app.admin.settings.email_settings import EmailSettings
        from app.admin.settings.reset_calendar_settings import CalendarAndResetSettings
        from app.admin.settings.reminder_settings import ReminderSettings
        from app.admin.settings.ksef_settings import KsefSettings
        from app.admin.settings.platnik_settings import PlatnikSettings
        from app.admin.settings.saldeo_web_settings import SaldeoWebSettings
        # Build lazily: large account/company lists should not block opening the panel.
        self.factories = {
            1: lambda: WorkerMonitor(actor_id), 2: lambda: WorkerSettings(actor_id),
            3: lambda: EzlaSettings(actor_id), 4: lambda: EnovaConnectionSettings(actor_id),
            5: EmailSettings, 6: CalendarAndResetSettings, 7: ReminderSettings, 8: KsefSettings,
            9: lambda: SaldeoWebSettings(actor_id),
            10: lambda: PlatnikSettings(actor_id),
        }
        for title in ('Panel i logi', 'Zadania', 'e-ZLA', 'Odczyt z Enovy', 'Konta poczty',
                      'Konto systemowe', 'Przypomnienia', 'KSeF', 'Saldeo', 'Płatnik'):
            self.tabs.addTab(QWidget(), title)
        self.tabs.currentChanged.connect(self.open_tab)

    def open_tab(self, index):
        factory = self.factories.get(index)
        if not factory:
            return
        try:
            page = factory()
        except Exception:
            QMessageBox.warning(self, 'Worker', 'Nie udało się odczytać ustawień. Sprawdź bazę i wspólny klucz.')
            return
        self.factories.pop(index)
        container = self.tabs.widget(index)
        layout = QVBoxLayout(container)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(page)
        layout.addWidget(area)

    def check(self):
        self.status.setText('Sprawdzanie bazy i wspólnego klucza…')
        self.runner.submit(preflight, lambda count: self.status.setText(
            f'Baza gotowa. Odszyfrowano zapisane hasła: {count}. '
            'Połączenia z usługami sprawdź przyciskami w ich zakładkach. Worker nie został uruchomiony.'), self.failed)

    def failed(self, error):
        self.install_button.setEnabled(True)
        if isinstance(error, CredentialKeyError):
            self.status.setText(str(error))
            return
        self.status.setText('Konfiguracja nie jest gotowa. Sprawdź dostęp do bazy, jej wersję i wspólny klucz. '
                            'Jeżeli klucz jest zgodny, ponownie zapisz niedziałające hasła w ustawieniach usług.')

    def install(self):
        if os.name != 'nt' or not getattr(sys, 'frozen', False):
            self.status.setText('Instalacja jednym kliknięciem wymaga zainstalowanej wersji Biuro EXE na Windows.')
            return
        self.install_button.setEnabled(False)
        self.status.setText('Sprawdzanie konfiguracji przed instalacją…')
        self.runner.submit(preflight, self.launch_installer, self.failed)

    def launch_installer(self, _):
        import ctypes
        import subprocess
        from app.services.worker_windows import identity
        try:
            sid = identity()[1]
        except Exception:
            self.failed(None)
            return
        args = subprocess.list2cmdline(['--worker-install-task', sid])
        result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable, args,
                                                    str(Path(sys.executable).parent), 1)
        self.install_button.setEnabled(True)
        self.status.setText('Otwarto okno instalacji workera. Podaj w nim hasło bieżącego konta Windows. '
                            'Po zakończeniu sprawdź świeży sygnał w Panel i logi; rejestracja zadania nie potwierdza działania.'
                            if result > 32 else 'Nie uruchomiono instalatora. Zezwól na UAC i spróbuj ponownie.')


def run(app, *, configure=True):
    if configure or not worker_env_path().is_file():
        dialog = ConnectionDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return 0
    from app.main_ui import LoginWindow
    login = LoginWindow()
    if login.exec() != QDialog.DialogCode.Accepted:
        return 0
    from app.security.authorization import is_administrator
    if not is_administrator(login.pracownik_id):
        QMessageBox.warning(None, 'Worker', 'Zaloguj się jako administrator Biuro.')
        return 1
    from app.preferences.appearance import apply_appearance
    apply_appearance(login.pracownik_id)
    window = WorkerSetupWindow(login.pracownik_id)
    if not configure:
        window.tabs.setCurrentIndex(1)
    window.show()
    return app.exec()
