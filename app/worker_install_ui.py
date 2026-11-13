"""Elevated worker install/control dialog; no console, credential files or command-line secrets."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox
from app.services.worker_windows import identity, manage_task
from app.utils.background import TaskRunner


class WorkerInstallDialog(QDialog):
    def __init__(self, expected_sid):
        super().__init__()
        self.sid = expected_sid
        self.runner = TaskRunner(self)
        self.setWindowTitle('Biuro Worker — instalacja i uruchamianie')
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        account, sid = identity()
        self.status = QLabel(f'Konto Windows: {account}\nWorker uruchomi się również po restarcie serwera '
                             'i bez zalogowanego użytkownika. Podaj hasło konta Windows (nie PIN i nie hasło Biuro). '
                             'Jeśli zadanie już istnieje dla tej instalacji, działający proces zostanie zatrzymany '
                             'i uruchomiony ponownie bez zmiany konta. Przed aktualizacją zakończ bieżące wysyłki.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Hasło bieżącego konta Windows — do pierwszej instalacji')
        layout.addWidget(self.password)
        self.buttons = []
        for mode, title in (('install', 'Zainstaluj / uruchom ponownie worker'),
                            ('restart', 'Uruchom ponownie worker'), ('stop', 'Zatrzymaj worker')):
            button = QPushButton(title)
            button.clicked.connect(lambda _, action=mode: self.execute(action))
            button.setEnabled(sid == expected_sid)
            layout.addWidget(button)
            self.buttons.append(button)
        if sid != expected_sid:
            self.status.setText('UAC wybrał inne konto Windows. Zamknij okno i uruchom konfigurację '
                                'na tym samym koncie administratora Windows, którego używasz w UAC.')

    def execute(self, mode):
        if mode in ('stop', 'restart') and QMessageBox.question(self, 'Worker',
                'Przerwać bieżący proces workera? Po przerwaniu wysyłki sprawdź jej wynik przed ponowieniem.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        password = self.password.text()
        self.password.clear()
        for button in self.buttons:
            button.setEnabled(False)
        self.status.setText('Wykonywanie operacji. Proszę czekać…')
        self.runner.submit(lambda: manage_task(mode, password, expected_sid=self.sid),
                           lambda _: self.operation_done(mode), self.failed)

    def operation_done(self, mode):
        for button in self.buttons:
            button.setEnabled(True)
        self.status.setText('Zatrzymano proces workera.' if mode == 'stop' else
                            'Zlecono uruchomienie workera. Sprawdź świeży sygnał oraz status zadań '
                            'w panelu Biuro Worker. Możesz zamknąć to okno — worker działa w tle.')

    def failed(self, error):
        for button in self.buttons:
            button.setEnabled(True)
        self.status.setText(str(error) if isinstance(error, ValueError) else
                            'Operacja nie powiodła się. Sprawdź konfigurację bazy, klucz i uprawnienia Windows.')
