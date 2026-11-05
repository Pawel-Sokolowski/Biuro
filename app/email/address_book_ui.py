"""Shared contacts, manual editing and reviewed Thunderbird CSV imports."""
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.db.dao_address_book import delete_contact, import_contacts, list_contacts, save_contact
from app.email.address_book import CONTACT_FIELDS, contact_name, normalize_contact, read_contacts_csv
from app.utils.background import CommandRunner, TaskRunner
from app.utils.view_state import preserve_view_state


class ContactDialog(QDialog):
    saved = pyqtSignal(object)

    def __init__(self, actor_id, contact=None, initial=None, parent=None):
        super().__init__(parent)
        self.actor_id, self.contact = actor_id, dict(contact or {})
        self._busy = False
        self._commands = CommandRunner(self)
        self.setWindowTitle('Edytuj kontakt globalny' if contact else 'Dodaj kontakt globalny')
        self.resize(650, 650)
        values = dict(self.contact)
        for key, value in (initial or {}).items():
            if not values.get(key):
                values[key] = value
        labels = dict(CONTACT_FIELDS)
        labels['phone_work'] = 'Telefon służbowy'
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Kontakt będzie dostępny we wspólnej książce adresowej pracowników.'))
        self.tabs = QTabWidget()
        self.fields = {}
        groups = (
            ('Kontakt', ('display_name', 'first_name', 'last_name', 'nickname', 'email', 'additional_email',
                         'company', 'job_title', 'department', 'phone_work', 'phone_mobile', 'phone_home')),
            ('Adres domowy', ('home_address', 'home_address2', 'home_city', 'home_region', 'home_postal', 'home_country')),
            ('Adres służbowy', ('work_address', 'work_address2', 'work_city', 'work_region', 'work_postal', 'work_country')),
            ('Pozostałe', ('fax', 'pager', 'aim', 'website1', 'website2', 'birth_year', 'birth_month', 'birth_day',
                          'custom1', 'custom2', 'custom3', 'custom4', 'notes')),
        )
        for title, keys in groups:
            host = QWidget()
            form = QFormLayout(host)
            for key in keys:
                field = QPlainTextEdit() if key == 'notes' else QLineEdit()
                text = str(values.get(key) or '')
                if key == 'notes':
                    field.setPlainText(text)
                else:
                    field.setText(text)
                self.fields[key] = field
                form.addRow(labels[key] + ':', field)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(host)
            self.tabs.addTab(scroll, title)
        self.fields['email'].setPlaceholderText('np. jan@example.com')
        layout.addWidget(self.tabs)
        self.status = QLabel()
        layout.addWidget(self.status)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton('Zapisz kontakt', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('Anuluj', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        values = {key: (field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text())
                  for key, field in self.fields.items()}
        if self.contact.get('extra_fields'):
            values['extra_fields'] = dict(self.contact['extra_fields'])
        return values

    def _save(self):
        if self._busy:
            return
        try:
            values = normalize_contact(self.values())
        except ValueError as exc:
            QMessageBox.warning(self, 'Kontakt', str(exc))
            return
        self._busy = True
        self.save_button.setEnabled(False)
        self.tabs.setEnabled(False)
        self.status.setText('Zapisywanie kontaktu…')
        contact_id, version = self.contact.get('id'), self.contact.get('version')
        def done(contact):
            self._busy = False
            self.saved.emit(contact)
            self.accept()
        def failed(exc):
            self._busy = False
            self.save_button.setEnabled(True)
            self.tabs.setEnabled(True)
            self.status.setText('Nie zapisano kontaktu.')
            QMessageBox.warning(self, 'Kontakt', str(exc))
        self._commands.submit(lambda: save_contact(self.actor_id, values, contact_id, version), done, failed)

    def reject(self):
        if not self._busy:
            super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)


class AddressBookDialog(QDialog):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self._busy = False
        self._contacts = {}
        self._reads = TaskRunner(self)
        self._commands = CommandRunner(self)
        self.setWindowTitle('Globalna książka adresowa')
        self.resize(1050, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Wspólne kontakty pracowników. Wpisz nazwę lub adres, aby wyszukać kontakt.'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Nazwa, imię, nazwisko, pseudonim, firma lub e-mail…')
        self.search.setClearButtonEnabled(True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda: self._timer.start())
        layout.addWidget(self.search)
        actions = QHBoxLayout()
        self.new_button, self.edit_button = QPushButton('Dodaj kontakt…'), QPushButton('Edytuj…')
        self.delete_button, self.import_button = QPushButton('Usuń'), QPushButton('Importuj CSV…')
        self.refresh_button = QPushButton('Odśwież')
        self.new_button.clicked.connect(self._new)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)
        self.import_button.clicked.connect(self._import_csv)
        self.refresh_button.clicked.connect(self.refresh)
        for button in (self.new_button, self.edit_button, self.delete_button, self.import_button, self.refresh_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Nazwa', 'Podstawowy e-mail', 'Dodatkowy e-mail', 'Firma / organizacja', 'Telefon'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda *_args: self._edit())
        layout.addWidget(self.table)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        close_button = QPushButton('Zamknij')
        close_button.clicked.connect(self.reject)
        layout.addWidget(close_button)
        self._controls = (self.search, self.table, self.new_button, self.edit_button, self.delete_button,
                          self.import_button, self.refresh_button, close_button)
        self.refresh()

    def _set_busy(self, busy):
        self._busy = busy
        for control in self._controls:
            control.setEnabled(not busy)

    def refresh(self):
        if self._busy:
            return
        self._timer.stop()
        query = self.search.text()
        def loaded(contacts):
            if self._busy or self.search.text() != query:
                return
            self._contacts = {contact['id']: contact for contact in contacts}
            with preserve_view_state(self.table, lambda index: index.data(Qt.ItemDataRole.UserRole)):
                self.table.setRowCount(0)
                self.table.setRowCount(len(contacts))
                for row, contact in enumerate(contacts):
                    texts = (contact_name(contact), contact['email'], contact.get('additional_email'),
                             contact.get('company'), contact.get('phone_work') or contact.get('phone_mobile'))
                    for column, text in enumerate(texts):
                        item = QTableWidgetItem(str(text or ''))
                        item.setData(Qt.ItemDataRole.UserRole, contact['id'])
                        self.table.setItem(row, column, item)
            total = contacts[0]['total_count'] if contacts else 0
            self.status.setText(f'Kontakty: {total}. Pokazano {len(contacts)}.'
                                + (' Zawęź wyszukiwanie, aby znaleźć pozostałe.' if total > len(contacts) else ''))
        self._reads.submit(lambda: list_contacts(self.actor_id, query, 500), loaded,
                           lambda _exc: self.status.setText('Nie udało się wczytać kontaktów. Odśwież listę.'))

    def _selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return self._contacts.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _new(self):
        if not self._busy and ContactDialog(self.actor_id, parent=self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _edit(self):
        contact = self._selected()
        if not self._busy and contact and ContactDialog(self.actor_id, contact=contact, parent=self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _delete(self):
        if self._busy:
            return
        contact = self._selected()
        if not contact:
            return
        contact_id, version = contact['id'], contact['version']
        if QMessageBox.question(self, 'Usuń kontakt', f'Usunąć „{contact_name(contact)}” ze wspólnej książki?',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes or self._busy:
            return
        self._set_busy(True)
        def done(_result):
            self._set_busy(False)
            self.refresh()
        def failed(exc):
            self._set_busy(False)
            QMessageBox.warning(self, 'Kontakt', str(exc))
        self._commands.submit(lambda: delete_contact(self.actor_id, contact_id, version), done, failed)

    def _import_csv(self):
        if self._busy:
            return
        self._set_busy(True)
        path, _ = QFileDialog.getOpenFileName(self, 'Importuj kontakty Thunderbird', '', 'Kontakty CSV (*.csv);;Wszystkie pliki (*)')
        if not path:
            self._set_busy(False)
            return
        self.status.setText('Odczytywanie CSV…')
        def failed(exc):
            self._set_busy(False)
            self.status.setText('Nie zaimportowano kontaktów.')
            QMessageBox.warning(self, 'Import kontaktów', str(exc))
        def preview(plan):
            contacts = plan['contacts']
            if not contacts:
                failed(ValueError('Brak poprawnych kontaktów w CSV.\n' + '\n'.join(plan['issues'][:20])))
                return
            review = QMessageBox(self)
            review.setWindowTitle('Sprawdź import kontaktów')
            review.setText(f'Kontakty do importu: {len(contacts)}. Pominięte wiersze: {len(plan["issues"])}. '
                           f'Scalone duplikaty w pliku: {plan["duplicates"]}.\n'
                           'Istniejące kontakty będą dopasowane po e-mailu; uzupełnione zostaną tylko puste pola.\n'
                           f'Kodowanie: {plan["encoding"]}. Rozwiń szczegóły, aby zobaczyć podgląd.')
            review.setDetailedText('\n'.join(f'{contact_name(contact)} — {contact["email"]}' for contact in contacts[:200])
                                   + ('\n… podgląd pierwszych 200 kontaktów.' if len(contacts) > 200 else '')
                                   + '\n\n' + '\n'.join(plan['issues'][:200]))
            review.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            review.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if review.exec() != QMessageBox.StandardButton.Yes:
                self._set_busy(False)
                self.status.setText('Anulowano import. Nie zmieniono kontaktów.')
                return
            self.status.setText('Importowanie kontaktów…')
            def done(counts):
                self._set_busy(False)
                self.refresh()
                QMessageBox.information(self, 'Import kontaktów',
                    f'Dodano: {counts["created"]}. Uzupełniono: {counts["updated"]}. Bez zmian: {counts["unchanged"]}. '
                    f'Pominięte wiersze: {len(plan["issues"])}. Duplikaty w pliku: {plan["duplicates"]}.')
            self._commands.submit(lambda: import_contacts(self.actor_id, contacts), done, failed)
        self._reads.submit(lambda: read_contacts_csv(path), preview, failed)

    def reject(self):
        if not self._busy:
            super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)
