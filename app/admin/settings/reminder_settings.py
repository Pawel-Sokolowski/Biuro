"""Automatic mail schedules and reusable plain-text templates."""
from copy import deepcopy
from PyQt6.QtCore import QTime
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QTimeEdit, QVBoxLayout, QWidget, QHeaderView)

from app.db.dao_conn import get_conn
from app.services.email_reminders import load_settings, save_settings, validate_settings
from app.utils.system_mailbox import get_system_account_id


class RuleEditor(QGroupBox):
    def __init__(self, title, rule, documents=False):
        super().__init__(title)
        form = QFormLayout(self)
        self.enabled = QCheckBox('Włącz automatyczne przypomnienia')
        self.enabled.setChecked(rule['enabled'])
        form.addRow(self.enabled)
        self.day = QSpinBox()
        self.day.setRange(1, 31)
        self.day.setValue(rule.get('day', 10))
        if documents:
            form.addRow('Termin: dzień następnego miesiąca', self.day)
            note = QLabel('Tylko firmy z przypisaną księgową; firmy obsługiwane wyłącznie kadrowo są pomijane. '
                          'Dotyczy dokumentów za poprzedni miesiąc. Np. 10 listopada dla października. '
                          'Dzień 31 w krótszym miesiącu oznacza jego ostatni dzień. '
                          'Wysyłka ustaje po zaznaczeniu „Dokumenty dostarczone / komplet dokumentów”.')
            note.setWordWrap(True)
            form.addRow(note)
        else:
            note = QLabel('Termin z pola „Termin płatności”. Tylko wystawione, nieanulowane faktury '
                          'i zaliczkowe z dodatnim saldem. Zapłata zatrzymuje przypomnienia.')
            note.setWordWrap(True)
            form.addRow(note)
        self.offsets = QLineEdit(', '.join(map(str, rule['offsets'])))
        self.offsets.setToolTip('0 = w terminie, 2 = dwa dni po terminie. Liczby rozdziel przecinkami.')
        form.addRow('Dni po terminie (np. 0, 2, 3)', self.offsets)
        self.repeat = QSpinBox()
        self.repeat.setRange(0, 365)
        self.repeat.setSpecialValueText('Nie powtarzaj')
        self.repeat.setValue(rule['repeat'])
        form.addRow('Potem powtarzaj co (dni kalendarzowe)', self.repeat)
        self.send_time = QTimeEdit(QTime.fromString(rule['time'], 'HH:mm'))
        self.send_time.setDisplayFormat('HH:mm')
        form.addRow('Godzina (Europe/Warsaw)', self.send_time)
        days = QHBoxLayout()
        self.days = []
        for day, name in enumerate(('Pon', 'Wt', 'Śr', 'Czw', 'Pt', 'Sob', 'Niedz')):
            check = QCheckBox(name)
            check.setChecked(day in rule['weekdays'])
            self.days.append(check)
            days.addWidget(check)
        form.addRow('Dni wysyłki', days)
        self.holidays = QCheckBox('Wysyłaj również w polskie święta ustawowe')
        self.holidays.setChecked(rule['holidays'])
        form.addRow(self.holidays)
        note = QLabel('Wykluczone dni przesuwają wysyłkę na najbliższy dozwolony dzień. '
                      'Po przerwie wysyłane jest tylko najnowsze zaległe przypomnienie.')
        note.setWordWrap(True)
        form.addRow(note)
        self.template = QComboBox()
        self.original_template = rule['template']
        form.addRow('Szablon wiadomości', self.template)

    def value(self):
        try:
            offsets = sorted(set(int(x.strip()) for x in self.offsets.text().split(',')))
        except ValueError:
            raise ValueError('Podaj dni po terminie jako liczby rozdzielone przecinkami.')
        return {'enabled': self.enabled.isChecked(), 'day': self.day.value(), 'offsets': offsets,
                'repeat': self.repeat.value(), 'time': self.send_time.time().toString('HH:mm'),
                'weekdays': [i for i, check in enumerate(self.days) if check.isChecked()],
                'holidays': self.holidays.isChecked(), 'template': self.template.currentText()}


class TemplateEditor(QWidget):
    def __init__(self, template):
        super().__init__()
        form = QFormLayout(self)
        self.name = QLineEdit(template['name'])
        self.subject = QLineEdit(template['subject'])
        self.body = QTextEdit()
        self.body.setPlainText(template['body'])
        self.body.setMinimumHeight(150)
        form.addRow('Nazwa', self.name)
        form.addRow('Temat', self.subject)
        form.addRow('Treść', self.body)

    def value(self):
        return {'name': self.name.text().strip(), 'subject': self.subject.text(), 'body': self.body.toPlainText()}


class ReminderSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = load_settings()
        layout = QVBoxLayout(self)
        note = QLabel('Automatyczne e-maile — dokumenty i płatności')
        layout.addWidget(note)
        note = QLabel('Wysyłka z konta systemowego. Harmonogram działa co minutę, gdy aplikacja jest uruchomiona. '
                      'Wysyłkę przy zamkniętej aplikacji można skonfigurować na serwerze. '
                      'Zmiany obowiązują po zapisaniu. Podgląd nie wysyła wiadomości.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.rules = {}
        for kind, title in (('documents', 'Brak kompletu dokumentów'), ('payments', 'Płatności')):
            self.rules[kind] = RuleEditor(title, self.settings[kind], documents=kind == 'documents')
            layout.addWidget(self.rules[kind])
        note = QLabel('Szablony — pola: {firma}, {miesiac}, {termin}, {numer}, {pozostalo}. '
                      'Numer i saldo dotyczą płatności. Treść jest zwykłym tekstem.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.templates = QTabWidget()
        layout.addWidget(self.templates)
        for template in self.settings['templates']:
            self._add_template(template)
        actions = QHBoxLayout()
        add = QPushButton('Dodaj szablon')
        add.clicked.connect(lambda: self._add_template({'name': 'Nowy szablon', 'subject': '', 'body': ''}))
        remove = QPushButton('Usuń szablon')
        remove.clicked.connect(self._remove_template)
        preview = QPushButton('Podgląd szablonu')
        preview.clicked.connect(self._preview)
        save = QPushButton('Zapisz ustawienia')
        save.clicked.connect(self._save)
        for button in (add, remove, preview, save):
            actions.addWidget(button)
        layout.addLayout(actions)
        self._sync_templates()
        for rule in self.rules.values():
            rule.template.setCurrentText(rule.original_template)
        note = QLabel('Historia: „sending” oznacza próbę rozpoczętą; „failed” — błąd; „sent” — wysłano. '
                      'Próby z błędem lub przerwane nie są automatycznie ponawiane w tym samym terminie, '
                      'aby uniknąć podwójnej wysyłki. Kolejny termin pozostaje aktywny.')
        note.setWordWrap(True)
        layout.addWidget(note)
        refresh = QPushButton('Odśwież historię')
        refresh.clicked.connect(self._load_history)
        layout.addWidget(refresh)
        self.history = QTableWidget(0, 6)
        self.history.setHorizontalHeaderLabels(['Data', 'Typ', 'Odbiorca', 'Temat', 'Status', 'Błąd'])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history.horizontalHeader().setStretchLastSection(True)
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history.setMinimumHeight(180)
        layout.addWidget(self.history)
        self._load_history()

    def _add_template(self, template):
        editor = TemplateEditor(deepcopy(template))
        self.templates.addTab(editor, template['name'])
        editor.name.textChanged.connect(self._sync_templates)
        self.templates.setCurrentWidget(editor)
        self._sync_templates()

    def _remove_template(self):
        if self.templates.count() <= 1:
            return
        index = self.templates.currentIndex()
        editor = self.templates.widget(index)
        self.templates.removeTab(index)
        editor.deleteLater()
        self._sync_templates()

    def _sync_templates(self):
        names = [self.templates.widget(i).name.text().strip() for i in range(self.templates.count())]
        for i, name in enumerate(names):
            self.templates.setTabText(i, name or 'Bez nazwy')
        for rule in self.rules.values():
            index, current = rule.template.currentIndex(), rule.template.currentText()
            rule.template.clear()
            rule.template.addItems(names)
            rule.template.setCurrentIndex(names.index(current) if current in names else max(0, min(index, len(names) - 1)))

    def _value(self):
        value = {kind: rule.value() for kind, rule in self.rules.items()}
        value['templates'] = [self.templates.widget(i).value() for i in range(self.templates.count())]
        validate_settings(value)
        return value

    def _preview(self):
        try:
            self._value()
            template = self.templates.currentWidget().value()
            fields = {'firma': 'Przykładowa firma', 'miesiac': '2026-10', 'termin': '2026-11-10',
                      'numer': 'FV/123/2026', 'pozostalo': '123.45'}
            QMessageBox.information(self, template['subject'].format(**fields), template['body'].format(**fields))
        except ValueError as exc:
            QMessageBox.warning(self, 'Szablon', str(exc))

    def _save(self):
        try:
            settings = self._value()
            if any(settings[k]['enabled'] for k in self.rules) and get_system_account_id() is None:
                raise ValueError('Najpierw skonfiguruj konto systemowe w „Konto systemowe i kalendarz”.')
            save_settings(settings)
        except Exception as exc:
            QMessageBox.warning(self, 'Przypomnienia', str(exc))
            return
        QMessageBox.information(self, 'Przypomnienia', 'Zapisano ustawienia automatycznych e-maili.')

    def _load_history(self):
        with get_conn() as conn:
            rows = conn.execute('SELECT created_at, kind, recipient, subject, status, error '
                                'FROM email_reminder_log ORDER BY id DESC LIMIT 100').fetchall()
        self.history.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem(str(value or ''))
                item.setToolTip(item.text())
                self.history.setItem(r, c, item)
