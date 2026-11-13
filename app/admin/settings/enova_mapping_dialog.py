"""Review discovered databases and stage explicit company mappings before saving."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QCheckBox, QPushButton, QMessageBox, QDialogButtonBox,QTabWidget)
from app.integrations.enova.discovery import mapping_review


class EnovaMappingDialog(QDialog):
    def __init__(self, companies, current, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Bazy Enovy — nierozpoznane i ręczne powiązania')
        self.resize(1000, 700)
        self.companies, self.mappings, self.result = companies, dict(current), result
        layout = QVBoxLayout(self)
        note = QLabel('Wybierz bazę i firmę, następnie „Powiąż”. Możesz też wpisać nazwę bazy ręcznie. '
                      'Zatwierdź listę, a potem zapisz połączenie Enovy. Ręczne powiązanie nie omija '
                      'kontroli NIP-u podczas importu i nie wybiera automatycznie kopii bazy.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.only_unassigned = QCheckBox('Pokaż tylko nieprzypisane bazy')
        self.only_unassigned.setChecked(True)
        self.only_unassigned.toggled.connect(self.refresh)
        layout.addWidget(self.only_unassigned)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Baza Enova', 'NIP w bazie', 'Powód / status', 'Firma Biuro'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.select_database)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.table, 'Bazy Enovy')
        self.missing = QTableWidget(0, 2)
        self.missing.setHorizontalHeaderLabels(['Firma bez bazy', 'NIP'])
        self.missing.horizontalHeader().setStretchLastSection(True)
        self.missing.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.missing.itemSelectionChanged.connect(self.select_company)
        self.tabs.addTab(self.missing, 'Firmy bez bazy')
        layout.addWidget(self.tabs)
        form = QFormLayout()
        self.database = QComboBox()
        self.database.setEditable(True)
        self.database.addItems([record['database'] for record in result.get('databases', [])])
        self.company = QComboBox()
        self.company.addItem('Wybierz firmę…', None)
        for company in companies:
            self.company.addItem(f"{company['nazwa']} | {company['nip'] or 'brak NIP-u'}", company['id'])
        form.addRow('Baza Enova', self.database)
        form.addRow('Firma Biuro', self.company)
        layout.addLayout(form)
        assign = QPushButton('Powiąż wybraną bazę z firmą')
        assign.clicked.connect(self.assign)
        layout.addWidget(assign)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        companies = {company['id']: company for company in self.companies}
        rows = mapping_review(self.companies, self.mappings, self.result)
        rows = [row for row in rows if not self.only_unassigned.isChecked() or row['company_id'] is None]
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            company = companies.get(row['company_id'])
            for column, value in enumerate((row['database'], ', '.join(row['nips']), row['reason'], company['nazwa'] if company else '')):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(2, 380)
        missing = [company for company in self.companies if not self.mappings.get(company['id'], '').strip()]
        self.missing.setRowCount(len(missing))
        for index, company in enumerate(missing):
            for column, value in enumerate((company['nazwa'], company['nip'] or 'brak NIP-u')):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, company['id'])
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.missing.setItem(index, column, item)
        self.missing.resizeColumnsToContents()
        self.tabs.setTabText(0, f'Bazy Enovy ({len(rows)})')
        self.tabs.setTabText(1, f'Firmy bez bazy ({len(missing)})')

    def select_company(self):
        row = self.missing.currentRow()
        item = self.missing.item(row, 0) if row >= 0 else None
        if item:
            self.company.setCurrentIndex(self.company.findData(item.data(Qt.ItemDataRole.UserRole)))

    def select_database(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item:
            self.database.setCurrentText(item.text())

    def assign(self):
        company_id = self.company.currentData()
        database = self.database.currentText().strip()
        if company_id is None or not database:
            QMessageBox.warning(self, 'Powiązanie Enova', 'Wybierz firmę i uzupełnij nazwę bazy.')
            return
        for other_id, other_database in self.mappings.items():
            if other_id != company_id and other_database.strip().casefold() == database.casefold():
                QMessageBox.warning(self, 'Powiązanie Enova', 'Ta baza jest już przypisana do innej firmy. Najpierw usuń tamto przypisanie w tabeli firm.')
                return
        previous = self.mappings.get(company_id, '').strip()
        if previous and previous.casefold() != database.casefold():
            if QMessageBox.question(self, 'Zmiana powiązania Enova',
                                    f'Zastąpić bazę „{previous}” bazą „{database}” dla wybranej firmy?') != QMessageBox.StandardButton.Yes:
                return
        self.mappings[company_id] = database
        self.refresh()
