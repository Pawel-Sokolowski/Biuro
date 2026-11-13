"""Recurring filing selection beside the company's annual checklist."""
from datetime import date
from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QDateEdit, QComboBox, QCheckBox
from app.companies.checking_obligations import MONTHLY_FORMS, VAT_PERIODS, load, profile, save


class MonthlySetup(QWidget):
    def __init__(self, company_id, parent=None):
        super().__init__(parent)
        self.saved = load(company_id)
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
        note = QLabel('Zaznacz raporty dotyczące firmy. Tylko DRA: zaznacz DRA, odznacz RCA/RSA/RPA/RZA. '
                      'Ustawienia obowiązują od wybranego miesiąca do kolejnej zmiany. '
                      'Przy VAT kwartalnym ewidencja JPK pozostaje miesięczna, część deklaracyjna jest kwartalna. '
                      'Zgłoszenia ZUA/ZZA/ZWUA/ZIUA/ZCNA są zdarzeniami, nie obowiązkiem tworzenia dokumentu co miesiąc. '
                      'Firma bez VAT nie pojawi się w JPK VAT. Ustawienia nie usuwają pobranych dokumentów.')
        note.setWordWrap(True)
        box.addWidget(note)
        self.vat.currentIndexChanged.connect(self.changed)
        self.month.dateChanged.connect(self.show_month)
        self.show_month()

    def period(self):
        return self.month.date().toString('yyyy-MM')

    def show_month(self, *_):
        self.loading = True
        value = profile({'periods': {**self.saved.get('periods', {}), **self.edits}}, self.period())
        self.vat.setCurrentIndex(max(0, self.vat.findData(value['vat_period'])))
        for key, check in self.checks.items():
            cadence = value['forms'].get(key, 'none' if value.get('_configured') else 'monthly')
            check.setChecked(cadence != 'none')
            if key in self.cadences:
                self.cadences[key].setCurrentIndex(max(0, self.cadences[key].findData(cadence)))
        self.loading = False

    def changed(self, *_):
        if self.loading:
            return
        self.edits[self.period()] = {
            'vat_period': self.vat.currentData(),
            'forms': {key: (self.cadences[key].currentData() if key in self.cadences else 'monthly')
                      if check.isChecked() else 'none' for key, check in self.checks.items()},
        }

    def persist(self, company_id, actor_id):
        if self.edits:
            save(company_id, self.edits, actor_id)
