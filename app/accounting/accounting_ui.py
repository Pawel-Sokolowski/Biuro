import json
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDateEdit,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem)
from app.integrations.enova.accounting import list_accounting
from app.integrations.enova.jpk import STATUSES
from app.utils.background import TaskRunner


class AccountingPage(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.reader = TaskRunner(self)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('Księgowość'))
        self.month = QDateEdit(QDate.currentDate())
        self.month.setDisplayFormat('yyyy-MM')
        self.month.setCalendarPopup(True)
        bar.addWidget(self.month)
        refresh = QPushButton('Odśwież')
        refresh.clicked.connect(self.refresh_async)
        bar.addWidget(refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Szukaj firmy lub NIP')
        self.search.textChanged.connect(self.filter_rows)
        bar.addWidget(self.search)
        layout.addLayout(bar)
        self.message = QLabel('JPK: status z Enova, przyjęcie wymaga potwierdzenia UPO. Brak odczytu nie oznacza zera dokumentów.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(['Firma', 'NIP', 'Księgowość', 'Dokumenty razem',
            'Rodzaje dokumentów', 'Pozycje bankowe', 'Ostatni import', 'JPK / UPO (Enova)'])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.month.dateChanged.connect(self.refresh_async)

    def refresh_async(self, *_):
        month = self.month.date().toString('yyyy-MM')
        self.reader.submit(lambda: list_accounting(self.actor_id, month),
                           lambda rows: self.render(rows, month), self.failed)

    def failed(self, exc):
        self.message.setText('Nie udało się wykonać operacji: ' + str(exc))

    def render(self, rows, month):
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            snapshot = json.loads(row['snapshot']) if row['snapshot'] else {}
            kinds = snapshot.get('by_type')
            detail = ', '.join(f'{key}: {value}' for key, value in sorted(kinds.items())) if kinds is not None else 'Brak podziału'
            values = (row['nazwa'], row['nip'], row['forma_opodatkowania'],
                      row['ilosc_dok'] if row['ilosc_dok'] is not None else '—', detail,
                      snapshot.get('bank_transactions', '—'), snapshot.get('imported_at', '—'))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value if value is not None else ''))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(index, column, item)
            jpk = json.loads(row['jpk']) if row['jpk'] else {}
            status = STATUSES.get(jpk.get('status'), 'Nie sprawdzono')
            item = QTableWidgetItem(status)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip('UPO: ' + jpk.get('upo', '—') + '\nOdczyt: ' + jpk.get('imported_at', '—'))
            self.table.setItem(index, 7, item)
        self.table.resizeColumnsToContents()
        self.filter_rows()

    def filter_rows(self, *_):
        query = self.search.text().strip().casefold()
        for row in range(self.table.rowCount()):
            text = ' '.join(self.table.item(row, col).text() for col in (0, 1))
            self.table.setRowHidden(row, query not in text.casefold())
