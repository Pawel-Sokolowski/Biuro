from app.utils.frozen_table import FrozenFirstColumnTable
from datetime import datetime
from zoneinfo import ZoneInfo
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QTabWidget,
    QAbstractItemView, QCheckBox, QStackedWidget)
from app.monthly_view.month_picker import MonthPicker
from app.integrations.enova.accounting import (list_accounting, decoded, documents,
                                                declaration_entries, declarations, simple_accounting,
                                                SIMPLE_DOCUMENT_TYPES)
from app.integrations.enova.jpk import STATUSES
from app.companies.cennik_keys import company_accounting_type
from app.companies.identities import is_civil
from app.companies.company_names import company_display_name, short_company_display_name
from app.integrations.enova.document_counts import normalize_nip
from app.utils.background import CommandRunner, TaskRunner
from app.utils.table_sorting import SortableItem, apply_header_sort
from app.integrations.platnik.gui_evidence import display_check
from app.accounting.insights import comparison, declaration_issue, hr_status, needs_attention, previous_active_types, HR_LABELS
from app.preferences.state_colors import paint as paint_state

BASE_TYPES = (*SIMPLE_DOCUMENT_TYPES, 'AMO')
TYPE_LABELS = {'SPU': 'Sprzedaż', 'ZR': 'Zakup'}
CONTRIBUTION_COLUMNS = {
    'health': 'Zdrowotna',
    'social': 'Społeczne razem',
    'pension': 'Emerytalna',
    'disability': 'Rentowa',
    'sickness': 'Chorobowa',
    'accident': 'Wypadkowa',
    'labour_fund': 'FP / FS',
    'guaranteed_benefits': 'FGŚP',
    'bridging_pensions': 'FEP',
}
DOCUMENT_NAMES = {
    'DR': 'Dokument rozliczeniowy', 'DW': 'Dokumenty wewnętrzne i pozostałe zapisy',
    'DWNNC': 'Dokument wewnętrzny – nabycie należny/naliczony',
    'DWNNZ': 'Dokument wewnętrzny – nabycie należny (zakup)',
    'LPEW': 'Lista płac – ewidencja', 'PKE': 'Polecenie księgowania – ewidencja',
    'RDEW': 'Raport dzienny ewidencji wewnętrznej', 'RKVE': 'Ważone pozycje kasowe',
    'WBEW': 'Ważone pozycje wyciągów bankowych', 'SPD': 'Sprzedaż detaliczna',
    'SPR': 'Sprzedaż', 'SPT': 'Sprzedaż towarów', 'SPUUE': 'Sprzedaż usług UE',
    'STE': 'Ewidencja dokumentów środków trwałych', 'AMO': 'Amortyzacja',
    'UE': 'Wypłata umów cywilnoprawnych', 'VME': 'VAT marża / VAT międzynarodowy / e-commerce',
    'ZP': 'Zakup paliw', 'ZR': 'Zakup pozostały', 'ZT': 'Zakup towarów',
    'ZUE': 'Zakup unijny', 'ZŚT': 'Zakup środków trwałych',
}
from app.accounting.report_types import (DEFAULT_FORMS, NOT_SENT_FORMS, declaration_title, form_matches,
                                      form_key, form_label, pit_form)


def _item(value, tooltip=''):
    item = SortableItem(str(value if value is not None else '—'))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setToolTip(tooltip)
    return item


def _date(value):
    return str(value).replace('T', ' ')[:19] if value else '—'


def _date_range(entry, first_key='upo_at', last_key='upo_to'):
    """Data albo zakres dat: zbiorczy wiersz deklaracji ma różne daty per pracownik."""
    first, last = _date(entry.get(first_key)), _date(entry.get(last_key))
    if first == '—':
        return '—'
    return first if last in ('—', first) else f'{first} – {last}'


def _upo_tooltip(entry):
    """Numer UPO albo — gdy źródło go nie podaje — informacja, skąd wiadomo o przyjęciu."""
    if entry.get('upo_numbers'):
        return 'Numery UPO poszczególnych pracowników:\n' + '\n'.join(entry['upo_numbers'][:20])
    if entry.get('upo'):
        return 'Nr UPO: ' + str(entry['upo'])
    if entry.get('upo_confirmed_at'):
        return ('Potwierdzenie przyjęcia ze statusu Enovy (' + _date(entry['upo_confirmed_at']) +
                '). Numer UPO nie jest dostępny w tym źródle.')
    return 'Brak potwierdzenia UPO.'


def _not_sent_entry(row, department, form=None):
    """PIT-y nie są wysyłane przez program — pokazujemy je, ale bez zarzutu o brak wysyłki.

    Zwraca wpis tylko dla raportu PIT (cały raport albo filtr formularza), bo lista
    deklaracji „wszystkie” ma już własne wiersze z importu.
    """
    if department != 'accounting' or not form or form_key(form) != 'PIT':
        return None
    return {'form': 'PIT', 'department': 'accounting', 'status': 'not_sent',
            'status_text': 'PIT-y nie są wysyłane przez program', 'upo': '', 'upo_at': None,
            'sent_at': None, 'source_system': ''}


