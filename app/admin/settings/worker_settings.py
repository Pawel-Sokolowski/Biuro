import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PyQt6.QtCore import QTime
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, QCheckBox,
    QSpinBox, QTimeEdit, QPushButton, QMessageBox)
from app.services.worker_settings import load_settings, save_settings
from app.db.dao_app_settings import get_setting


class WorkerSettings(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Brak uprawnień do workera.')
        self.actor_id = actor_id
        settings = load_settings()
        layout = QVBoxLayout(self)
        note = QLabel('Worker działa na jednym serwerze, bez otwartego okna Biuro. '
                      'Używa wspólnej bazy i wspólnego klucza szyfrowania. '
                      'Uruchom scripts/install_background_worker.cmd jako administrator na serwerze. '
                      'Wysyła wyłącznie faktury z potwierdzonej kolejki; przypomnienia według reguł z Automatyczne e-maile.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.enabled = QCheckBox('Włącz zadania workera')
        self.enabled.setChecked(settings['enabled'])
        layout.addWidget(self.enabled)
        form = QFormLayout()
        self.toggles, self.intervals = {}, {}
        for key, name in [('mail', 'Sprawdzanie i pobieranie poczty'), ('reminders', 'Przypomnienia e-mail'),
                          ('invoices', 'Kolejka faktur PDF / sprawdzanie KSeF'), ('enova', 'Import Enova'),
                          ('ksef_import', 'Pobieranie faktur z KSeF (bieżący i poprzedni miesiąc)')]:
            toggle = QCheckBox(name)
            toggle.setChecked(settings[key])
            interval = QSpinBox()
            interval.setRange(30, 86400)
            interval.setSuffix(' s')
            interval.setValue(settings[key + '_interval'])
            self.toggles[key], self.intervals[key] = toggle, interval
            form.addRow(toggle, interval)
        self.enova_time = QTimeEdit(QTime.fromString(settings['enova_time'], 'HH:mm'))
        self.enova_time.setDisplayFormat('HH:mm')
        form.addRow('Import Enova raz dziennie od (Europe/Warsaw)', self.enova_time)
        layout.addLayout(form)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for label, action in [('Zapisz ustawienia workera', self.save), ('Odśwież stan workera', self.refresh)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
        layout.addStretch()
        self.refresh()

    def save(self):
        settings = load_settings()
        settings['enabled'] = self.enabled.isChecked()
        settings['enova_time'] = self.enova_time.time().toString('HH:mm')
        for key, toggle in self.toggles.items():
            settings[key] = toggle.isChecked()
            settings[key + '_interval'] = self.intervals[key].value()
        try:
            save_settings(settings, actor_id=self.actor_id)
        except Exception as exc:
            QMessageBox.warning(self, 'Worker', str(exc))
            return
        QMessageBox.information(self, 'Worker', 'Zapisano. Działający worker odczyta ustawienia w ciągu kilku sekund.')

    def refresh(self):
        raw = get_setting('background_worker_heartbeat')
        if not raw:
            self.status.setText('Brak sygnału workera. Uruchom go na serwerze.')
            return
        status = json.loads(raw)
        stamp = datetime.fromisoformat(status['checked_at'])
        stale = datetime.now(ZoneInfo('Europe/Warsaw')) - stamp > timedelta(minutes=2)
        lines = [('Brak aktualnego sygnału' if stale else 'Worker aktywny') + ': ' + stamp.isoformat(timespec='seconds')]
        lines.extend(name + ': ' + value['state'] for name, value in status['jobs'].items())
        self.status.setText('\n'.join(lines))
