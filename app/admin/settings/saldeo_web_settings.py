"""Saldeo browser settings plus a durable, company-scoped notification history."""
import json
import subprocess
import sys
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, QCheckBox,
    QComboBox, QSpinBox, QPushButton, QLabel, QTableWidget, QTableWidgetItem, QAbstractItemView)
from app.utils.background import TaskRunner
from app.db.dao_app_settings import get_setting
from app.integrations.saldeo_web.settings import load, save
from app.integrations.saldeo_web.history import list_history

LABELS = {'pending':'Oczekuje', 'applied':'Zapisano komplet', 'needs_mapping':'Do przypisania',
          'ignored':'Inne powiadomienie', 'ok':'Historia sprawdzona', 'partial':'Historia niepełna — sprawdź stronicowanie',
          'login_required':'Wymagane logowanie / 2FA', 'error':'Błąd odczytu — sprawdź przeglądarkę i selektory',
          'disabled':'Wyłączony', 'browser_busy':'Przeglądarka zajęta', 'interrupted':'Przerwano'}


class SaldeoHistory(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.runner = TaskRunner(self)
        layout = QVBoxLayout(self)
        self.status = QLabel('Historia powiadomień Saldeo')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Odebrano w Biuro', 'Data źródłowa', 'Firma Saldeo', 'Miesiąc', 'Wynik', 'Treść'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        refresh = QPushButton('Odśwież historię')
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self.refresh()

    def hideEvent(self, event):
        self.timer.stop()
        self.runner.invalidate()
        super().hideEvent(event)

    def refresh(self):
        self.runner.submit(lambda: (list_history(self.actor_id), json.loads(get_setting('saldeo_web_status') or '{}')), self.render,
                           lambda _: self.status.setText('Nie udało się odczytać historii. Sprawdź połączenie i aktualizację bazy.'), key='saldeo-history')

    def render(self, payload):
        rows, status = payload
        state = LABELS.get(status.get('state'), 'Jeszcze nie sprawdzono')
        self.status.setText(state + '; ostatnie sprawdzenie: ' + str(status.get('checked_at', '—')) + '\n' + f'Historia Saldeo — ostatnie {len(rows)} zdarzeń. Zdarzenia „Do przypisania” są ponawiane po poprawieniu nazwy skróconej firmy.')
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            values = [row['detected_at'], row['source_time'], row['company_name'], row['miesiac'], LABELS.get(row['state'], row['state']), row['body']]
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(value or '—')))
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(5, 450)


class SaldeoWebSettings(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Tylko administrator konfiguruje Saldeo.')
        self.actor_id = actor_id
        self.runner = TaskRunner(self)
        layout = QVBoxLayout(self)
        note = QLabel('Monitoring odczytuje historię wewnątrz Saldeo, również przeczytane wpisy. '
                      'Edge lub Chrome musi być zainstalowany na serwerze. Sesja przeglądarki jest lokalna '
                      'dla konta Windows workera. Po pierwszym logowaniu zamknij okno przeglądarki. '
                      'Selektory CSS muszą odpowiadać rzeczywistemu widokowi historii Saldeo; nie są zgadywane. '
                      'Nie wskazuj listy pokazującej wyłącznie nieprzeczytane wpisy.')
        note.setWordWrap(True)
        layout.addWidget(note)
        config = load()
        self.enabled = QCheckBox('Włącz monitoring historii Saldeo')
        self.enabled.setChecked(config['enabled'])
        layout.addWidget(self.enabled)
        form = QFormLayout()
        self.fields = {}
        for key, label in (('login_url','Adres logowania HTTPS'), ('history_url','Adres pełnej historii powiadomień'),
                           ('username','Login (opcjonalne automatyczne logowanie)'),
                           ('logged_in_selector','CSS: element widoczny po zalogowaniu'),
                           ('item_selector','CSS: pojedynczy wpis historii'), ('empty_selector','CSS: potwierdzenie pustej historii'),
                           ('next_selector','CSS: przycisk następnej strony'), ('open_selector','CSS: otwórz dzwonek/listę (opcjonalne)'),
                           ('username_selector','CSS: pole loginu'), ('password_selector','CSS: pole hasła'),
                           ('submit_selector','CSS: przycisk logowania')):
            field = QLineEdit(config[key])
            self.fields[key] = field
            form.addRow(label, field)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Puste zachowuje zapisane hasło; 2FA wymaga ręcznego logowania')
        form.addRow('Hasło Saldeo (opcjonalne)', self.password)
        self.channel = QComboBox()
        for label, value in (('Microsoft Edge','msedge'), ('Google Chrome','chrome')):
            self.channel.addItem(label, value)
        self.channel.setCurrentIndex(max(0,self.channel.findData(config['channel'])))
        form.addRow('Przeglądarka', self.channel)
        self.pages, self.pause = QSpinBox(), QSpinBox()
        self.pages.setRange(1,100)
        self.pages.setValue(config['max_pages'])
        self.pause.setRange(1,60)
        self.pause.setValue(config['pause_seconds'])
        self.pause.setSuffix(' s')
        form.addRow('Maksymalnie stron na przebieg', self.pages)
        form.addRow('Przerwa między stronami', self.pause)
        layout.addLayout(form)
        self.status = QLabel('Monitoring wymaga także włączenia zadania Saldeo w Zadaniach workera.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        for title, action in (('Zapisz ustawienia Saldeo', self.save),
                              ('Otwórz przeglądarkę — zaloguj się na tym komputerze', self.login),
                              ('Sprawdź i zapisz powiadomienia teraz', self.check)):
            button = QPushButton(title)
            button.clicked.connect(action)
            layout.addWidget(button)
        layout.addWidget(SaldeoHistory(actor_id))

    def save(self):
        try:
            config = {key: field.text().strip() for key, field in self.fields.items()}
            config.update(enabled=self.enabled.isChecked(), channel=self.channel.currentData(),
                          max_pages=self.pages.value(), pause_seconds=self.pause.value())
            save(config, self.actor_id, self.password.text())
            self.password.clear()
            self.status.setText('Zapisano. Skonfiguruj i sprawdź widok na koncie Saldeo przed włączeniem automatu.')
        except (ValueError, PermissionError) as error:
            self.status.setText(str(error))

    def login(self):
        args = [sys.executable, '--saldeo-login'] if getattr(sys,'frozen',False) else [sys.executable, '-m', 'app.integrations.saldeo_web.login']
        subprocess.Popen(args)
        self.status.setText('Otwieranie przeglądarki. Sesja dotyczy tego konta Windows. Worker zaczeka, jeśli profil jest zajęty.')

    def check(self):
        from app.integrations.saldeo_web.sync import run
        from app.security.authorization import is_administrator
        def work():
            if not is_administrator(self.actor_id):
                raise PermissionError()
            return run()
        self.runner.submit(work, self.done, self.failed)

    def done(self, result):
        self.status.setText(LABELS.get(result['state'],result['state']) + f"; nowe: {result.get('new',0)}, zastosowane: {result.get('applied',0)}")

    def failed(self, _):
        status = json.loads(get_setting('saldeo_web_status') or '{}')
        self.status.setText(LABELS.get(status.get('state'), 'Nie udało się sprawdzić Saldeo. Sprawdź logowanie, widok i dostęp do serwera.'))