def _search_haystack(row):
    """Pole wyszukiwania: nazwa, NIP i opiekunowie (księgowość i kadry).

    Obejmuje też opiekuna zastępowanego, żeby „moje firmy” działało zarówno dla
    osoby na zastępstwie, jak i dla osoby, którą zastępuje.
    """
    parts = [
        company_display_name(row),
        row.get('nazwa_skrocona'),
        row.get('nazwa_podstawowa'),
        row.get('nazwa'),
        row.get('nazwa_pelna'),
        row.get('nip'),
        row.get('ksiegowa'),
        row.get('kadrowa'),
    ]
    for department in ('accounting', 'payroll'):
        parts.append((row.get('owner_original') or {}).get(department))
        parts.append((row.get(f'{department}_substitute') or {}).get('zastepowany'))
    return ' '.join(str(part or '') for part in parts).casefold()


def _company_has_report(row, department, form):
    from app.companies.checking_obligations import report_applies
    return report_applies(row, form) or any(form_matches(entry['form'], form)
                                           for entry in declaration_entries(row, department))


class AccountingPage(QWidget):
    """Sprawdzanie: dynamic, read-only accounting/payroll pivots."""
    def __init__(self, actor_id, parent=None, *, department=None):
        super().__init__(parent)
        if department not in (None, 'accounting', 'payroll'):
            raise ValueError('Nieprawidłowy dział.')
        self._fixed_department = department
        self.actor_id = actor_id
        self.reader = TaskRunner(self)
        self.ezla_reader = TaskRunner(self)
        self.platnik_gui_reader = TaskRunner(self)
        self.platnik_gui_writer = CommandRunner(self)
        self.platnik_gui_timer = QTimer(self)
        self.platnik_gui_timer.setInterval(5000)
        self.platnik_gui_timer.timeout.connect(self._read_platnik_gui_checks)
        self.platnik_gui_checks = {}
        self.platnik_gui_month = None
        from app.remote.state import client
        remote = client()
        self.platnik_gui_supported = remote is None or 'platnik_gui_checks' in remote.server_features
        self._remote_ezla_month = None
        self.rows = []
        from app.utils.months import default_month_for_department
        self.period = default_month_for_department(department or 'accounting')
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('Sprawdzanie'))
        self.month = MonthPicker(self.period)
        bar.addWidget(self.month)
        self.tax_filter = QComboBox()
        for label, value in [('Wszystkie formy', ''), ('KPiR', 'KPIR'), ('Ryczałt', 'RYCZALT'),
                             ('Księgi rachunkowe', 'KH'), ('Pozostałe', 'OTHER')]:
            self.tax_filter.addItem(label, value)
        self.tax_filter.currentIndexChanged.connect(self.filter_rows)
        bar.addWidget(self.tax_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Szukaj firmy, NIP lub opiekuna (księgowość / kadry)')
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
                              '„—” oznacza brak odczytu, nie zero. Status przyjęcia wymaga UPO; daty wysyłki i UPO są odrębne. '
                              'Opiekun to pracownik faktycznie prowadzący firmę w tym miesiącu — z uwzględnieniem zastępstw.')
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
                                  ('Przyjęte z UPO', 'accepted'), ('Odrzucone lub z błędem', 'rejected')]:
                selector.addItem(title, status)
            self.declaration_filters[department] = selector
            declaration_layout.addWidget(selector)
            self.declaration_tables[department] = self._new_table()
            declaration_layout.addWidget(self.declaration_tables[department])
            selector.currentIndexChanged.connect(self.filter_rows)
            self._add_report(department, 'Deklaracje i UPO — wszystkie', 'declarations', declaration_panel)
            for form in DEFAULT_FORMS[department]:
                # 'VAT' i 'VAT-7' to ten sam obowiązek — bez duplikatu na liście.
                if department == 'accounting' and form_key(form) == 'VAT':
                    continue
                self._add_report(department, declaration_title(form), 'form:' + form_key(form), declaration_panel)
            if department == 'payroll':
                panel = QWidget()
                panel_layout = QVBoxLayout(panel)
                filters = QHBoxLayout()
                filters.addWidget(QLabel('Zakres'))
                self.contribution_scope = QComboBox()
                self.contribution_scope.addItem('Cała firma (DRA)', 'company')
                self.contribution_scope.addItem('Właściciel lub wspólnik', 'owner')
                self.contribution_scope.currentIndexChanged.connect(self.filter_rows)
                filters.addWidget(self.contribution_scope)
                self.platnik_gui_button = QPushButton('Sprawdź wysyłkę w Płatniku')
                self.platnik_gui_button.setToolTip(
                    'Sprawdź zaznaczone firmy. Bez zaznaczenia sprawdzisz wszystkie dostępne firmy z bazy Biuro.')
                self.platnik_gui_button.setVisible(self.platnik_gui_supported)
                self.platnik_gui_button.clicked.connect(self._queue_platnik_gui_checks)
                filters.addWidget(self.platnik_gui_button)
                filters.addStretch()
                panel_layout.addLayout(filters)
                note = QLabel('Kwoty w PLN z deklaracji Płatnika. „—” oznacza brak kwoty w odczycie lub na formularzu. '
                              'Aby uzupełnić starsze miesiące, pobierz formularze ZUS ponownie.')
                note.setWordWrap(True)
                panel_layout.addWidget(note)
                self.platnik_gui_note = QLabel()
                self.platnik_gui_note.setWordWrap(True)
                panel_layout.addWidget(self.platnik_gui_note)
                self.contribution_table = self._new_table()
                panel_layout.addWidget(self.contribution_table)
                self._add_report(department, 'Składki ZUS (miesięcznie)', 'contributions', panel)
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
                from app.accounting.annual_zus_ui import AnnualZusPanel
                self.annual_zus_panel = AnnualZusPanel(actor_id, self)
                self._add_report(department, 'ZUS IWA — Płatnik (rocznie)', 'annual:IWA', self.annual_zus_panel)
                self._add_report(department, 'ZUS ZSWA — Płatnik (rocznie)', 'annual:ZSWA', self.annual_zus_panel)
            self.tabs.addTab(sections, label)
            report_selector.currentIndexChanged.connect(self._report_changed)
        self.table = self.tables['accounting']
        layout.addWidget(self.tabs)
        from app.accounting.refresh_ui import CheckingRefreshControls
        self.refresh_controls = CheckingRefreshControls(actor_id,self.refresh_companies,self.selected_company_ids,
            lambda kind:self.holiday_panel.year.value() if kind=='platnik' else
            self.annual_zus_panel.year.value() if kind=='platnik_annual' else self.month.monthText(),self.refresh_async,self)
        layout.addWidget(self.refresh_controls)
        self.source_note = QLabel()
        self.source_note.setWordWrap(True)
        layout.addWidget(self.source_note)
        self.tabs.currentChanged.connect(self._report_changed)
        self.month.monthChanged.connect(self.refresh_async)
        self.apply_department_default()
        if self._fixed_department:
            self.tabs.tabBar().hide()
            self.tabs.currentChanged.connect(self.apply_department_default)
        self.filter_rows()

    def set_month(self, month: str):
        datetime.strptime(month, '%Y-%m')
        self.month.setCurrentText(month)
        self.period = month

    def _add_report(self, department, label, key, widget):
        stack = self.report_stacks[department]
        index = stack.indexOf(widget)
        if index < 0:
            index = stack.addWidget(widget)
        self.report_indices[department][key] = index
        selector = self.report_selectors[department]
        # Ten sam raport bywa dodawany drugi raz (przycisk + lista formularzy z firmy)
        # i wtedy na liście pojawiała się druga pozycja „Deklaracja VAT-7”.
        if selector.findData(key) >= 0:
            return
        selector.addItem(label, key)

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
                    if key not in self.report_indices[department]:
                        index = self.report_indices[department]['declarations']
                        self.report_indices[department][key] = index
                        selector.addItem(declaration_title(entry['form']), key)
        finally:
            selector.blockSignals(blocked)

    @staticmethod
    def _new_table():
        table = FrozenFirstColumnTable(0, 0)
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

    def annual_zus_selected(self):
        return self.tabs.currentIndex() == 1 and str(self.report_selectors['payroll'].currentData()).startswith('annual:')

    def contributions_selected(self):
        return self.tabs.currentIndex() == 1 and self.report_selectors['payroll'].currentData() == 'contributions'

    def update_period_controls(self,*_):
        annual = self.yearly_selected() or self.annual_zus_selected()
        contributions = self.contributions_selected()
        for widget in (self.month,self.tax_filter,self.source_note):
            widget.setVisible(not annual)
        for widget in (self.attention, self.message):
            widget.setVisible(not annual and not contributions)
        if hasattr(self,'refresh_controls'):
            payroll = self.tabs.currentIndex()==1
            self.refresh_controls.buttons['enova'].setVisible(not annual and not contributions)
            self.refresh_controls.buttons['ezla'].setVisible(payroll and not annual and not contributions)
            self.refresh_controls.buttons['platnik'].setVisible(self.yearly_selected())
            self.refresh_controls.buttons['platnik_annual'].setVisible(self.annual_zus_selected())
            self.refresh_controls.buttons['platnik_declarations'].setVisible(payroll and not annual)

    def refresh_companies(self):
        if self.yearly_selected():
            return self.holiday_panel.payload.get('companies',[])
        if self.annual_zus_selected():
            return self.annual_zus_panel.companies()
        department = 'payroll' if self.tabs.currentIndex() == 1 else 'accounting'
        form = self._selected_form(department)
        return [row for row in self.rows if not form or _company_has_report(row, department, form)]

    def selected_company_ids(self):
        department = 'payroll' if self.tabs.currentIndex()==1 else 'accounting'
        key = self.report_selectors[department].currentData() or 'documents'
        table = (self.annual_zus_panel.table if key.startswith('annual:') else
                 self.holiday_panel.table if key=='holidays' else self.ezla_panel.table if key=='ezla'
                 else self.contribution_table if key=='contributions'
                 else self.hr_table if key.startswith('hr') else self.comparison_tables['accounting'] if key=='comparison'
                 else self.declaration_tables[department] if key=='declarations' or key.startswith('form:')
                 else self.tables[department])
        by_name = {}
        display_name = short_company_display_name if key.startswith('hr') else company_display_name
        for company in self.refresh_companies():
            name = display_name(company)
            by_name.setdefault(name, []).append(company['id'])
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
        if self.annual_zus_selected():
            self.annual_zus_panel.refresh()
            return
        month = self.month.monthText()
        if self.contributions_selected() and month != self.period:
            self.contribution_table.setRowCount(0)
        if self.contributions_selected():
            self._read_platnik_gui_checks()
        self.reader.submit(lambda: list_accounting(self.actor_id, month),
                           lambda rows: self.render(rows, month), self.failed, key=month)
        if self._ezla_selected():
            self._refresh_ezla(month)

    def _ezla_selected(self):
        return self.tabs.currentIndex() == 1 and self.report_selectors['payroll'].currentData() == 'ezla'

    def _report_changed(self, *_):
        self.filter_rows()
        if self.contributions_selected():
            self._read_platnik_gui_checks()
        else:
            self.platnik_gui_timer.stop()
        if self.isVisible() and self._ezla_selected():
            self._refresh_ezla(self.month.monthText())

    def _refresh_ezla(self, month):
        from app.integrations.ezla.sync import load_reports

        def loaded(batch):
            if month == self.month.monthText():
                self._remote_ezla_month = month
                self.ezla_panel.set_remote(batch)
                if self._ezla_selected() and month == self.period:
                    self.filter_rows()

        def failed(exc):
            if month == self.month.monthText() and self._ezla_selected():
                self.ezla_panel.failed(exc)

        self.ezla_reader.submit(lambda: load_reports(self.actor_id, month), loaded, failed, key=month)

    def failed(self, exc):
        self.source_note.setText('Nie udało się odczytać danych: ' + str(exc))

    def render(self, rows, month):
        rows = [
            {
                **row,
                'checking_period': month,
            }
            for row in rows
        ]
        for row in rows:
            for field in ('checking', 'snapshot', 'jpk', 'hr', 'checking_obligations'):
                row[field] = decoded(row.get(field))
        self.rows, self.period = rows, month
        for department in self.tables:
            self._update_report_choices(department)
        self.filter_rows()

    def _report_attention(self, row, department, today, horizon):
        selected = self._selected_form(department)
        if selected:
            entries = [entry for entry in declaration_entries(row, department) if form_matches(entry['form'], selected)]
            from app.accounting.report_types import EVENT_FORMS
            if form_key(selected) in EVENT_FORMS and not entries and decoded(row.get('platnik_declarations')).get('imported_at'):
                return False  # No registration event is not a missed monthly filing.
            return not entries or any(declaration_issue(entry) for entry in entries)
        report_key = self.report_selectors[department].currentData() or 'documents'
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
        department = 'payroll' if self.tabs.currentIndex() == 1 else 'accounting'
        report = self.report_selectors[department].currentData() or 'documents'
        self.report_stacks[department].setCurrentIndex(self.report_indices[department][report])
        if self.yearly_selected():
            self.holiday_panel.set_company_filter(self.search.text().strip())
            return
        if self.annual_zus_selected():
            form = self.report_selectors['payroll'].currentData().split(':', 1)[1]
            self.annual_zus_panel.set_filter(form, self.search.text())
            return
        words = self.search.text().casefold().split()
        selected = self.tax_filter.currentData()
        rows = []
        for row in self.rows:
            if selected and (company_accounting_type(row) or 'OTHER') != selected:
                continue
            if words:
                haystack = _search_haystack(row)
                if report == 'contributions' and self.contribution_scope.currentData() == 'owner':
                    haystack += ' ' + ' '.join(decoded(row.get('platnik_declarations')).get('contributions', {}))
                if not all(word in haystack for word in words):
                    continue
            rows.append(row)
        if report == 'contributions':
            self._render_contributions(rows if self.period == self.month.monthText() else [])
            apply_header_sort(self.contribution_table)
            return
        today = datetime.now(ZoneInfo('Europe/Warsaw')).date()
        horizon = self.hr_range.currentData()
        form = self._selected_form(department)
        applicable = [row for row in rows if not form or _company_has_report(row, department, form)]
        only_attention = self.attention.isChecked()
        visible = [row for row in applicable if not only_attention or self._report_attention(row, department, today, horizon)]
        if report == 'documents':
            self._render_table(visible, department)
        elif report == 'comparison':
            self._render_comparisons(visible, department)
        elif report == 'declarations' or form:
            self._render_declarations(visible, department)
        elif report.startswith('hr'):
            self._render_hr(visible, today, horizon)
            apply_header_sort(self.hr_table)
        elif report == 'ezla':
            if self._remote_ezla_month != self.period:
                self.ezla_panel.set_remote({'reports': []})
            self.ezla_panel.set_companies(visible, self.period, only_attention)
        table = self.report_stacks[department].currentWidget()
        if isinstance(table, FrozenFirstColumnTable):
            apply_header_sort(table)
        missing_reports = sum(not decoded(row.get('checking')) for row in rows)
        missing_payroll = sum(documents(row, 'payroll') is None for row in rows)
        missing_declarations = sum(decoded(row.get('checking')).get('declarations') is None for row in rows)
        self.source_note.setText(f'Firmy: {len(rows)}. Brak pełnego odczytu dokumentów: {missing_reports}; '
                                 f'brak odczytu kadr: {missing_payroll}; brak odczytu deklaracji: {missing_declarations}. '
                                 'Listy płac odczytuje Enova, formularze ZUS — Płatnik. Brak potwierdzenia nie oznacza przyjęcia. '
                                 'Liczba do faktury pochodzi z Widoku miesięcznego.')

    def _render_contributions(self, companies):
        scope = self.contribution_scope.currentData()
        table = self.contribution_table
        headers = ('Firma', 'NIP płatnika', *CONTRIBUTION_COLUMNS.values(), 'Formularz', 'Status wysyłki',
                   'Agent Płatnika')
        if not table.columnCount():
            table.setColumnCount(len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.setColumnHidden(len(headers) - 1, not self.platnik_gui_supported)
        rows = []
        for company in companies:
            snapshot = decoded(company.get('platnik_declarations'))
            breakdown = snapshot.get('contributions', {})
            try:
                company_nip = normalize_nip(company.get('nip'))
            except ValueError:
                company_nip = ''
            nips = [company_nip]
            if scope == 'owner' and is_civil(company):
                nips = [nip for nip in breakdown if nip != company_nip] or ['']
            for nip in nips:
                rows.append((company, nip, breakdown.get(nip, {}).get(scope) or {}))
        table.setRowCount(len(rows))
        for index, (company, nip, report) in enumerate(rows):
            name = _item(company_display_name(company))
            name.setData(Qt.ItemDataRole.UserRole, company['id'])
            cells = [name, _item(nip or None)]
            for key in CONTRIBUTION_COLUMNS:
                value = report.get('amounts', {}).get(key)
                cell = _item(value.replace('.', ',') if value is not None else None)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                cells.append(cell)
            source = report.get('source')
            document = f"{source} {' / '.join(report['reports'])}" if source else None
            document_item = _item(document)
            document_item.setData(Qt.ItemDataRole.UserRole, report)
            cells.extend((document_item, _item(report.get('problem') or report.get('status') or 'Brak danych')))
            for column, cell in enumerate(cells):
                table.setItem(index, column, cell)
        self._paint_platnik_gui_checks()
        table.resizeColumnsToContents()
        if self.platnik_gui_supported:
            table.setColumnWidth(table.columnCount() - 1, 200)
        self.source_note.setText('')

    def _paint_platnik_gui_checks(self):
        if not self.platnik_gui_supported:
            return
        table = self.contribution_table
        checks = self.platnik_gui_checks if self.platnik_gui_month == self.month.monthText() else {}
        column = table.columnCount() - 1
        for row in range(table.rowCount()):
            company_id = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            nip = table.item(row, 1).text()
            report = table.item(row, column - 2).data(Qt.ItemDataRole.UserRole)
            text, details = display_check(checks.get((company_id, nip), {}), report or {})
            table.setItem(row, column, _item(text, details))

    def _read_platnik_gui_checks(self):
        if not self.platnik_gui_supported or not self.contributions_selected() or not self.isVisible():
            self.platnik_gui_timer.stop()
            return
        from app.services.platnik_gui import read_checks
        month = self.month.monthText()
        if self.platnik_gui_month != month:
            self.platnik_gui_note.clear()

        def loaded(rows):
            if self.month.monthText() != month or not self.contributions_selected():
                return
            self.platnik_gui_month = month
            self.platnik_gui_checks = {(row['company_id'], row['nip']): row for row in rows}
            self._paint_platnik_gui_checks()
            apply_header_sort(self.contribution_table)
            if any(row['state'] in ('queued', 'running') for row in rows):
                if not self.platnik_gui_timer.isActive():
                    self.platnik_gui_timer.start()
            else:
                self.platnik_gui_timer.stop()

        def failed(_error):
            if self.month.monthText() == month and self.contributions_selected():
                self.platnik_gui_note.setText('Nie udało się pobrać stanu zadań agenta Płatnika.')

        self.platnik_gui_reader.submit(lambda: read_checks(self.actor_id, month), loaded, failed, key=month)

    def _queue_platnik_gui_checks(self):
        from app.services.platnik_gui import request_checks
        company_ids = self.selected_company_ids() or None
        month = self.month.monthText()
        self.platnik_gui_button.setEnabled(False)
        self.platnik_gui_note.setText('Zlecam sprawdzenie w Płatniku…')

        def queued(result):
            self.platnik_gui_button.setEnabled(True)
            if self.month.monthText() == month:
                message = f"Zlecono: {result['queued']}. Już w kolejce: {result['existing']}."
                if result.get('skipped'):
                    message += f" Pominięto firmy bez poprawnego NIP-u płatnika: {result['skipped']}."
                self.platnik_gui_note.setText(message)
                self._read_platnik_gui_checks()

        def failed(error):
            self.platnik_gui_button.setEnabled(True)
            if self.month.monthText() == month:
                self.platnik_gui_note.setText(
                    str(error) if isinstance(error, (ValueError, PermissionError))
                    else 'Nie udało się zlecić odczytu agentowi Płatnika.'
                )

        self.platnik_gui_writer.submit(lambda: request_checks(self.actor_id, company_ids, month), queued, failed)

    @staticmethod
    def _owner_item(row, department):
        """Opiekun: zastępca, gdy zastępuje (z informacją, kogo), inaczej opiekun stały."""
        field = 'kadrowa' if department == 'payroll' else 'ksiegowa'
        name = row.get(field) or '—'
        item = _item(name)
        substitute = row.get(f'{department}_substitute')
        if substitute:
            original = (row.get('owner_original') or {}).get(department) or substitute.get('zastepowany') or ''
            details = [f'Zastępstwo za: {original or "—"}']
            if substitute.get('do_dnia'):
                details.append(f'Do: {_date(substitute["do_dnia"])}')
            item.setToolTip('\n'.join(details))
            item.setForeground(QColor('#1a5fb4'))
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
        return item

    def _render_table(self, rows, department):
        table = self.tables[department]
        simple_ids = {row['id'] for row in rows if department == 'accounting' and simple_accounting(row)}
        simple_only = bool(rows and len(simple_ids) == len(rows))
        kinds = set((SIMPLE_DOCUMENT_TYPES if simple_only else BASE_TYPES) if department == 'accounting' else ())
        forms = set(DEFAULT_FORMS[department])
        by_company = {row['id']: declarations(row, department) for row in rows}
        for row in rows:
            kinds.update(documents(row, department) or {})
            if department == 'accounting':
                kinds.update(previous_active_types(row, self.period))
            forms.update(by_company[row['id']])
            for old in row.get('history', []):
                forms.update(entry['form'] for entry in old.get('declarations') or [] if entry['department'] == department)
        kinds = sorted(kinds, key=lambda value: (BASE_TYPES.index(value) if value in BASE_TYPES else len(BASE_TYPES), value))
        forms = sorted(forms, key=lambda value: (DEFAULT_FORMS[department].index(value) if value in DEFAULT_FORMS[department] else len(DEFAULT_FORMS[department]), value))
        labels = [TYPE_LABELS.get(kind, kind) for kind in kinds]
        # Keep distinct definitions distinct even when their friendly names coincide.
        labels = [label + (' (' + kind + ')' if labels.count(label) > 1 else '') for label, kind in zip(labels, kinds)]
        headers = ['Firma', 'NIP', 'Forma opodatkowania', 'Opiekun (obecny, z zastępstwami)', *labels,
                   'Razem' if simple_only else 'Dokumenty razem']
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
            simple = row['id'] in simple_ids
            historical = previous_active_types(row, self.period) if department == 'accounting' else set()
            cells = [
                _item(company_display_name(row)),
                _item(row.get('nip')),
                _item(row.get('forma_opodatkowania')),
            ]
            cells.append(self._owner_item(row, department))
            for kind in kinds:
                known = counts is not None and (kind in counts or bool(report))
                if simple and kind not in SIMPLE_DOCUMENT_TYPES:
                    known = False
                value = counts.get(kind, 0) if known else None
                item = _item(value, DOCUMENT_NAMES.get(kind, 'Rodzaj dokumentu: ' + kind))
                if value == 0 and kind in historical:
                    paint_state(item, 'error')
                    item.setToolTip('Brak dokumentów tego rodzaju w wybranym miesiącu; występowały w tej firmie w poprzednim miesiącu.\nRodzaj: ' + kind)
                cells.append(item)
            detail = ''
            if simple:
                snapshot = decoded(row.get('snapshot'))
                breakdown = report.get('accounting_details', {}).get('by_type') if report else snapshot.get('by_type')
                if breakdown:
                    detail = '\n'.join(f'{symbol} — {DOCUMENT_NAMES.get(symbol, symbol)}: {amount}'
                                       for symbol, amount in sorted(breakdown.items()))
            cells.append(_item(sum(counts.values()) if counts is not None else None, detail))
            if department == 'accounting':
                cells.append(_item(row.get('ilosc_dok'), 'Licznik miesięczny, z zachowaniem ręcznych korekt.'))
            stamp = report.get('imported_at') if counts is not None or report.get('declarations') is not None else None
            if department == 'accounting' and counts is not None:
                stamp = report.get('accounting_imported_at') or stamp
            if department == 'accounting' and not report:
                stamp = decoded(row.get('snapshot')).get('imported_at')
            cells.append(_item(_date(stamp)))
            entries = by_company[row['id']]
            for form in forms:
                entry = entries.get(form, {})
                status = entry.get('status_text') or STATUSES.get(entry.get('status'), 'Brak danych' if report.get('declarations') is not None else 'Nie sprawdzono')
                if form_key(entry.get('form') or form) in NOT_SENT_FORMS and not entry.get('status_text'):
                    status = 'Nie wysyłane przez program'
                    entry = {**entry, 'status': 'not_sent'}
                # Trzy osobne daty i numer: wysyłka, potwierdzenie UPO, numer UPO.
                sent_tooltip = 'Odczyt statusu: ' + _date(entry.get('imported_at') or report.get('imported_at'))
                if entry.get('upo_confirmed_at'):
                    sent_tooltip += '\nPrzyjęte wg statusu: ' + _date(entry['upo_confirmed_at'])
                cells.append(_item(status, 'Odczyt statusu: ' + _date(entry.get('imported_at') or report.get('imported_at'))))
                cells.append(_item(_date_range(entry, 'sent_at', 'sent_to'), sent_tooltip))
                upo_item = _item(f"{entry['upo_count']}/{entry['count']}" if 'count' in entry else ('Tak' if entry.get('upo') or entry.get('upo_confirmed_at') else '—'),
                                 _upo_tooltip(entry))
                cells.append(upo_item)
                cells.append(_item(_date_range(entry)))
            for column, item in enumerate(cells):
                if column==0:
                    item.setData(Qt.ItemDataRole.UserRole,row['id'])
                table.setItem(index, column, item)
        table.resizeColumnsToContents()
        if rows:
            table.setColumnWidth(0, min(330, max(190, table.columnWidth(0))))

    def _render_comparisons(self, rows, department):
        from app.utils.months import previous_month
        table = self.comparison_tables[department]
        previous = previous_month(self.period)
        # Kolumna wybranego miesiąca i kolumna miesiąca wcześniejszego — z nazwami,
        # żeby nie trzeba było liczyć, który to miesiąc.
        headers = ['Firma', 'Opiekun (obecny, z zastępstwami)', 'Rodzaj', f'{self.period} (wybrany)',
                   f'{previous} (poprzedni)', 'Zmiana', 'Zmiana %']
        records = []
        for row in rows:
            kinds = set(documents(row, department) or {}) | previous_active_types(row, self.period)
            for kind in [None, *sorted(kinds)]:
                values = comparison(row, self.period, department, kind)
                percent = 'Nowe' if values['new'] else (f"{values['percent']:+.1f}%" if values['percent'] is not None else '—')
                records.append([company_display_name(row), row.get('ksiegowa') or row.get('kadrowa') or '—',
                                'Razem' if kind is None else TYPE_LABELS.get(kind, kind),
                                values['current'], values['previous'], values['delta'], percent, row['id']])
        self._fill(table, headers, records)
        # Firma może wystąpić kilka razy (raz na rodzaj dokumentu) — ale opiekun jest ten sam.
        by_id = {row['id']: row for row in rows}
        for index, record in enumerate(records):
            source = by_id[record[-1]]
            table.item(index, 0).setData(Qt.ItemDataRole.UserRole, source['id'])
            if source.get(f'{department}_substitute'):
                owner_item = self._owner_item(source, department)
                cell = table.item(index, 1)
                cell.setText(owner_item.text())
                cell.setToolTip(owner_item.toolTip())
                cell.setForeground(QColor('#1a5fb4'))
        for index, record in enumerate(records):
            delta = record[5]
            if delta is not None and delta != 0:
                item = table.item(index, 5)
                item.setForeground(QColor('#b45309' if delta < 0 else '#166534'))
                if record[3] == 0 and record[4] > 0:
                    paint_state(table.item(index, 3), 'error')
        table.setToolTip('Porównanie wyłącznie z poprzednim miesiącem. Brak odczytu: —. Przy zerowej podstawie nie obliczamy procentu.')

    def _declaration_name(self, entry, row):
        """Nazwa pozycji: „PIT-5”/„PIT-5L” zależnie od formy firmy, reszta wg skrótu."""
        if form_key(entry['form']) == 'PIT':
            return pit_form(row)
        return form_label(entry['form'])

    def _render_declarations(self, rows, department):
        table = self.declaration_tables[department]
        form = self._selected_form(department)
        selected = self.declaration_filters[department].currentData()
        records, receipts = [], []
        for row in rows:
            entries = declaration_entries(row, department)
            apply_not_sent = _not_sent_entry(row, department, form)
            if form:
                entries = [entry for entry in entries if form_matches(entry['form'], form)]
                if apply_not_sent:
                    entries = entries + [apply_not_sent]
            owner = row.get('kadrowa' if department == 'payroll' else 'ksiegowa') or '—'
            if not entries and not selected:
                # Nazwa formularza wg firmy (PIT-5/PIT-5L), nie wg podpisu zakładki.
                empty_name = self._declaration_name(apply_not_sent or {'form': form}, row) if form else '—'
                records.append([company_display_name(row), owner, '—', empty_name,
                                'Brak danych' if decoded(row.get('checking')).get('declarations') is not None else 'Nie sprawdzono',
                                '—', '—', '—', '—', 'Brak danych źródłowych', '—', '', False, row['id']])
                receipts.append('')
            for entry in sorted(entries, key=lambda item: (item['form'], item.get('employee_name', ''))):
                status = entry.get('status')
                matches = {'to_send': status in ('prepared', 'not_sent'),
                           'sent': status in ('sent', 'processing', 'accepted'),
                           'awaiting': status in ('sent', 'processing', 'accepted') and not entry.get('upo'),
                           'accepted': status == 'accepted' and bool(entry.get('upo')),
                           'rejected': status in ('rejected', 'failed')}
                if selected and not matches[selected]:
                    continue
                name = self._declaration_name(entry, row)
                employee = f"{entry['employee_name']} ({entry['employee_id']})" if entry.get('employee_name') else '—'
                # PIT-y nie są wysyłane przez program, więc brak wysyłki nie jest zarzutem.
                issue = 'Nie wysyłane przez program' if form_key(entry['form']) in NOT_SENT_FORMS else declaration_issue(entry)
                # Uwaga: kolejność kolumn = nagłówki w _fill; ostatnia kolumna to stan
                # „bez żółtego” (PIT-y, których dziś nie wysyłamy).
                records.append([company_display_name(row), owner, employee, name,
                                entry.get('status_text') or STATUSES.get(entry.get('status'), 'Nie sprawdzono'),
                                _date(entry.get('sent_at')),
                                'Tak' if (entry.get('upo') or entry.get('upo_confirmed_at')) else '—',
                                _date(entry.get('upo_at')),
                                str(entry.get('upo') or '—'), issue or 'OK',
                                _date(entry.get('created_at')),
                                (f"Status źródłowy: {entry['raw_status']}" if entry.get('raw_status') else '') +
                                (f"{'; ' if entry.get('raw_status') else ''}Status zestawu Płatnika: {entry['filing_status_raw']}"
                                 if entry.get('filing_status_raw') else '') +
                                (f"{'; ' if entry.get('raw_status') or entry.get('filing_status_raw') else ''}"
                                 f"dokument {entry['source_id']}, identyfikator raportu {entry.get('report_id') or '—'}"
                                 if entry.get('source_system') == 'platnik' and entry.get('source_id') else ''),
                                form_key(entry['form']) in NOT_SENT_FORMS, row['id']])
                receipts.append(_upo_tooltip(entry))
        self._fill(table, ['Firma', 'Opiekun (obecny, z zastępstwami)', 'Pracownik', 'Deklaracja', 'Status',
                           'Data wysyłki', 'UPO', 'Data UPO', 'Nr UPO', 'Do sprawdzenia',
                           'Utworzono'], records)
        table.setToolTip('Kolumna „Deklaracja” to nr formularza. PIT-y nie są wysyłane przez program, '
                         'więc nie są oznaczane jako niewysłane. Źródło odczytu jest w tooltipie kolumny „Do sprawdzenia”.')
        by_id = {row['id']: row for row in rows}
        for index, record in enumerate(records):
            table.item(index, 6).setToolTip(receipts[index])
            table.item(index, 8).setToolTip(receipts[index])
            source = by_id[record[-1]]
            table.item(index, 0).setData(Qt.ItemDataRole.UserRole, source['id'])
            if source.get(f'{department}_substitute'):
                owner_item = self._owner_item(source, department)
                table.item(index, 1).setText(owner_item.text())
                table.item(index, 1).setToolTip(owner_item.toolTip())
                table.item(index, 1).setForeground(QColor('#1a5fb4'))
            # Źródło odczytu trafia do tooltipu, nie do osobnej kolumny.
            issue = table.item(index, 9)
            issue.setToolTip(record[11] or 'Status odczytu bez danych o źródle.')
            if record[9] not in ('OK', 'Brak danych źródłowych') and not record[12]:
                issue.setBackground(QColor('#fef3c7'))
                issue.setForeground(QColor('#92400e'))

    def _render_hr(self, rows, today, days):
        records, missing, failed = [], 0, 0
        selected = self.report_selectors['payroll'].currentData() or ''
        kind = selected[3:] if selected.startswith('hr:') else None
        for row in rows:
            before = len(records)
            employer = short_company_display_name(row)
            report = decoded(row.get('hr'))
            if report.get('events') is None:
                missing += 1
            if report.get('last_attempt_failed'):
                failed += 1
            for event in report.get('events') or []:
                if kind and event['event_type'] != kind:
                    continue
                if kind is None and event['event_type'] == 'employee':
                    continue
                status = hr_status(event, today, days or 90)
                if status is None:
                    continue
                if days is not None and status in ('Bezterminowa', 'Późniejszy termin'):
                    continue
                employee = event['employee_name'] + ' (' + event['employee_id'] + ')'
                records.append([employer, row.get('nip'), row.get('kadrowa') or '—', employee,
                                event.get('document_number') or '—', event.get('detail') or HR_LABELS[event['event_type']],
                                event.get('due_date') or '—', status, _date(report.get('imported_at')), row['id']])
            if len(records) == before:
                state = 'Brak odczytu' if report.get('events') is None else 'Brak terminów w wybranym zakresie'
                records.append([employer, row.get('nip'), row.get('kadrowa') or '—', '—', '—', HR_LABELS.get(kind, '—'), '—',
                                state, _date(report.get('imported_at')), row['id']])
        records.sort(key=lambda record: (0 if record[7] in ('Po terminie', 'Brak terminu') else 1, record[6], record[0]))
        self._fill(self.hr_table, ['Firma', 'NIP', 'Kadrowa (obecna, z zastępstwami)', 'Pracownik', 'Numer dokumentu',
                                  'Rodzaj', 'Data', 'Stan', 'Ostatni odczyt'], records)
        by_id = {row['id']: row for row in rows}
        for index, record in enumerate(records):
            source = by_id[record[-1]]
            self.hr_table.item(index, 0).setData(Qt.ItemDataRole.UserRole, source['id'])
            if source.get('payroll_substitute'):
                owner_item = self._owner_item(source, 'payroll')
                self.hr_table.item(index, 2).setText(owner_item.text())
                self.hr_table.item(index, 2).setToolTip(owner_item.toolTip())
                self.hr_table.item(index, 2).setForeground(QColor('#1a5fb4'))
            if record[7] in ('Po terminie', 'Brak terminu'):
                paint_state(self.hr_table.item(index, 7), 'error')
        self.hr_note.setText(f'Stan terminów na {today.isoformat()}. Brak odczytu dla {missing} firm. '
                             f'Nieudana ostatnia próba dla {failed} firm. '
                             'Zaległe terminy tylko z bieżącego roku. '
                             'Umowy bezterminowe są widoczne w „Wszystkie pobrane”.')

    @staticmethod
    def _fill(table, headers, records):
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(records))
        for row, values in enumerate(records):
            for column, value in enumerate(values[:len(headers)]):
                table.setItem(row, column, _item(value))
        table.resizeColumnsToContents()
        if records:
            table.setColumnWidth(0, min(330, max(190, table.columnWidth(0))))
