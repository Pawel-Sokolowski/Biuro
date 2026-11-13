"""A basic worker interface that observes the existing runner rather than starting another."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PyQt6.QtCore import QTimer, QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QTabWidget, QTableWidget, QTableWidgetItem, QAbstractItemView)
from app.services.worker_monitor import snapshot
from app.integrations.ezla.service import ServiceError
from app.utils.background import TaskRunner

JOB_LABELS = {'platnik':'Płatnik — wakacje składkowe', 'mail': 'Poczta', 'reminders': 'Przypomnienia', 'invoices': 'Faktury',
              'saldeo': 'Saldeo — powiadomienia', 'enova': 'Odczyt z Enovy', 'ezla': 'e-ZLA ZUS', 'ksef_import': 'Pobieranie KSeF'}
STATES = {'running': 'W trakcie', 'ok': 'Zakończono', 'failed': 'Błąd', 'started': 'Rozpoczęto'}


class WorkerMonitor(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Brak dostępu do panelu workera.')
        self.actor_id = actor_id
        self.reader = TaskRunner(self)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.summary = QLabel('Panel odczytuje stan workera z bazy Biuro. Nie uruchamia drugiej kopii.')
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        bar.addWidget(self.summary, 1)
        refresh = QPushButton('Odśwież')
        refresh.clicked.connect(self.refresh)
        bar.addWidget(refresh)
        layout.addLayout(bar)
        self.instance_info = QLabel()
        self.instance_info.setWordWrap(True)
        self.instance_info.setTextFormat(Qt.TextFormat.PlainText)
        self.instance_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.instance_info)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        status_page = QWidget()
        status_layout = QVBoxLayout(status_page)
        self.progress = QLabel()
        self.progress.setWordWrap(True)
        self.progress.setTextFormat(Qt.TextFormat.PlainText)
        self.jobs = self._table(['Zadanie', 'Stan', 'Start', 'Koniec', 'Następne sprawdzenie', 'Typ błędu'])
        status_layout.addWidget(self.jobs)
        self.tabs.addTab(status_page, 'Status')
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        self.logs = self._table(['Data', 'Zadanie', 'Zdarzenie', 'Typ błędu'])
        log_layout.addWidget(self.logs)
        log_note = QLabel('Ostatnie 200 zdarzeń workera jest dostępne z każdego komputera. '
                          'Szczegółowe pliki logów znajdują się na komputerze, na którym działa worker.')
        log_note.setWordWrap(True)
        log_layout.addWidget(log_note)
        local_logs = QPushButton('Otwórz folder logów na tym komputerze')
        local_logs.clicked.connect(self.open_logs)
        log_layout.addWidget(local_logs)
        self.log_page = log_page
        self.ezla = self._table(['Firma', 'NIP', 'Pobieranie', 'Stan', 'Ostatnia próba',
                                 'Ostatni sukces', 'Nowe raporty', 'Błąd'])
        ezla_page = QWidget()
        ezla_layout = QVBoxLayout(ezla_page)
        ezla_layout.addWidget(self.progress)
        ezla_layout.addWidget(self.ezla)
        self.tabs.addTab(ezla_page, 'e-ZLA')
        import_page = QWidget()
        import_layout = QVBoxLayout(import_page)
        import_note = QLabel('Import e-ZLA do Enovy nie jest jeszcze podłączony. '
                            'Pobranie raportu z ZUS nie oznacza zaimportowania go do Enovy. '
                            'Status importu wymaga potwierdzenia z zadania importującego w Enovie.')
        import_note.setWordWrap(True)
        import_layout.addWidget(import_note)
        import_layout.addStretch()
        self.tabs.addTab(import_page, 'Import do Enovy')
        self.job_views = {}
        for keys, title in ((('enova',), 'Odczyt z Enovy'),
                            (('mail',), 'Poczta'),
                            (('invoices', 'reminders'), 'Faktury i przypomnienia'),
                            (('ksef_import',), 'KSeF'), (('platnik',), 'Płatnik')):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            note = QLabel('Ostatni zapisany stan zadań. Brak wpisu oznacza, że worker nie zgłosił jeszcze przebiegu.')
            note.setWordWrap(True)
            page_layout.addWidget(note)
            table = self._table(['Zadanie', 'Stan', 'Start', 'Koniec', 'Następne sprawdzenie', 'Typ błędu'])
            page_layout.addWidget(table)
            self.job_views[keys] = table
            self.tabs.addTab(page, title)
        from app.admin.settings.saldeo_web_settings import SaldeoHistory
        self.tabs.addTab(SaldeoHistory(actor_id), 'Saldeo')
        self.tabs.addTab(log_page, 'Logi')
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        return table

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self.refresh()

    def hideEvent(self, event):
        self.timer.stop()
        self.reader.invalidate()
        super().hideEvent(event)

    def refresh(self, *_):
        self.reader.submit(lambda: snapshot(self.actor_id), self.render, self.failed, key='worker-monitor')

    def failed(self, error):
        self.summary.setText('Nie udało się odczytać stanu workera. Sprawdź połączenie z bazą i uprawnienia.')

    @staticmethod
    def _fill(table, rows):
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value if value not in (None, '') else '—')))
        table.resizeColumnsToContents()

    def render(self, data):
        heartbeat = data['heartbeat']
        now = datetime.now(ZoneInfo('Europe/Warsaw'))
        checked = heartbeat.get('checked_at')
        try:
            stale = not checked or now - datetime.fromisoformat(checked) > timedelta(minutes=2)
        except (ValueError, TypeError):
            stale = True
        state = 'Brak aktualnego sygnału workera' if stale else 'Worker aktywny'
        enabled = 'zadania włączone' if heartbeat.get('enabled') else 'zadania wyłączone'
        self.summary.setText(f'{state}; {enabled}. Ostatni sygnał: {checked or "—"}.')
        instance = heartbeat.get('instance') or {}
        if instance:
            label = 'Ostatnio zgłoszony proces' if stale else 'Działający worker'
            self.instance_info.setText(
                f"{label}: {instance.get('host','—')}, PID {instance.get('pid','—')}, wersja {instance.get('version','—')}\n"
                f"Uruchomiony: {instance.get('started_at','—')}\nPlik: {instance.get('executable','—')}\n"
                f"Konfiguracja: {instance.get('config_file','—')}\n"
                f"Harmonogram odczytany: {heartbeat.get('settings_checked_at') or '—'}. "
                'Ustawienia usług są używane przy następnym przebiegu zadania.')
        else:
            self.instance_info.setText('Worker nie zgłosił jeszcze identyfikatora procesu i wersji. '
                                       'Panel odczytuje stan tej bazy Biuro; nie uruchamia kolejnego workera.')
        jobs = heartbeat.get('jobs', {})
        job_rows = {name: [JOB_LABELS.get(name, name), STATES.get(value.get('state'), value.get('state')),
            value.get('started_at'), value.get('finished_at'), value.get('next_run_at'), value.get('error_type')]
            for name, value in jobs.items()}
        self._fill(self.jobs, list(job_rows.values()))
        for keys, table in self.job_views.items():
            self._fill(table, [job_rows[key] for key in keys if key in job_rows])
        self._fill(self.logs, [[event.get('at'), JOB_LABELS.get(event.get('job'), event.get('job')),
                               STATES.get(event.get('state'), event.get('state')), event.get('error_type')]
                              for event in reversed(data['events'])])
        progress = data['progress']
        names = {row['id']: row['nazwa'] for row in data['companies']}
        message = 'e-ZLA: ' + str(progress.get('stage') or 'Oczekiwanie na pobieranie')
        if progress.get('company_id'):
            message += ' — ' + str(names.get(progress['company_id'], progress['company_id']))
        if progress.get('total') is not None:
            message += f" ({progress.get('position', progress.get('processed', 0))}/{progress['total']})"
        if progress.get('resume_at'):
            try:
                seconds = max(0, int((datetime.fromisoformat(progress['resume_at']) - now).total_seconds()))
                message += f'; wznowienie za {seconds} s'
            except (ValueError, TypeError):
                pass
        if jobs.get('ezla', {}).get('state') == 'failed':
            message += ' [ostatni przebieg e-ZLA zakończony błędem]'
        if progress.get('checked_at'):
            message += '; stan z ' + progress['checked_at']
        if stale:
            message += ' [ostatni zapisany stan; worker nie potwierdza aktualności]'
        if not data['ezla_enabled']:
            message += ' [pobieranie e-ZLA wyłączone w ustawieniach]'
        self.progress.setText(message)
        rows = []
        for row in data['companies']:
            sync = row['sync']
            error = str(ServiceError(sync.get('error_code'), http_status=sync.get('http_status'))) if sync.get('state') == 'failed' else ''
            rows.append([row['nazwa'], row['nip'], 'Tak' if row['id'] in data['ezla_companies'] else 'Nie',
                         STATES.get(sync.get('state'), 'Nie sprawdzono'), sync.get('checked_at'),
                         sync.get('last_success'), sync.get('new_reports'), error])
        self._fill(self.ezla, rows)

    def open_logs(self):
        from app.config import get_data_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_data_dir() / 'logs')))
