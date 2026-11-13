"""Annual source evidence, kept separate from manually completed year-end filings."""
from datetime import datetime
from zoneinfo import ZoneInfo
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QTableWidget, QTableWidgetItem, QAbstractItemView
from app.integrations.platnik.declarations import read_year
from app.accounting.report_types import form_key
from app.utils.background import TaskRunner


class AnnualZusPanel(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.reader = TaskRunner(self)
        self.payload = {'companies': []}
        self.form, self.query = 'IWA', ''
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('Rok, którego dotyczy dokument:'))
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(datetime.now(ZoneInfo('Europe/Warsaw')).year - 1)
        self.year.valueChanged.connect(self.refresh)
        bar.addWidget(self.year)
        layout.addLayout(bar)
        note = QLabel('Firmy według rozliczeń rocznych wybranego roku. Odczyt dokumentów z Płatnika '
                      'nie oznacza potwierdzenia wysyłki/UPO i nie odznacza ręcznego zakończenia roku. '
                      'Pokazywane są wszystkie odczytane dokumenty i korekty.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(['Firma', 'NIP', 'Dokument', 'Identyfikator raportu',
                                             'Osoba', 'Status źródłowy', 'UPO', 'Odczyt'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

    def companies(self):
        return [row for row in self.payload['companies'] if self.form in row['forms']]

    def set_filter(self, form, query):
        self.form, self.query = form, query.strip().casefold()
        self.render()

    def refresh(self, *_):
        year = self.year.value()
        self.payload = {'companies': []}
        self.table.setRowCount(0)
        self.status.setText('Odczytywanie zapisu raportów rocznych…')
        def done(value):
            self.payload = value
            self.render()
        self.reader.submit(lambda: read_year(self.actor_id, year), done, self.failed)

    def failed(self, _):
        self.status.setText('Nie udało się odczytać raportów. Sprawdź dostęp i wspólny klucz szyfrowania.')

    def render(self):
        records = []
        for company in self.companies():
            if self.query not in (company['nazwa'] + ' ' + str(company['nip'] or '')).casefold():
                continue
            entries = [entry for entry in company['declarations'] if form_key(entry['form']) == self.form]
            for entry in entries or [{}]:
                values = [company['nazwa'], company['nip'], entry.get('source_id') or '—', entry.get('report_id') or '—',
                          entry.get('employee_name') or '—', entry.get('status_text') or
                          ('Brak dokumentu w odczycie' if company['imported_at'] else 'Nie odczytano roku'),
                          'Nie odczytano' if entry else '—', company['imported_at'] or '—']
                records.append((company['id'], values))
        self.table.setRowCount(len(records))
        for index, (fid, values) in enumerate(records):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value or '—'))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, fid)
                self.table.setItem(index, column, item)
        self.status.setText(f'Rok {self.year.value()}; ZUS {self.form}; wiersze: {len(records)}.')

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def hideEvent(self, event):
        self.reader.invalidate()
        super().hideEvent(event)
