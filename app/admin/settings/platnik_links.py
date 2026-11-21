"""Review cases in which Płatnik needs an explicit payer or owner selection."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from app.integrations.platnik.links import review, save
from app.utils.background import CommandRunner, TaskRunner


class PlatnikLinksDialog(QDialog):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.setWindowTitle('Powiązania z Płatnikiem')
        self.resize(850, 550)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.reader = TaskRunner(self)
        self.writer = CommandRunner(self)
        self.finished.connect(self.reader.invalidate)
        self.rows, self.visible_rows = [], []
        self.source = ''
        self.registry_available = False
        self.busy = False
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Szukaj firmy lub NIP-u')
        self.show_automatic = QCheckBox('Pokaż też dopasowane automatycznie')
        self.refresh_button = QPushButton('Odśwież')
        filters.addWidget(self.search, 1)
        filters.addWidget(self.show_automatic)
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Firma w Biuro', 'NIP płatnika', 'Status'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        form = QFormLayout()
        self.payer = QComboBox()
        self.person = QComboBox()
        form.addRow('Kartoteka płatnika', self.payer)
        form.addRow('Kartoteka właściciela lub wspólnika', self.person)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.approve = QPushButton('Zatwierdź powiązanie')
        self.revoke = QPushButton('Cofnij zatwierdzenie')
        actions.addStretch()
        actions.addWidget(self.revoke)
        actions.addWidget(self.approve)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.search.textChanged.connect(self._filter)
        self.show_automatic.toggled.connect(self._filter)
        self.table.itemSelectionChanged.connect(self._show_selection)
        self.payer.currentIndexChanged.connect(self._show_people)
        self.refresh_button.clicked.connect(self._refresh)
        self.approve.clicked.connect(lambda: self._save(self.payer.currentData(), self.person.currentData()))
        self.revoke.clicked.connect(lambda: self._save(None, None))
        self._refresh()

    def _selected(self):
        row = self.table.currentRow()
        return self.visible_rows[row] if 0 <= row < len(self.visible_rows) else None

    def _set_busy(self, busy):
        self.busy = busy
        for widget in (self.search, self.show_automatic, self.refresh_button, self.table):
            widget.setEnabled(not busy)
        self._actions()

    def _refresh(self, *_):
        self._set_busy(True)
        self.status.setText('Odczytuję kartoteki z zapisanej bazy Płatnika…')
        self.reader.submit(lambda: review(self.actor_id), self._loaded, self._failed)

    def _loaded(self, result):
        self.source = result['source']
        self.rows = result['rows']
        self.registry_available = result['registry_available']
        self._set_busy(False)
        self._filter()
        if not self.registry_available:
            self.status.setText('Rejestr właścicieli jest niedostępny. Możesz zatwierdzać kartoteki płatników.')
        elif not self.visible_rows:
            self.status.setText('Brak powiązań do wyświetlenia.')
        else:
            self.status.setText('Zatwierdzenie będzie użyte przy kolejnym odczycie Płatnika.')

    def _filter(self, *_):
        previous = self._selected()
        key = (previous['company_id'], previous['nip']) if previous else None
        phrase = self.search.text().strip().casefold()
        self.visible_rows = [row for row in self.rows
                             if (self.show_automatic.isChecked() or row['status'] != 'Dopasowano po NIP')
                             and (phrase in row['company'].casefold() or phrase in row['nip'])]
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.visible_rows))
        selected = 0
        for index, row in enumerate(self.visible_rows):
            for column, value in enumerate((row['company'], row['nip'], row['status'])):
                self.table.setItem(index, column, QTableWidgetItem(value))
            if (row['company_id'], row['nip']) == key:
                selected = index
        if self.visible_rows:
            self.table.selectRow(selected)
        self.table.blockSignals(False)
        self._show_selection()

    def _show_selection(self):
        row = self._selected()
        self.payer.blockSignals(True)
        self.payer.clear()
        if row:
            self.payer.addItem('Wybierz kartotekę płatnika', None)
            for candidate in row['payers']:
                label = f"Płatnik #{candidate['payer_id']}"
                if candidate['owners']:
                    label += f" · kartoteki właściciela: {len(candidate['owners'])}"
                self.payer.addItem(label, candidate['payer_id'])
            self.payer.setCurrentIndex(max(0, self.payer.findData(row['selected'])))
        self.payer.blockSignals(False)
        self._show_people()

    def _show_people(self, *_):
        row = self._selected()
        self.person.clear()
        self.person.addItem('Dobierz automatycznie', None)
        if row and self.payer.currentData() is not None:
            payer = next(candidate for candidate in row['payers'] if candidate['payer_id'] == self.payer.currentData())
            for person in payer['owners']:
                text = (f"Ubezpieczony #{person['person_id']} · zgłoszenia {person['first'] or '—'} – "
                        f"{person['last'] or '—'} ({person['count']})")
                self.person.addItem(text, person['person_id'])
            approval = row['approval']
            if approval and approval['payer_id'] == self.payer.currentData() and approval['source_key'] == self.source:
                self.person.setCurrentIndex(max(0, self.person.findData(approval['person_id'])))
        self._actions()

    def _actions(self):
        row = self._selected()
        editable = not self.busy and row is not None and row['can_approve']
        self.payer.setEnabled(editable)
        self.person.setEnabled(editable and self.person.count() > 1)
        self.approve.setEnabled(editable and self.payer.currentData() is not None)
        self.revoke.setEnabled(not self.busy and row is not None and row['approval'] is not None)

    def _save(self, payer_id, person_id):
        row = self._selected()
        if row is None or self.busy:
            return
        previous = row['approval']
        args = (self.actor_id, row['company_id'], row['nip'], payer_id, person_id,
                self.source, previous['revision'] if previous else None)
        self._set_busy(True)
        self.status.setText('Zapisuję powiązanie…')
        self.writer.submit(lambda: save(*args), self._refresh, self._failed)

    def _failed(self, error):
        self._set_busy(False)
        self.status.setText(str(error))
