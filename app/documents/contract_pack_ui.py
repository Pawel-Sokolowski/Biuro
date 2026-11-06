"""Generate the office's printable onboarding pack from the selected client."""
from pathlib import Path

from PyQt6.QtCore import QDate, QUrl, QRegularExpression
from PyQt6.QtGui import QDesktopServices, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from app.documents.contract_pack import DOCUMENTS, generate_pack, load_context
from app.utils.background import CommandRunner


class ContractPackDialog(QDialog):
    def __init__(self, company_id, actor_id, parent=None):
        super().__init__(parent)
        self.company_id, self.actor_id = company_id, actor_id
        self.context = load_context(company_id, actor_id)
        self.setWindowTitle('Komplet dokumentów do druku')
        self.resize(760, 690)
        self._busy = False
        self.runner = CommandRunner(self)
        self.fields, self.representatives, self.documents, self.extra_pdfs = {}, [], {}, []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Sprawdź dane przed wygenerowaniem. Zmiany dotyczą tylko tego kompletu dokumentów.'))
        tabs = QTabWidget()
        layout.addWidget(tabs)

        client_form = self._tab(tabs, 'Dane i daty')
        for field, label in (('name', 'Pełna nazwa firmy'), ('address', 'Adres siedziby'), ('nip', 'NIP'),
                             ('krs', 'KRS'), ('pesel', 'PESEL'), ('phone', 'Telefon'), ('email', 'E-mail')):
            self._field(client_form, field, label, self.context.get(field, ''))
        self.agreement_date = self._date(client_form, 'Data zawarcia umowy')
        self.main_date = self._date(client_form, 'Data umowy głównej (powierzenie/wypowiedzenie)')
        self._previous_agreement_date = self.agreement_date.date()
        self.agreement_date.dateChanged.connect(self._agreement_changed)
        self.first_period = QDateEdit(QDate.currentDate())
        self.first_period.setDisplayFormat('MM.yyyy')
        self.first_period.setCalendarPopup(True)
        client_form.addRow('Pierwszy okres księgowy', self.first_period)

        reps = self._tab(tabs, 'Reprezentanci')
        reps.addRow(QLabel('Wybierz osobę z kartoteki lub wpisz inną. Potwierdź jej funkcję; PESEL jest opcjonalny.'))
        self.representative_choices = []
        for index in range(2):
            reps.addRow(QLabel(f'<b>Reprezentant {index + 1}</b>'))
            choice = QComboBox()
            choice.addItem('Wpisz inną osobę', None)
            for person in self.context.get('representative_candidates', ()):
                name = ' '.join(str(person.get(k) or '') for k in ('imie_pierwsze', 'nazwisko')).strip()
                if name:
                    choice.addItem(name, person)
            self.representative_choices.append(choice)
            reps.addRow('Przedstawiciel', choice)
            fields = {}
            for key, label in (('imie_pierwsze', 'Imię'), ('nazwisko', 'Nazwisko'), ('pesel', 'PESEL (opcjonalnie)'), ('nr_telefonu', 'Telefon')):
                widget = QLineEdit()
                if key == 'pesel':
                    widget.setMaxLength(11)
                    widget.setValidator(QRegularExpressionValidator(QRegularExpression('[0-9]{0,11}'), widget))
                fields[key] = widget
                reps.addRow(label, widget)
            role = QComboBox()
            role.setEditable(True)
            role.addItems(['', 'Prezes zarządu', 'Członek zarządu', 'Wiceprezes zarządu', 'Prokurent', 'Właściciel', 'Pełnomocnik'])
            fields['stanowisko'] = role
            reps.addRow('Funkcja / rola', role)
            self.representatives.append(fields)
            choice.currentIndexChanged.connect(lambda _, i=index: self._representative_changed(i))
        self._field(reps, 'pep_person', 'Osoba składająca oświadczenie PEP')
        self._field(reps, 'authorized_person', 'Osoba upoważniona: imię, nazwisko, seria/numer dowodu i organ wydający')

        scope = self._tab(tabs, 'Zakres i parametry')
        self.accounting = QComboBox()
        for label, key in (('Do ustalenia / bez zaznaczenia', ''), ('Ryczałt', 'ryczalt'), ('KPiR', 'kpir'), ('Księgi rachunkowe', 'ksiegi')):
            self.accounting.addItem(label, key)
        scope.addRow('Usługi księgowe', self.accounting)
        self.payroll = QComboBox()
        for label, key in (('Bez zaznaczenia', ''), ('Usługi płacowe', 'place'), ('Pełne usługi kadrowo-płacowe', 'kadry')):
            self.payroll.addItem(label, key)
        scope.addRow('Obsługa kadrowo-płacowa', self.payroll)
        self.invoicing = QCheckBox('Wystawianie faktur w imieniu klienta')
        self.transfers = QCheckBox('Generowanie przelewów klienta')
        scope.addRow(self.invoicing)
        scope.addRow(self.transfers)
        for field, label in (('document_limit', 'Limit dokumentów'), ('extra_document_price', 'Cena netto dokumentu ponad limit'),
                             ('insured_limit', 'Maksymalna liczba ubezpieczonych, łącznie ze wspólnikami'),
                             ('monthly_fee', 'Podstawowe wynagrodzenie netto (cennik)'), ('tax_office', 'Urząd Skarbowy')):
            self._field(scope, field, label, self.context.get(field, ''))
        self.termination_date = self._date(scope, 'Data rozwiązania (tylko wypowiedzenie)')
        self._field(scope, 'last_period', 'Ostatni miesiąc rozliczenia (tylko wypowiedzenie)')

        document_form = self._tab(tabs, 'Dokumenty')
        for key, label, default in DOCUMENTS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(default)
            self.documents[key] = checkbox
            document_form.addRow(checkbox)
        self.attachments_label = QLabel('Brak dodatkowych PDF-ów.')
        self.attachments_label.setWordWrap(True)
        document_form.addRow(self.attachments_label)
        attach = QPushButton('Dodaj PDF-y, np. UPL-1 / PEL…')
        attach.clicked.connect(self._attach)
        clear = QPushButton('Usuń dodatkowe PDF-y')
        clear.clicked.connect(self._clear_attachments)
        document_form.addRow(attach, clear)
        self.duplex = QCheckBox('Druk dwustronny: każdy dokument zaczyna się na nowej kartce')
        document_form.addRow(self.duplex)
        notice = QLabel('Wzory zachowują oryginalne postanowienia i cennik 2026. '
                        'Oświadczenia klienta (m.in. PEP i zgody) oraz podpisy pozostają do wypełnienia. '
                        'Dołączone PDF-y są wstawiane w wybranej kolejności; sprawdź ich treść przed drukiem.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.status = QLabel('PDF wymaga zainstalowanego Worda (Windows) lub LibreOffice.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.pdf_button = QPushButton('Generuj komplet PDF…')
        self.docx_button = QPushButton('Zapisz komplet DOCX (ZIP)…')
        self.pdf_button.clicked.connect(lambda: self._generate(False))
        self.docx_button.clicked.connect(lambda: self._generate(True))
        self.close_button = QPushButton('Zamknij')
        self.close_button.clicked.connect(self.reject)
        for button in (self.pdf_button, self.docx_button, self.close_button):
            actions.addWidget(button)
        layout.addLayout(actions)

    @staticmethod
    def _tab(tabs, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        form = QFormLayout(widget)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll.setWidget(widget)
        tabs.addTab(scroll, title)
        return form

    def _field(self, form, key, label, value=''):
        widget = QLineEdit(str(value or ''))
        self.fields[key] = widget
        form.addRow(label, widget)

    @staticmethod
    def _date(form, label):
        widget = QDateEdit(QDate.currentDate())
        widget.setCalendarPopup(True)
        widget.setDisplayFormat('dd.MM.yyyy')
        form.addRow(label, widget)
        return widget

    def _attach(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Dodatkowe dokumenty do kompletu', '', 'PDF (*.pdf)')
        for path in paths:
            if path not in self.extra_pdfs:
                self.extra_pdfs.append(path)
        self.attachments_label.setText('\n'.join(Path(p).name for p in self.extra_pdfs) or 'Brak dodatkowych PDF-ów.')

    def _clear_attachments(self):
        self.extra_pdfs.clear()
        self.attachments_label.setText('Brak dodatkowych PDF-ów.')

    def _agreement_changed(self, date):
        if self.main_date.date() == self._previous_agreement_date:
            self.main_date.setDate(date)
        self._previous_agreement_date = date

    def _representative_changed(self, index):
        person = self.representative_choices[index].currentData() or {}
        for key, widget in self.representatives[index].items():
            value = str(person.get(key) or (person.get('telefon') if key == 'nr_telefonu' else '') or '')
            if isinstance(widget, QComboBox):
                widget.setCurrentText(value)
            else:
                widget.setText(value)

    def values(self):
        values = {key: widget.text().strip() for key, widget in self.fields.items()}
        values.update(agreement_date=self.agreement_date.date().toString('dd.MM.yyyy'),
                      main_agreement_date=self.main_date.date().toString('dd.MM.yyyy'),
                      first_period=self.first_period.date().toString('MM.yyyy'),
                      termination_date=self.termination_date.date().toString('dd.MM.yyyy'),
                      accounting=self.accounting.currentData(), payroll=self.payroll.currentData(),
                      invoicing=self.invoicing.isChecked(), transfers=self.transfers.isChecked(),
                      representatives=[{key: (w.currentText() if isinstance(w, QComboBox) else w.text()).strip()
                                        for key, w in fields.items()} for fields in self.representatives])
        return values

    def _generate(self, docx_only):
        if self._busy:
            return
        data = self.values()
        selected = [key for key, checkbox in self.documents.items() if checkbox.isChecked()]
        if not data['name'] or not data['address'] or not selected:
            QMessageBox.warning(self, 'Komplet dokumentów', 'Uzupełnij nazwę i adres oraz wybierz dokumenty.')
            return
        suffix = '.zip' if docx_only else '.pdf'
        # Snapshot input before a native file dialog can re-enter the event loop.
        extra_pdfs, duplex = tuple(self.extra_pdfs), self.duplex.isChecked()
        self._set_busy(True)
        path, _ = QFileDialog.getSaveFileName(self, 'Zapisz komplet dokumentów', 'Komplet_dokumentow' + suffix,
                                            'ZIP (*.zip)' if docx_only else 'PDF (*.pdf)')
        if not path:
            self._set_busy(False)
            return
        if Path(path).suffix.lower() != suffix:
            path += suffix
            if Path(path).exists() and QMessageBox.question(
                    self, 'Zastąpienie pliku', 'Plik już istnieje. Zastąpić go?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                self._set_busy(False)
                return
        self.status.setText('Generowanie kompletu…')
        try:
            self.runner.submit(lambda: generate_pack(self.company_id, self.actor_id, data, selected, path,
                                extra_pdfs=extra_pdfs, duplex=duplex, docx_only=docx_only), self._done, self._error)
        except Exception as error:
            self._error(error)

    def _set_busy(self, busy):
        self._busy = busy
        for button in (self.pdf_button, self.docx_button, self.close_button):
            button.setEnabled(not busy)

    def _done(self, path):
        self._set_busy(False)
        self.status.setText('Zapisano: ' + path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _error(self, error):
        self._set_busy(False)
        self.status.setText('Nie utworzono kompletu.')
        QMessageBox.warning(self, 'Komplet dokumentów', str(error))

    def reject(self):
        if not self._busy:
            super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)
