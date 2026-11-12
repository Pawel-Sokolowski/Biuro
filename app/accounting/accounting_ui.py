from datetime import datetime
from zoneinfo import ZoneInfo
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDateEdit,
    QPushButton, QLineEdit, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QCheckBox)
from app.integrations.enova.accounting import (list_accounting, decoded, documents,
                                                declaration_entries, declarations)
from app.integrations.enova.jpk import STATUSES
from app.companies.cennik_keys import typ_from_forma
from app.utils.background import TaskRunner
from app.accounting.insights import comparison, declaration_issue, hr_status, needs_attention, previous_active_types, HR_LABELS

BASE_TYPES = ('SPU', 'ZR', 'DW', 'AMO')
TYPE_LABELS = {'SPU': 'Sprzedaż', 'ZR': 'Zakup'}
DEFAULT_FORMS = {'accounting': ('JPK_VAT', 'CIT-8'), 'payroll': ('PIT-11', 'PIT-4R', 'ZUS DRA')}


def _item(value, tooltip=''):
    item = QTableWidgetItem(str(value if value is not None else '—'))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setToolTip(tooltip)
    return item


def _date(value):
    return str(value).replace('T', ' ')[:19] if value else '—'


class AccountingPage(QWidget):
    """Sprawdzanie: dynamic, read-only accounting/payroll pivots."""
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.reader = TaskRunner(self)
        self.rows = []
        self.period = QDate.currentDate().toString('yyyy-MM')
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('Sprawdzanie'))
        self.month = QDateEdit(QDate.currentDate())
        self.month.setDisplayFormat('yyyy-MM')
        self.month.setCalendarPopup(True)
        bar.addWidget(self.month)
        self.tax_filter = QComboBox()
        for label, value in [('Wszystkie formy', ''), ('KPiR', 'KPIR'), ('Ryczałt', 'RYCZALT'),
                             ('Księgi rachunkowe', 'KH'), ('Pozostałe', 'OTHER')]:
            self.tax_filter.addItem(label, value)
        self.tax_filter.currentIndexChanged.connect(self.filter_rows)
        bar.addWidget(self.tax_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Szukaj firmy lub NIP')
        self.search.textChanged.connect(self.filter_rows)
        bar.addWidget(self.search)
        self.attention = QCheckBox('Tylko wymagające uwagi')
        self.attention.toggled.connect(self.filter_rows)
        bar.addWidget(self.attention)
        refresh = QPushButton('Odśwież')
        refresh.clicked.connect(self.refresh_async)
        bar.addWidget(refresh)
        layout.addLayout(bar)
        self.message = QLabel('Księgowość: porównanie wyłącznie z poprzednim miesiącem; czerwone 0, gdy wtedy były dokumenty tego rodzaju. Kadry bez porównań. '
                              '„—” oznacza brak odczytu, nie zero. Status przyjęcia wymaga UPO; daty wysyłki i UPO są odrębne.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.tabs = QTabWidget()
        self.tables, self.comparison_tables, self.declaration_tables, self.declaration_filters = {}, {}, {}, {}
        for department, label in [('accounting', 'Księgowość'), ('payroll', 'Kadry')]:
            sections = QTabWidget()
            table = self._new_table()
            self.tables[department] = table
            sections.addTab(table, 'Dokumenty i deklaracje')
            if department == 'accounting':
                self.comparison_tables[department] = self._new_table()
                sections.addTab(self.comparison_tables[department], 'Poprzedni miesiąc')
            declaration_panel = QWidget()
            declaration_layout = QVBoxLayout(declaration_panel)
            selector = QComboBox()
            for title, status in [('Wszystkie deklaracje', ''), ('Do wysłania', 'to_send'),
                                  ('Wysłane', 'sent'), ('Oczekujące na UPO', 'awaiting'),
                                  ('Przyjęte z UPO', 'accepted'), ('Odrzucone', 'rejected')]:
                selector.addItem(title, status)
            self.declaration_filters[department] = selector
            declaration_layout.addWidget(selector)
            self.declaration_tables[department] = self._new_table()
            declaration_layout.addWidget(self.declaration_tables[department])
            selector.currentIndexChanged.connect(self.filter_rows)
            sections.addTab(declaration_panel, 'Deklaracje i UPO')
            if department == 'payroll':
                panel = QWidget()
                panel_layout = QVBoxLayout(panel)
                controls = QHBoxLayout()
                controls.addWidget(QLabel('Terminy od dzisiaj (niezależnie od miesiąca dokumentów):'))
                self.hr_range = QComboBox()
                for title, days in [('7 dni', 7), ('30 dni', 30), ('60 dni', 60), ('90 dni', 90), ('Wszystkie pobrane', None)]:
                    self.hr_range.addItem(title, days)
                self.hr_range.setCurrentIndex(1)
                self.hr_range.currentIndexChanged.connect(self.filter_rows)
                controls.addWidget(self.hr_range)
                panel_layout.addLayout(controls)
                self.hr_note = QLabel()
                self.hr_note.setWordWrap(True)
                panel_layout.addWidget(self.hr_note)
                self.hr_table = self._new_table()
                panel_layout.addWidget(self.hr_table)
                sections.addTab(panel, 'Umowy, badania i BHP')
            self.tabs.addTab(sections, label)
        self.table = self.tables['accounting']
        layout.addWidget(self.tabs)
        self.source_note = QLabel()
        self.source_note.setWordWrap(True)
        layout.addWidget(self.source_note)
        self.month.dateChanged.connect(self.refresh_async)
        self.apply_department_default()
        self.filter_rows()

    @staticmethod
    def _new_table():
        table = QTableWidget(0, 0)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(False)
        return table

    def apply_department_default(self):
        from app.preferences.department import default_department
        self.tabs.setCurrentIndex(1 if default_department(self.actor_id) == 'payroll' else 0)

    def refresh_async(self, *_):
        month = self.month.date().toString('yyyy-MM')
        self.reader.submit(lambda: list_accounting(self.actor_id, month),
                           lambda rows: self.render(rows, month), self.failed)

    def failed(self, exc):
        self.source_note.setText('Nie udało się odczytać danych: ' + str(exc))

    def render(self, rows, month):
        self.rows, self.period = rows, month
        self.filter_rows()

    def filter_rows(self, *_):
        query, selected = self.search.text().strip().casefold(), self.tax_filter.currentData()
        rows = [row for row in self.rows
                if (not selected or (typ_from_forma(row.get('forma_opodatkowania')) or 'OTHER') == selected)
                and query in (str(row.get('nazwa') or '') + ' ' + str(row.get('nip') or '')).casefold()]
        today = datetime.now(ZoneInfo('Europe/Warsaw')).date()
        horizon = self.hr_range.currentData()
        for department in self.tables:
            visible = [row for row in rows if not self.attention.isChecked() or needs_attention(row, department, today, horizon or 90, self.period)]
            self._render_table(visible, department)
            if department == 'accounting':
                self._render_comparisons(visible, department)
            self._render_declarations(visible, department)
            if department == 'payroll':
                self._render_hr(visible, today, horizon)
        missing_reports = sum(not decoded(row.get('checking')) for row in rows)
        missing_payroll = sum(documents(row, 'payroll') is None for row in rows)
        missing_declarations = sum(decoded(row.get('checking')).get('declarations') is None for row in rows)
        self.source_note.setText(f'Firmy: {len(rows)}. Brak pełnego odczytu dokumentów: {missing_reports}; '
                                 f'brak odczytu kadr: {missing_payroll}; brak odczytu deklaracji: {missing_declarations}. '
                                 'Źródła kadrowe i deklaracje/UPO wymagają skonfigurowania w połączeniu Enova. '
                                 'Liczba do faktury pochodzi z Widoku miesięcznego.')

    def _render_table(self, rows, department):
        table = self.tables[department]
        kinds = set(BASE_TYPES if department == 'accounting' else ())
        forms = set(DEFAULT_FORMS[department])
        for row in rows:
            kinds.update(documents(row, department) or {})
            if department == 'accounting':
                kinds.update(previous_active_types(row, self.period))
            forms.update(declarations(row, department))
            for old in row.get('history', []):
                forms.update(entry['form'] for entry in old.get('declarations') or [] if entry['department'] == department)
        kinds = sorted(kinds, key=lambda value: (BASE_TYPES.index(value) if value in BASE_TYPES else len(BASE_TYPES), value))
        forms = sorted(forms, key=lambda value: (DEFAULT_FORMS[department].index(value) if value in DEFAULT_FORMS[department] else len(DEFAULT_FORMS[department]), value))
        labels = [TYPE_LABELS.get(kind, kind) for kind in kinds]
        # Keep distinct definitions distinct even when their friendly names coincide.
        labels = [label + (' (' + kind + ')' if labels.count(label) > 1 else '') for label, kind in zip(labels, kinds)]
        headers = ['Firma', 'NIP', 'Forma opodatkowania', 'Opiekun w biurze', *labels, 'Dokumenty razem']
        if department == 'accounting':
            headers.append('Ilość dokumentów — Widok miesięczny')
        headers.append('Ostatni odczyt')
        for form in forms:
            headers.extend(form + '\n' + label for label in ('Status', 'Data wysyłki', 'UPO', 'Data UPO'))
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            report, counts = decoded(row.get('checking')), documents(row, department)
            historical = previous_active_types(row, self.period) if department == 'accounting' else set()
            cells = [_item(row.get(key)) for key in ('nazwa', 'nip', 'forma_opodatkowania')]
            cells.append(_item(row.get('kadrowa' if department == 'payroll' else 'ksiegowa') or '—'))
            for kind in kinds:
                known = counts is not None and (kind in counts or bool(report))
                value = counts.get(kind, 0) if known else None
                item = _item(value, 'Rodzaj dokumentu: ' + kind)
                if value == 0 and kind in historical:
                    item.setBackground(QColor('#fee2e2'))
                    item.setForeground(QColor('#991b1b'))
                    item.setToolTip('Brak dokumentów tego rodzaju w wybranym miesiącu; występowały w tej firmie w poprzednim miesiącu.\nRodzaj: ' + kind)
                cells.append(item)
            cells.append(_item(sum(counts.values()) if counts is not None else None))
            if department == 'accounting':
                cells.append(_item(row.get('ilosc_dok'), 'Licznik miesięczny, z zachowaniem ręcznych korekt.'))
            stamp = report.get('imported_at') if counts is not None or report.get('declarations') is not None else None
            if department == 'accounting' and not report:
                stamp = decoded(row.get('snapshot')).get('imported_at')
            cells.append(_item(_date(stamp)))
            entries = declarations(row, department)
            for form in forms:
                entry = entries.get(form, {})
                status = entry.get('status_text') or STATUSES.get(entry.get('status'), 'Brak danych' if report.get('declarations') is not None else 'Nie sprawdzono')
                cells.extend((_item(status, 'Odczyt statusu: ' + _date(entry.get('imported_at') or report.get('imported_at'))), _item(_date(entry.get('sent_at'))),
                              _item(f"{entry['upo_count']}/{entry['count']}" if 'count' in entry else ('Tak' if entry.get('upo') else '—'), str(entry.get('upo') or 'Szczegóły pracowników w zakładce Deklaracje i UPO.')),
                              _item(_date(entry.get('upo_at')))))
            for column, item in enumerate(cells):
                table.setItem(index, column, item)
        table.resizeColumnsToContents()
        if rows:
            table.setColumnWidth(0, min(330, max(190, table.columnWidth(0))))

    def _render_comparisons(self, rows, department):
        table = self.comparison_tables[department]
        headers = ['Firma', 'Opiekun w biurze', 'Rodzaj', self.period, 'Poprzedni miesiąc', 'Zmiana', 'Zmiana %']
        records = []
        for row in rows:
            kinds = set(documents(row, department) or {}) | previous_active_types(row, self.period)
            for kind in [None, *sorted(kinds)]:
                values = comparison(row, self.period, department, kind)
                percent = 'Nowe' if values['new'] else (f"{values['percent']:+.1f}%" if values['percent'] is not None else '—')
                records.append([row.get('nazwa'), row.get('ksiegowa') or '—',
                                'Razem' if kind is None else TYPE_LABELS.get(kind, kind),
                                values['current'], values['previous'], values['delta'], percent])
        self._fill(table, headers, records)
        for index, record in enumerate(records):
            delta = record[5]
            if delta is not None and delta != 0:
                item = table.item(index, 5)
                item.setForeground(QColor('#b45309' if delta < 0 else '#166534'))
                if record[3] == 0 and record[4] > 0:
                    table.item(index, 3).setBackground(QColor('#fee2e2'))
                    table.item(index, 3).setForeground(QColor('#991b1b'))
        table.setToolTip('Porównanie wyłącznie z poprzednim miesiącem. Brak odczytu: —. Przy zerowej podstawie nie obliczamy procentu.')

    def _render_declarations(self, rows, department):
        table = self.declaration_tables[department]
        records, receipts = [], []
        for row in rows:
            entries = declaration_entries(row, department)
            owner = row.get('kadrowa' if department == 'payroll' else 'ksiegowa') or '—'
            selected = self.declaration_filters[department].currentData()
            if not entries and not selected:
                records.append([row.get('nazwa'), owner, '—', '—', 'Nie sprawdzono', '—', '—', '—', '—', 'Brak danych źródłowych'])
                receipts.append('')
            for entry in sorted(entries, key=lambda item: (item['form'], item.get('employee_name', ''))):
                status = entry.get('status')
                matches = {'to_send': status in ('prepared', 'not_sent'),
                           'sent': status in ('sent', 'processing', 'accepted'),
                           'awaiting': status in ('sent', 'processing', 'accepted') and not entry.get('upo'),
                           'accepted': status == 'accepted' and bool(entry.get('upo')),
                           'rejected': status == 'rejected'}
                if selected and not matches[selected]:
                    continue
                employee = entry.get('employee_name') or 'Cała firma / zestaw'
                if entry.get('employee_id'):
                    employee += ' (' + entry['employee_id'] + ')'
                records.append([row.get('nazwa'), owner, employee, entry['form'],
                                STATUSES.get(entry.get('status'), 'Nie sprawdzono'),
                                _date(entry.get('sent_at')), 'Tak' if entry.get('upo') else '—',
                                _date(entry.get('upo_at')), entry.get('source_system', 'enova'),
                                declaration_issue(entry) or 'OK'])
                receipts.append(str(entry.get('upo') or 'Brak potwierdzenia UPO.'))
        self._fill(table, ['Firma', 'Opiekun w biurze', 'Pracownik firmy', 'Deklaracja', 'Status',
                           'Data wysyłki', 'UPO', 'Data UPO', 'Źródło', 'Do sprawdzenia'], records)
        for index, record in enumerate(records):
            table.item(index, 6).setToolTip(receipts[index])
            if record[-1] not in ('OK', 'Brak danych źródłowych'):
                table.item(index, 9).setBackground(QColor('#fef3c7'))
                table.item(index, 9).setForeground(QColor('#92400e'))

    def _render_hr(self, rows, today, days):
        records, missing = [], 0
        for row in rows:
            report = decoded(row.get('hr'))
            if report.get('events') is None:
                missing += 1
            for event in report.get('events') or []:
                status = hr_status(event, today, days or 90)
                if status is None:
                    continue
                if days is not None and status in ('Bezterminowa', 'Późniejszy termin'):
                    continue
                employee = event['employee_name'] + ' (' + event['employee_id'] + ')'
                records.append([row.get('nazwa'), row.get('nip'), row.get('kadrowa') or '—', employee,
                                event.get('document_number') or '—', HR_LABELS[event['event_type']],
                                event.get('due_date') or '—', status, _date(report.get('imported_at'))])
        records.sort(key=lambda record: (0 if record[7] in ('Po terminie', 'Brak terminu') else 1, record[6], record[0]))
        self._fill(self.hr_table, ['Firma', 'NIP', 'Kadrowa w biurze', 'Pracownik firmy', 'Numer dokumentu',
                                  'Rodzaj', 'Data', 'Stan', 'Ostatni odczyt'], records)
        for index, record in enumerate(records):
            if record[7] in ('Po terminie', 'Brak terminu'):
                item = self.hr_table.item(index, 7)
                item.setBackground(QColor('#fee2e2'))
                item.setForeground(QColor('#991b1b'))
        self.hr_note.setText(f'Stan terminów na {today.isoformat()}. Brak odczytu dla {missing} firm. '
                             'Umowy bezterminowe są widoczne w „Wszystkie pobrane”. Dane wymagają źródła terminów kadrowych z Enova.')

    @staticmethod
    def _fill(table, headers, records):
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(records))
        for row, values in enumerate(records):
            for column, value in enumerate(values):
                table.setItem(row, column, _item(value))
        table.resizeColumnsToContents()
        if records:
            table.setColumnWidth(0, min(330, max(190, table.columnWidth(0))))
