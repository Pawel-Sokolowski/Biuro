"""Generate the office's printable onboarding pack from the selected client."""
from pathlib import Path

from PyQt6.QtCore import QDate, QUrl, QRegularExpression, Qt
from PyQt6.QtGui import QDesktopServices, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget, QListWidget, QListWidgetItem,
)

from app.documents.power_of_attorney import office_email, office_address
from app.utils.background import CommandRunner
from app.documents.contract_pack import (
    DOCUMENTS,
    generate_pack,
    load_context,
)

class ContractPackDialog(QDialog):
    def __init__(self, company_id, actor_id, parent=None):
        super().__init__(parent)
        self.company_id, self.actor_id = company_id, actor_id
        self.context = load_context(company_id, actor_id)
        self.setWindowTitle('Komplet dokumentów do druku')
        self.resize(760, 690)
        self._busy = False
        self.runner = CommandRunner(self)
        from app.documents.print_renderer import PrintRenderer
        self.print_renderer = PrintRenderer(self)
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
            reps.addRow('Stanowisko / funkcja', role)
            for field, label in (('birth_date', 'Data urodzenia do PEL / PEL-Z, gdy brak PESEL'),
                                 ('identity_number', 'Dokument tożsamości do PEL / PEL-Z, gdy brak PESEL'),
                                 ('street', 'Adres przedstawiciela do PEL / PEL-Z — ulica'), ('building', 'Numer domu'),
                                 ('apartment', 'Numer lokalu'), ('postcode', 'Kod pocztowy'),
                                 ('city', 'Miejscowość'), ('country', 'Państwo (jeśli poza Polską)')):
                widget = QLineEdit()
                fields[field] = widget
                reps.addRow(label, widget)
            self.representatives.append(fields)
            choice.currentIndexChanged.connect(lambda _, i=index: self._representative_changed(i))
        if self.context.get('civil_partnership'):
            for index, choice in enumerate(self.representative_choices):
                if choice.count() > index + 1:
                    choice.setCurrentIndex(index + 1)
        self._field(reps, 'pep_person', 'Osoba składająca oświadczenie PEP')
        self._field(reps, 'authorized_person', 'Osoba upoważniona: imię, nazwisko, seria/numer dowodu i organ wydający')

        powers = self._tab(tabs, 'UPL / PEL')
        self.principal_kind = QComboBox()
        for label, value in (('Wybierz rodzaj podmiotu', ''), ('Osoba fizyczna / JDG', 'person'), ('Osoba prawna', 'legal'), ('Inny podmiot', 'other')):
            self.principal_kind.addItem(label, value)
        self.principal_kind.setCurrentIndex(max(0, self.principal_kind.findData(self.context.get('principal_kind'))))
        powers.addRow('Mocodawca', self.principal_kind)
        self.principal_owner = QComboBox()
        self.principal_owner.addItem('Firma — NIP firmy', None)
        for person in self.context.get('partners', []):
            self.principal_owner.addItem(' '.join(str(person.get(k) or '') for k in ('imie_pierwsze', 'nazwisko')), person)
        powers.addRow('Dane mocodawcy', self.principal_owner)
        self._field(powers, 'short_name', 'Nazwa skrócona', self.context.get('short_name', ''))
        self._field(powers, 'principal_first_name', 'Imię mocodawcy (osoba fizyczna)', self.context.get('principal_first_name', ''))
        self._field(powers, 'principal_surname', 'Nazwisko mocodawcy (osoba fizyczna)', self.context.get('principal_surname', ''))
        self._field(powers, 'principal_birth_date', 'Data urodzenia mocodawcy, gdy brak PESEL (DD.MM.RRRR)', self.context.get('principal_birth_date', ''))
        self._field(powers, 'principal_identity_number', 'Dokument tożsamości mocodawcy, gdy brak PESEL')
        for field, label in (('street', 'Ulica klienta'), ('building', 'Numer domu'), ('apartment', 'Numer lokalu'),
                             ('postcode', 'Kod pocztowy klienta'), ('city', 'Miejscowość klienta'), ('country', 'Kod kraju (np. PL)'),
                             ('region', 'Województwo'), ('county', 'Powiat'), ('municipality', 'Gmina')):
            self._field(client_form, field, label, self.context.get(field, ''))
            self.fields[field].textChanged.connect(self._sync_client_address)
        self.fields['address'].setReadOnly(True)
        self._sync_client_address()
        self.principal_owner.currentIndexChanged.connect(self._principal_owner_changed)
        self.employee = QComboBox(self)
        self.employee.hide()
        self._attorney_overrides = {}
        self._edited_attorney_id = None
        self.employee.addItem('Wybierz pracownika', None)
        from app.db.dao_employees import list_employees
        self._employees = {p['id']: p for p in list_employees() if p.get('aktywny')}
        for employee in self._employees.values():
            self.employee.addItem(' '.join(str(employee.get(k) or '') for k in ('imie', 'nazwisko')).strip(), employee['id'])
        self.employee_checks = QListWidget()
        for employee in self._employees.values():
            item = QListWidgetItem(' '.join(str(employee.get(k) or '') for k in ('imie', 'nazwisko')).strip())
            item.setData(Qt.ItemDataRole.UserRole, employee['id'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.employee_checks.addItem(item)
        self.employee_checks.currentRowChanged.connect(self._select_employee_row)
        powers.addRow('Pełnomocnicy — zaznacz pracowników', self.employee_checks)
        powers.addRow(QLabel('Dane zaznaczonej osoby są pobierane z kartoteki pracownika. Pola poniżej pozwalają poprawić ten wydruk.'))
        self.attorney_fields = {}
        for field, label in (('pesel', 'PESEL pełnomocnika'), ('nip', 'NIP pełnomocnika (zamiast PESEL w UPL)'),
                             ('birth_date', 'Data urodzenia, gdy brak PESEL (DD.MM.RRRR)'),
                             ('identity_number', 'Dokument tożsamości, gdy brak PESEL'),
                             ('identity_country', 'Kraj wydania dokumentu (np. PL)')):
            field_widget = QLineEdit()
            self.attorney_fields[field] = field_widget
            powers.addRow(label, field_widget)
        self.employee.currentIndexChanged.connect(self._employee_changed)
        self.identity_type = QComboBox()
        self.identity_type.addItem('Dowód osobisty', '1')
        self.identity_type.addItem('Paszport', '2')
        powers.addRow('Rodzaj dokumentu pełnomocnika, gdy brak PESEL', self.identity_type)
        address = office_address()
        office = QLabel(', '.join(v for v in (' '.join(address.get(k, '') for k in ('street', 'building', 'apartment')).strip(),
                                             ' '.join(address.get(k, '') for k in ('postcode', 'city')).strip()) if v))
        office.setWordWrap(True)
        powers.addRow('Adres pełnomocnika — firmowy', office)
        powers.addRow('E-mail pełnomocnika — z ustawień biura', QLabel(office_email() or 'Uzupełnij w ustawieniach biura'))
        powers.addRow(QLabel('Adres i dane administracyjne biura uzupełnisz w Ustawienia → Faktury i KSeF.'))
        self.authority_from = self._date(powers, 'Pełnomocnictwo ważne od')
        powers.addRow('Pełnomocnictwo ważne do', QLabel('Bezterminowo / do odwołania — data końcowa pusta'))
        powers.addRow('Adres elektroniczny w UPL', QLabel('Pozostaje pusty'))
        powers.addRow('Zakres PEL', QLabel('Załatwianie spraw w ZUS oraz przez portal eZUS — bez ograniczeń'))
        roles_notice = QLabel('JDG / osoba fizyczna: Ubezpieczony i Płatnik składek. '
                              'Spółki i pozostałe podmioty: Płatnik składek w sekcji reprezentowanego podmiotu.')
        roles_notice.setWordWrap(True)
        powers.addRow('Role eZUS', roles_notice)
        self.pel_pit = QCheckBox('Upoważnienie do otrzymywania deklaracji PIT z ZUS')
        powers.addRow(self.pel_pit)
        powers_notice = QLabel('UPL korzysta z przesłanego druku UPL-1(6), a PEL z trzystronicowego PDF 579/26. '
                               'Dla spółki dane osoby udzielającej PEL pochodzą z pierwszego przedstawiciela. '
                               'Przy wielu przedstawicielach wybierz też PEL-Z. Stanowisko / funkcję wybierzesz przy przedstawicielu. '
                               'Sprawdź podgląd i właściwą wersję formularza przed podpisaniem. Podpisy pozostają puste.')
        powers_notice.setWordWrap(True)
        powers.addRow(powers_notice)

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
        self.status = QLabel('Dokumenty Word wymagają Worda lub LibreOffice. PDF-y pełnomocnictw tworzy aplikacja.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()

        self.contract_button = QPushButton('Generuj umowę')
        self.upl_button = QPushButton('Generuj UPL-1')
        self.pel_button = QPushButton('Generuj PEL')
        self.contract_button.clicked.connect(self._generate_contract)
        self.upl_button.clicked.connect(self._generate_upl)
        self.pel_button.clicked.connect(self._generate_pel)

        self.pdf_button = QPushButton('Generuj wybrane PDF')
        self.docx_button = QPushButton('Zapisz wybrane Word (ZIP)')
        self.pdf_button.clicked.connect(lambda: self._generate(False))
        self.docx_button.clicked.connect(lambda: self._generate(True))

        self.close_button = QPushButton('Zamknij')
        self.close_button.clicked.connect(self.reject)

        for button in (
                self.contract_button,
                self.upl_button,
                self.pel_button,
                self.pdf_button,
                self.docx_button,
                self.close_button,
        ):
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
        if hasattr(self, 'authority_from') and self.authority_from.date() == self._previous_agreement_date:
            self.authority_from.setDate(date)
        self._previous_agreement_date = date

    def _representative_changed(self, index):
        person = self.representative_choices[index].currentData() or {}
        aliases = dict(nr_telefonu='telefon', street='ulica', building='nr_domu', apartment='nr_mieszkania',
                       postcode='kod_pocztowy', city='miejscowosc', country='panstwo', birth_date='data_urodzenia')
        for key, widget in self.representatives[index].items():
            value = str(person.get(key) or person.get(aliases.get(key)) or '')
            if key == 'country' and value.casefold() in ('polska', 'poland'):
                value = 'PL'
            if isinstance(widget, QComboBox):
                widget.setCurrentText(value)
            else:
                widget.setText(value)

    def _principal_owner_changed(self):
        owner = self.principal_owner.currentData()
        values = dict(self.context, principal_first_name=self.context.get('principal_first_name', ''),
                      principal_surname=self.context.get('principal_surname', ''), principal_birth_date=self.context.get('principal_birth_date', ''))
        if owner:
            from app.documents.power_of_attorney import formatted_address
            values.update(name=owner.get('nazwa_organizacji') or ' '.join(str(owner.get(k) or '') for k in ('imie_pierwsze','nazwisko')),
                          nip=owner.get('nip') or '', pesel=owner.get('pesel') or '',
                          principal_first_name=owner.get('imie_pierwsze') or '', principal_surname=owner.get('nazwisko') or '',
                          principal_birth_date=owner.get('data_urodzenia') or '', email=owner.get('email') or '',
                          phone=owner.get('telefon') or '')
            values.update(street=owner.get('ulica') or values.get('street', ''),
                          building=owner.get('nr_domu') or values.get('building', ''),
                          apartment=owner.get('nr_mieszkania') or '', postcode=owner.get('kod_pocztowy') or values.get('postcode', ''),
                          city=owner.get('miejscowosc') or values.get('city', ''), country=owner.get('panstwo') or 'PL')
            if str(values['country']).casefold() in ('polska', 'poland'):
                values['country'] = 'PL'
            values['address'] = formatted_address(values)
        for key, widget in self.fields.items():
            if key in values:
                widget.setText(str(values.get(key) or ''))
        self.principal_kind.setCurrentIndex(max(0, self.principal_kind.findData('person' if owner else self.context.get('principal_kind'))))

    def _select_employee_row(self, index):
        if index >= 0:
            self.employee.setCurrentIndex(self.employee.findData(self.employee_checks.item(index).data(Qt.ItemDataRole.UserRole)))

    def _selected_attorneys(self):
        from app.employees.authority_profiles import load
        result = []
        for index in range(self.employee_checks.count()):
            item = self.employee_checks.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                employee_id = item.data(Qt.ItemDataRole.UserRole)
                values = {**load(employee_id, self.actor_id), **self._attorney_overrides.get(employee_id, {})}
                if employee_id == self.employee.currentData():
                    values.update({key: widget.text().strip() for key, widget in self.attorney_fields.items()})
                    values['identity_type'] = self.identity_type.currentData()
                result.append(dict(values, employee_id=employee_id))
        return result

    def _employee_changed(self):
        from app.employees.authority_profiles import load
        employee_id = self.employee.currentData()
        if self._edited_attorney_id:
            self._attorney_overrides[self._edited_attorney_id] = {key: w.text().strip() for key, w in self.attorney_fields.items()}
            self._attorney_overrides[self._edited_attorney_id]['identity_type'] = self.identity_type.currentData()
        profile = {**load(employee_id, self.actor_id), **self._attorney_overrides.get(employee_id, {})} if employee_id else {}
        self._edited_attorney_id = employee_id
        for key, widget in self.attorney_fields.items():
            widget.setText(profile.get(key, ''))
        self.identity_type.setCurrentIndex(max(0, self.identity_type.findData(profile.get('identity_type', '1'))))

    def _sync_client_address(self):
        from app.documents.power_of_attorney import formatted_address
        values = {key: self.fields[key].text().strip() for key in ('street', 'building', 'apartment', 'postcode', 'city') if key in self.fields}
        self.fields['address'].setText(formatted_address(values))

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
        values.update(attorneys=self._selected_attorneys(), principal_kind=self.principal_kind.currentData(),
                      attorney=dict(employee_id=self.employee.currentData(), identity_type=self.identity_type.currentData(),
                                    **{key: w.text().strip() for key, w in self.attorney_fields.items()}),
                      authority_from=self.authority_from.date().toString('dd.MM.yyyy'),
                      authority_until='',
                      pel_scope='all', pel_ezus='full',
                      pel_pit=self.pel_pit.isChecked())
        return values

    def _generate_contract(self):
        self._generate_documents([
            '00_framework',
            '01_terms',
            '02_prices',
            '03_processing',
            '04_checklist',
            '05_pep',
            '06_ksef',
            '07_gdpr',
            '09_authorization',
        ])

    def _generate_upl(self):
        self._generate_documents([
            '11_upl',
        ])

    def _generate_pel(self):
        selected = ['12_pel']

        reps = [
            r for r in self.values()['representatives']
            if r.get('imie_pierwsze') or r.get('nazwisko')
        ]

        if len(reps) > 1:
            selected.append('13_pel_z')

        self._generate_documents(selected)

    def _generate(self, docx_only=False):
        selected = [key for key, checkbox in self.documents.items() if checkbox.isChecked()]
        self._generate_documents(selected, docx_only=docx_only,
                                 extra_pdfs=tuple(self.extra_pdfs), duplex=self.duplex.isChecked())

    def _generate_documents(self, selected, *, docx_only=False, extra_pdfs=(), duplex=False):
        if self._busy:
            return

        data = self.values()

        if not data['name'] or not data['address']:
            QMessageBox.warning(
                self,
                'Dokumenty',
                'Uzupełnij nazwę i adres.'
            )
            return

        if any(x in ('11_upl', '12_pel', '13_pel_z') for x in selected):
            if not data.get('attorneys'):
                QMessageBox.warning(
                    self,
                    'Pełnomocnictwa',
                    'Zaznacz co najmniej jednego pracownika jako pełnomocnika.'
                )
                return

        company_name = data.get('name', '').strip()

        employee_initials = ''
        attorneys = data.get('attorneys') or []
        employee_id = attorneys[0]['employee_id'] if len(attorneys) == 1 else None

        if employee_id in self._employees:
            employee = self._employees[employee_id]

            first_name = str(employee.get('imie') or '').strip()
            last_name = str(employee.get('nazwisko') or '').strip()

            employee_initials = (
                    (first_name[:1] if first_name else '') +
                    (last_name[:1] if last_name else '')
            ).upper()

        if selected == ['11_upl']:
            default_name = f'UPL {company_name} {employee_initials}.pdf'

        elif selected == ['12_pel']:
            default_name = f'PEL {company_name} {employee_initials}.pdf'

        elif selected == ['13_pel_z']:
            default_name = f'PEL-Z {company_name} {employee_initials}.pdf'

        elif '12_pel' in selected and '13_pel_z' in selected:
            default_name = f'PEL {company_name} {employee_initials}.pdf'

        elif selected == [
            '00_framework',
            '01_terms',
            '02_prices',
            '03_processing',
            '04_checklist',
            '05_pep',
            '06_ksef',
            '07_gdpr',
            '09_authorization',
        ]:
            default_name = f'Umowa {company_name}.pdf'

        else:
            default_name = f'{company_name}.pdf'

        path, _ = QFileDialog.getSaveFileName(
            self,
            'Zapisz dokument',
            str(Path(default_name).with_suffix('.zip')) if docx_only else default_name,
            'ZIP (*.zip)' if docx_only else 'PDF (*.pdf)'
        )

        if not path:
            return

        extension = '.zip' if docx_only else '.pdf'
        if not path.lower().endswith(extension):
            path += extension

        self._set_busy(True)
        self.status.setText('Generowanie dokumentu…')

        try:
            self.runner.submit(
                lambda: generate_pack(
                    self.company_id,
                    self.actor_id,
                    data,
                    selected,
                    path,
                    extra_pdfs=extra_pdfs,
                    duplex=duplex,
                    docx_only=docx_only,
                    form_renderer=self.print_renderer.render,
                ),
                self._done,
                self._error,
            )
        except Exception as error:
            self._error(error)

    def _set_busy(self, busy):
        self._busy = busy
        for button in (
                self.contract_button,
                self.upl_button,
                self.pel_button,
                self.pdf_button,
                self.docx_button,
                self.close_button,
        ):
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
