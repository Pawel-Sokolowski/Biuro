"""Recurring filing selection beside the company's annual checklist."""
from datetime import date
from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QDateEdit, QComboBox, QCheckBox
from app.companies.checking_obligations import (MONTHLY_FORMS, VAT_PERIODS, default_forms, load,
                                                profile, save)
from app.integrations.enova.document_counts import DOCUMENT_TYPE_LABELS


class MonthlySetup(QWidget):
    def __init__(self, company_id, parent=None, company=None, *, defer_load=False):
        super().__init__(parent)
        self.company = company or {}
        self.saved = {} if defer_load else load(company_id)
        self.edits = {}
        self.loading = True
        box = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel('Obowiązuje od miesiąca:'))
        self.month = QDateEdit(QDate(date.today().year, date.today().month, 1))
        self.month.setDisplayFormat('yyyy-MM')
        self.month.setCalendarPopup(True)
        row.addWidget(self.month)
        row.addWidget(QLabel('Rozliczenie VAT:'))
        self.vat = QComboBox()
        for key, label in VAT_PERIODS:
            self.vat.addItem(label, key)
        row.addWidget(self.vat)
        box.addLayout(row)
        self.checks, self.cadences = {}, {}
        grid = QGridLayout()
        for index, (key, label) in enumerate(MONTHLY_FORMS):
            check = QCheckBox(label)
            self.checks[key] = check
            grid.addWidget(check, index // 2, (index % 2) * 2)
            if key in ('PIT', 'CIT'):
                cadence = QComboBox()
                cadence.addItem('Miesięcznie', 'monthly')
                cadence.addItem('Kwartalnie', 'quarterly')
                self.cadences[key] = cadence
                grid.addWidget(cadence, index // 2, (index % 2) * 2 + 1)
                cadence.currentIndexChanged.connect(self.changed)
            check.toggled.connect(self.changed)
        box.addLayout(grid)
        # Rodzaje dokumentów z Enovy wliczane do liczby dokumentów za miesiąc.
        types_box = QWidget()
        types_layout = QVBoxLayout(types_box)
        types_layout.addWidget(QLabel('Dokumenty z Enovy wliczane do liczby dokumentów za miesiąc:'))
        self.type_checks = {}
        types_grid = QGridLayout()
        for index, (key, label) in enumerate(DOCUMENT_TYPE_LABELS):
            check = QCheckBox(label)
            check.toggled.connect(self.changed)
            self.type_checks[key] = check
            types_grid.addWidget(check, index // 4, index % 4)
        types_layout.addLayout(types_grid)
        self.types_all = QCheckBox('Wszystkie rodzaje (domyślnie)')
        self.types_all.setToolTip('Odznaczenie pozwala wybrać tylko wybrane rodzaje. Ustawienie działa od wybranego miesiąca.')
        self.types_all.toggled.connect(self._types_all_changed)
        types_layout.addWidget(self.types_all)
        box.addWidget(types_box)
        note = QLabel('Zaznacz raporty dotyczące firmy. Tylko DRA: zaznacz DRA, odznacz RCA/RSA/RPA/RZA. '
                      'Ustawienia obowiązują od wybranego miesiąca do kolejnej zmiany. '
                      'Przy VAT kwartalnym ewidencja JPK pozostaje miesięczna, część deklaracyjna jest kwartalna. '
                      'Zgłoszenia ZUA/ZZA/ZWUA/ZIUA/ZCNA są zdarzeniami, nie obowiązkiem tworzenia dokumentu co miesiąc. '
                      'Firma bez VAT nie pojawi się w JPK VAT. Ustawienia nie usuwają pobranych dokumentów.')
        note.setWordWrap(True)
        box.addWidget(note)
        self.import_note = QLabel()
        self.import_note.setWordWrap(True)
        box.addWidget(self.import_note)
        self.vat.currentIndexChanged.connect(self.changed)
        self.month.dateChanged.connect(self.show_month)
        self.show_month()

    def period(self):
        return self.month.date().toString('yyyy-MM')

    def show_month(self, *_):
        self.loading = True
        value = profile({**self.saved,'periods': {**self.saved.get('periods', {}), **self.edits}}, self.period())
        imported = value.get('_imported',{})
        detected = ', '.join(dict(MONTHLY_FORMS).get(key,key) for key in imported.get('forms',{}))
        if detected:
            self.import_note.setText('Wykryto w imporcie: '+detected+'. Brak dokumentu nie wyłącza pozostałych obowiązków; ręczne ustawienia mają pierwszeństwo.')
        elif value.get('_configured') or value.get('_imported'):
            self.import_note.setText('Brak wykrytych deklaracji za wybrany miesiąc.')
        else:
            self.import_note.setText('Firma nie ma jeszcze ustawień — poniżej zakres domyślny z danych rejestrowych '
                                     '(status VAT, forma opodatkowania). Zaznacz i zapisz, aby go potwierdzić.')
        self.vat.setCurrentIndex(max(0, self.vat.findData(value['vat_period'])))
        fallback = value['forms'] if (value.get('_configured') or value.get('_imported')) else {
            **default_forms(self.company)['forms'], **value['forms']}
        for key, check in self.checks.items():
            cadence = fallback.get(key, 'none')
            check.setChecked(cadence != 'none')
            if key in self.cadences:
                self.cadences[key].setCurrentIndex(max(0, self.cadences[key].findData(cadence)))
        # Rodzaje dokumentów: brak wpisu = wszystkie (tak jak dotychczas).
        # Przy zmianie miesiąca używamy jego zapisanych ustawień, a nie bieżącej edycji.
        chosen = (self.saved.get('periods', {}).get(self.period()) or self.edits.get(self.period()) or {}).get('document_types')
        all_types = not chosen
        self.types_all.setChecked(all_types)
        for key, check in self.type_checks.items():
            check.setChecked(all_types or key in chosen)
            check.setEnabled(not all_types)
        self.loading = False

    def _types_all_changed(self, checked):
        for check in self.type_checks.values():
            check.setEnabled(not checked)
            if checked:
                check.blockSignals(True)
                check.setChecked(True)
                check.blockSignals(False)
        self.changed()

    def selected_types(self):
        if self.types_all.isChecked():
            return None  # wszystkie rodzaje
        return [key for key, check in self.type_checks.items() if check.isChecked()]

    def changed(self, *_):
        if self.loading:
            return
        self.edits[self.period()] = {
            'vat_period': self.vat.currentData(),
            'forms': {key: (self.cadences[key].currentData() if key in self.cadences else 'monthly')
                      if check.isChecked() else 'none' for key, check in self.checks.items()},
            'document_types': self.selected_types(),
        }

    def persist(self, company_id, actor_id):
        if self.edits:
            save(company_id, self.edits, actor_id)
