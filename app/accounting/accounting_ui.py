from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDateEdit,
    QPushButton, QLineEdit, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QAbstractItemView)
from app.integrations.enova.accounting import (list_accounting, decoded, documents,
                                                active_types, declarations)
from app.integrations.enova.jpk import STATUSES
from app.companies.cennik_keys import typ_from_forma
from app.utils.background import TaskRunner

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
        refresh = QPushButton('Odśwież')
        refresh.clicked.connect(self.refresh_async)
        bar.addWidget(refresh)
        layout.addLayout(bar)
        self.message = QLabel('Czerwone 0: ten rodzaj wystąpił w tej firmie w poprzednich 12 miesiącach. '
                              '„—” oznacza brak odczytu, nie zero. Status przyjęcia wymaga UPO; daty wysyłki i UPO są odrębne.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.tabs = QTabWidget()
        self.tables = {}
        for department, label in [('accounting', 'Księgowość'), ('payroll', 'Kadry')]:
            table = QTableWidget(0, 0)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setStretchLastSection(False)
            self.tables[department] = table
            self.tabs.addTab(table, label)
        self.table = self.tables['accounting']
        layout.addWidget(self.tabs)
        self.source_note = QLabel()
        self.source_note.setWordWrap(True)
        layout.addWidget(self.source_note)
        self.month.dateChanged.connect(self.refresh_async)
        self.filter_rows()

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
        for department in self.tables:
            self._render_table(rows, department)
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
            kinds.update(active_types(row, department))
            forms.update(declarations(row, department))
            for old in row.get('history', []):
                forms.update(entry['form'] for entry in old.get('declarations') or [] if entry['department'] == department)
        kinds = sorted(kinds, key=lambda value: (BASE_TYPES.index(value) if value in BASE_TYPES else len(BASE_TYPES), value))
        forms = sorted(forms, key=lambda value: (DEFAULT_FORMS[department].index(value) if value in DEFAULT_FORMS[department] else len(DEFAULT_FORMS[department]), value))
        labels = [TYPE_LABELS.get(kind, kind) for kind in kinds]
        # Keep distinct definitions distinct even when their friendly names coincide.
        labels = [label + (' (' + kind + ')' if labels.count(label) > 1 else '') for label, kind in zip(labels, kinds)]
        headers = ['Firma', 'NIP', 'Forma opodatkowania', *labels, 'Dokumenty razem']
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
            historical = active_types(row, department)
            cells = [_item(row.get(key)) for key in ('nazwa', 'nip', 'forma_opodatkowania')]
            for kind in kinds:
                known = counts is not None and (kind in counts or bool(report))
                value = counts.get(kind, 0) if known else None
                item = _item(value, 'Rodzaj dokumentu: ' + kind)
                if value == 0 and kind in historical:
                    item.setBackground(QColor('#fee2e2'))
                    item.setForeground(QColor('#991b1b'))
                    item.setToolTip('Brak dokumentów tego rodzaju w wybranym miesiącu; występowały w tej firmie w poprzednich 12 miesiącach.\nRodzaj: ' + kind)
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
                status = STATUSES.get(entry.get('status'), 'Brak danych' if report.get('declarations') is not None else 'Nie sprawdzono')
                cells.extend((_item(status, 'Odczyt statusu: ' + _date(entry.get('imported_at') or report.get('imported_at'))), _item(_date(entry.get('sent_at'))),
                              _item('Tak' if entry.get('upo') else '—', str(entry.get('upo') or 'Brak potwierdzenia UPO.')),
                              _item(_date(entry.get('upo_at')))))
            for column, item in enumerate(cells):
                table.setItem(index, column, item)
        table.resizeColumnsToContents()
        if rows:
            table.setColumnWidth(0, min(330, max(190, table.columnWidth(0))))
