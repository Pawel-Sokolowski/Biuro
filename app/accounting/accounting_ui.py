from datetime import datetime
from zoneinfo import ZoneInfo
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDateEdit,
    QPushButton, QLineEdit, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QCheckBox, QStackedWidget)
from app.integrations.enova.accounting import (list_accounting, decoded, documents,
                                                declaration_entries, declarations)
from app.integrations.enova.jpk import STATUSES
from app.companies.cennik_keys import company_accounting_type
from app.utils.background import TaskRunner
from app.accounting.insights import comparison, declaration_issue, hr_status, needs_attention, previous_active_types, HR_LABELS

BASE_TYPES = ('SPU', 'ZR', 'DW', 'AMO')
TYPE_LABELS = {'SPU': 'Sprzedaż', 'ZR': 'Zakup'}
from app.accounting.report_types import DEFAULT_FORMS, form_matches, form_key


def _item(value, tooltip=''):
    item = QTableWidgetItem(str(value if value is not None else '—'))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setToolTip(tooltip)
    return item


def _date(value):
    return str(value).replace('T', ' ')[:19] if value else '—'


class AccountingPage(QWidget):
    """Sprawdzanie: dynamic, read-only accounting/payroll pivots."""
    def __init__(self, actor_id, parent=None, *, department=None):
        super().__init__(parent)
        if department not in (None, 'accounting', 'payroll'):
            raise ValueError('Nieprawidłowy dział.')
        self._fixed_department = department
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
        self.report_selectors, self.report_stacks, self.report_indices = {}, {}, {}
        for department, label in [('accounting', 'Księgowość'), ('payroll', 'Kadry')]:
            sections = QWidget()
            section_layout = QVBoxLayout(sections)
            report_bar = QHBoxLayout()
            report_bar.addWidget(QLabel('Raport:'))
            report_selector = QComboBox()
            report_selector.setMinimumWidth(240)
            report_bar.addWidget(report_selector)
            report_bar.addStretch()
            section_layout.addLayout(report_bar)
            stack = QStackedWidget()
            section_layout.addWidget(stack)
            self.report_selectors[department] = report_selector
            self.report_stacks[department] = stack
            self.report_indices[department] = {}
            table = self._new_table()
            self.tables[department] = table
            self._add_report(department, 'Dokumenty', 'documents', table)
            if department == 'accounting':
                self.comparison_tables[department] = self._new_table()
                self._add_report(department, 'Poprzedni miesiąc', 'comparison', self.comparison_tables[department])
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
            self._add_report(department, 'Deklaracje i UPO — wszystkie', 'declarations', declaration_panel)
            for form in DEFAULT_FORMS[department]:
                self._add_report(department, form, 'form:' + form_key(form), declaration_panel)
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
                self._add_report(department, 'Umowy, badania i BHP', 'hr', panel)
                for kind, title in HR_LABELS.items():
                    self._add_report(department, title, 'hr:' + kind, panel)
                from app.accounting.ezla_ui import EzlaPreviewPanel
                self.ezla_panel = EzlaPreviewPanel(actor_id, self)
                self._add_report(department, 'e-ZLA', 'ezla', self.ezla_panel)
                from app.accounting.holiday_ui import ContributionHolidays
                self.holiday_panel = ContributionHolidays(actor_id,self)
                self._add_report(department,'Wakacje składkowe — Płatnik (rocznie)','holidays',self.holiday_panel)
            self.tabs.addTab(sections, label)
            report_selector.currentIndexChanged.connect(self.filter_rows)
        self.table = self.tables['accounting']
        layout.addWidget(self.tabs)
        from app.accounting.refresh_ui import CheckingRefreshControls
        self.refresh_controls = CheckingRefreshControls(actor_id,self.refresh_companies,self.selected_company_ids,
            lambda kind:self.holiday_panel.year.value() if kind=='platnik' else self.month.date().toString('yyyy-MM'),self.refresh_async,self)
        layout.addWidget(self.refresh_controls)
        self.source_note = QLabel()
        self.source_note.setWordWrap(True)
        layout.addWidget(self.source_note)
        self.tabs.currentChanged.connect(self.update_period_controls)
        self.month.dateChanged.connect(self.refresh_async)
        self.apply_department_default()
        if self._fixed_department:
            self.tabs.tabBar().hide()
            self.tabs.currentChanged.connect(self.apply_department_default)
        self.filter_rows()

    def _add_report(self, department, label, key, widget):
        stack = self.report_stacks[department]
        index = stack.indexOf(widget)
        if index < 0:
            index = stack.addWidget(widget)
        self.report_indices[department][key] = index
        self.report_selectors[department].addItem(label, key)

    def _selected_form(self, department):
        key = self.report_selectors[department].currentData() or ''
        return key[5:] if key.startswith('form:') else None

    def _update_report_choices(self, department):
        selector = self.report_selectors[department]
        blocked = selector.blockSignals(True)
        try:
            # Choices survive filters/month changes so a selected report never silently changes.
            for row in self.rows:
                for entry in declaration_entries(row, department):
                    key = 'form:' + form_key(entry['form'])
                    if selector.findData(key) < 0:
                        index = self.report_indices[department]['declarations']
                        self.report_indices[department][key] = index
                        selector.addItem(entry['form'], key)
        finally:
            selector.blockSignals(blocked)
        self.report_stacks[department].setCurrentIndex(self.report_indices[department][selector.currentData()])

    @staticmethod
    def _new_table():
        table = QTableWidget(0, 0)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.horizontalHeader().setStretchLastSection(False)
        return table

    def apply_department_default(self, *_):
        from app.preferences.department import default_department
        department = self._fixed_department or default_department(self.actor_id)
        self.tabs.setCurrentIndex(1 if department == 'payroll' else 0)

    def yearly_selected(self):
        return (self.tabs.currentIndex()==1 and self.report_selectors['payroll'].currentData()=='holidays')

    def update_period_controls(self,*_):
        annual = self.yearly_selected()
        for widget in (self.month,self.tax_filter,self.attention,self.message,self.source_note):
            widget.setVisible(not annual)
        if hasattr(self,'refresh_controls'):
            payroll = self.tabs.currentIndex()==1
            self.refresh_controls.buttons['enova'].setVisible(not annual)
            self.refresh_controls.buttons['ezla'].setVisible(payroll and not annual)
            self.refresh_controls.buttons['platnik'].setVisible(annual)

    def refresh_companies(self):
        if self.yearly_selected():
            return self.holiday_panel.payload.get('companies',[])
        return self.rows

    def selected_company_ids(self):
        department = 'payroll' if self.tabs.currentIndex()==1 else 'accounting'
        key = self.report_selectors[department].currentData() or 'documents'
        table = (self.holiday_panel.table if key=='holidays' else self.ezla_panel.table if key=='ezla'
                 else self.hr_table if key.startswith('hr') else self.comparison_tables['accounting'] if key=='comparison'
                 else self.declaration_tables[department] if key=='declarations' or key.startswith('form:')
                 else self.tables[department])
        by_name = {}
        for company in self.refresh_companies():
            by_name.setdefault(company['nazwa'],[]).append(company['id'])
        ids = set()
        for row in {item.row() for item in table.selectedItems()}:
            item = table.item(row,0)
            if item is None:
                continue
            fid = item.data(Qt.ItemDataRole.UserRole)
            if fid is None:
                matches = by_name.get(item.text(),[])
                fid = matches[0] if len(matches)==1 else None
            if fid is not None:
                ids.add(fid)
        return sorted(ids)

    def refresh_async(self, *_):
        if self.yearly_selected():
            self.holiday_panel.refresh()
            return
        month = self.month.date().toString('yyyy-MM')
        def read():
            rows = list_accounting(self.actor_id, month)
            remote = None
            if self._fixed_department != 'accounting':
                from app.integrations.ezla.sync import load_reports
                remote = load_reports(self.actor_id, month)
            return rows, remote
        def loaded(result):
            rows, remote = result
            if remote is not None:
                self.ezla_panel.set_remote(remote)
            self.render(rows, month)
        self.reader.submit(read, loaded, self.failed)

    def failed(self, exc):
        self.source_note.setText('Nie udało się odczytać danych: ' + str(exc))

    def render(self, rows, month):
        self.rows, self.period = rows, month
        self.filter_rows()

    def _report_attention(self, row, department, today, horizon):
        selected = self._selected_form(department)
        if selected:
            entries = [entry for entry in declaration_entries(row, department) if form_matches(entry['form'], selected)]
            return not entries or any(declaration_issue(entry) for entry in entries)
        report_key = self.report_selectors[department].currentData()
        if report_key.startswith('hr'):
            events = decoded(row.get('hr')).get('events')
            if events is None:
                return True
            kind = report_key[3:] if report_key.startswith('hr:') else None
            return any((not kind or event['event_type'] == kind) and hr_status(event, today, horizon or 90)
                       in ('Po terminie', 'Dzisiaj', 'Zbliża się termin', 'Brak terminu') for event in events)
        return needs_attention(row, department, today, horizon or 90, self.period)

    def filter_rows(self, *_):
        self.update_period_controls()
        if self.yearly_selected():
            self._update_report_choices('payroll')
            self.holiday_panel.set_company_filter(self.search.text().strip())
            return
        query, selected = self.search.text().strip().casefold(), self.tax_filter.currentData()
        rows = [row for row in self.rows
                if (not selected or (company_accounting_type(row) or 'OTHER') == selected)
                and query in (str(row.get('nazwa') or '') + ' ' + str(row.get('nip') or '')).casefold()]
        today = datetime.now(ZoneInfo('Europe/Warsaw')).date()
        horizon = self.hr_range.currentData()
        for department in self.tables:
            self._update_report_choices(department)
            visible = [row for row in rows if not self.attention.isChecked() or self._report_attention(row, department, today, horizon)]
            self._render_table(visible, department)
            if department == 'accounting':
                self._render_comparisons(visible, department)
            self._render_declarations(visible, department)
            if department == 'payroll':
                self._render_hr(visible, today, horizon)
                self.ezla_panel.set_companies(rows, self.period, self.attention.isChecked())
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
            if department == 'accounting' and counts is not None:
                stamp = report.get('accounting_imported_at') or stamp
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
                if column==0:
                    item.setData(Qt.ItemDataRole.UserRole,row['id'])
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
            form = self._selected_form(department)
            if form:
                entries = [entry for entry in entries if form_matches(entry['form'], form)]
            owner = row.get('kadrowa' if department == 'payroll' else 'ksiegowa') or '—'
            selected = self.declaration_filters[department].currentData()
            if not entries and not selected:
                records.append([row.get('nazwa'), owner, '—', self.report_selectors[department].currentText() if form else '—',
                                'Brak danych' if decoded(row.get('checking')).get('declarations') is not None else 'Nie sprawdzono',
                                '—', '—', '—', '—', 'Brak danych źródłowych'])
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
        selected = self.report_selectors['payroll'].currentData() or ''
        kind = selected[3:] if selected.startswith('hr:') else None
        for row in rows:
            before = len(records)
            report = decoded(row.get('hr'))
            if report.get('events') is None:
                missing += 1
            for event in report.get('events') or []:
                if kind and event['event_type'] != kind:
                    continue
                status = hr_status(event, today, days or 90)
                if status is None:
                    continue
                if days is not None and status in ('Bezterminowa', 'Późniejszy termin'):
                    continue
                employee = event['employee_name'] + ' (' + event['employee_id'] + ')'
                records.append([row.get('nazwa'), row.get('nip'), row.get('kadrowa') or '—', employee,
                                event.get('document_number') or '—', HR_LABELS[event['event_type']],
                                event.get('due_date') or '—', status, _date(report.get('imported_at'))])
            if len(records) == before:
                state = 'Brak odczytu' if report.get('events') is None else 'Brak terminów w wybranym zakresie'
                records.append([row.get('nazwa'), row.get('nip'), row.get('kadrowa') or '—',
                                '—', '—', HR_LABELS.get(kind, '—'), '—', state, _date(report.get('imported_at'))])
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
