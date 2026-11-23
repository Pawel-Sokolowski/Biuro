from PyQt6.QtCore import QTime
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, QCheckBox,
    QSpinBox, QTimeEdit, QPushButton, QMessageBox)
from app.services.worker_settings import load_settings, save_settings


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
                          ('invoices', 'Kolejka faktur PDF / sprawdzanie KSeF'), ('enova', 'Enova — tylko po zmianie etapu firmy w Biuro'),
                          ('ezla', 'e-ZLA — raz w tygodniu, w poniedziałek'),
                          ('checking_refresh', 'Obsługa zapisanych zleceń pobrania'),
                          ('saldeo', 'Historia powiadomień Saldeo — komplet dokumentów'),
                          ('platnik', 'Płatnik — wakacje składkowe (bieżący i poprzedni rok)'),
                          ('ksef_import', 'Pobieranie faktur z KSeF (bieżący i poprzedni miesiąc)')]:
            toggle = QCheckBox(name)
            toggle.setChecked(settings[key])
            interval = QSpinBox()
            interval.setRange(30, 86400)
            interval.setSuffix(' s')
            interval.setValue(settings[key + '_interval'])
            self.toggles[key], self.intervals[key] = toggle, interval
            if key=='enova':
                interval.setValue(30)
                interval.setEnabled(False)
                interval.setToolTip('Sprawdza lokalną kolejkę zmian; bez zmiany etapu nie łączy się z Enovą.')
            if key=='ezla':
                interval.setToolTip('Odstęp sprawdzania harmonogramu/ponowienia po błędzie, nie częstotliwość pobierania.')
            form.addRow(toggle, interval)
        self.enova_time = QTimeEdit(QTime.fromString(settings['enova_time'], 'HH:mm'))
        self.enova_time.setDisplayFormat('HH:mm')
        self.enova_time.hide()
        self.ezla_time = QTimeEdit(QTime.fromString(settings['ezla_time'],'HH:mm'))
        self.ezla_time.setDisplayFormat('HH:mm')
        form.addRow('e-ZLA — poniedziałek od (Europe/Warsaw)',self.ezla_time)
        self.platnik_gui = QCheckBox('Płatnik GUI — codzienne sprawdzanie wysyłki')
        self.platnik_gui.setChecked(settings['platnik_gui'])
        self.platnik_gui_time = QTimeEdit(QTime.fromString(settings['platnik_gui_time'], 'HH:mm'))
        self.platnik_gui_time.setDisplayFormat('HH:mm')
        self.platnik_gui_time.setToolTip('Godzina w strefie Europe/Warsaw. Agent odbiera zlecenia na aktywnym pulpicie.')
        form.addRow(self.platnik_gui, self.platnik_gui_time)
        layout.addLayout(form)
        from app.admin.settings.worker_monitor import WorkerMonitor
        self.monitor = WorkerMonitor(actor_id, self)
        self.status = self.monitor.summary
        layout.addWidget(self.monitor)
        panel = QPushButton('Otwórz panel workera w osobnym oknie')
        panel.clicked.connect(self.open_panel)
        layout.addWidget(panel)
        for label, action in [('Zapisz ustawienia workera', self.save), ('Odśwież stan workera', self.refresh)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
        layout.addStretch()
        self._saved_state = self.form_settings()
        self.refresh()

    def form_settings(self):
        settings = load_settings()
        settings['enabled'] = self.enabled.isChecked()
        settings['platnik_gui'] = self.platnik_gui.isChecked()
        settings['platnik_gui_time'] = self.platnik_gui_time.time().toString('HH:mm')
        settings['enova_time'] = self.enova_time.time().toString('HH:mm')
        settings['ezla_time'] = self.ezla_time.time().toString('HH:mm')
        for key, toggle in self.toggles.items():
            settings[key] = toggle.isChecked()
            settings[key + '_interval'] = self.intervals[key].value()
        return settings

    def is_dirty(self):
        return self.form_settings()!=self._saved_state

    def persist(self):
        save_settings(self.form_settings(),actor_id=self.actor_id)
        self._saved_state = self.form_settings()

    def reload_saved(self):
        if self.is_dirty():
            return
        settings = load_settings()
        self.enabled.setChecked(settings['enabled'])
        self.platnik_gui.setChecked(settings['platnik_gui'])
        self.platnik_gui_time.setTime(QTime.fromString(settings['platnik_gui_time'], 'HH:mm'))
        self.enova_time.setTime(QTime.fromString(settings['enova_time'],'HH:mm'))
        self.ezla_time.setTime(QTime.fromString(settings['ezla_time'],'HH:mm'))
        for key,toggle in self.toggles.items():
            toggle.setChecked(settings[key])
            self.intervals[key].setValue(30 if key=='enova' else settings[key+'_interval'])
        self._saved_state = self.form_settings()

    def save(self):
        try:
            self.persist()
        except Exception as exc:
            QMessageBox.warning(self, 'Worker', str(exc))
            return
        QMessageBox.information(self, 'Worker', 'Zapisano. Działający worker odczyta ustawienia w ciągu kilku sekund.')

    def refresh(self):
        if self.monitor.isVisible():
            self.monitor.refresh()

    def open_panel(self):
        from PyQt6.QtWidgets import QDialog
        from app.admin.settings.worker_monitor import WorkerMonitor
        window = QDialog(self)
        window.setWindowTitle('Biuro — panel workera')
        window.resize(1100, 700)
        layout = QVBoxLayout(window)
        layout.addWidget(WorkerMonitor(self.actor_id, window))
        window.exec()
        window.deleteLater()
