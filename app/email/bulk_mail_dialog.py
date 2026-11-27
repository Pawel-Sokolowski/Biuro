"""Company selection and review for paced office announcements."""
import csv
from datetime import datetime
from math import ceil
from pathlib import Path
from zoneinfo import ZoneInfo

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QTableWidget, QTextEdit, QVBoxLayout,
)

from app.db.dao_companies import list_mailing_companies
from app.email.bulk_mail import BulkMailPlan, BulkMailSender, client_addresses, filter_companies
from app.companies.company_names import company_display_name
from app.companies.holiday_eligibility import list_candidates
from app.utils.background import TaskRunner
from app.utils.month_names import MONTHS_PL
from app.utils.table_sorting import SortableItem, apply_header_sort, enable_header_sort

STATUS_LABELS = {'sent': 'Wysłano', 'blocked': 'Zablokowano przed wysłaniem',
                 'uncertain': 'Wynik niepotwierdzony', 'not_sent': 'Niewysłano'}


def _csv_cell(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text

class BulkMailDialog(QDialog):
    def __init__(self, actor_id, accounts, current_account_id=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Mail masowy do klientów')
        self.resize(1100, 850)
        self.actor_id = actor_id
        self.companies = list_mailing_companies(actor_id)
        self._selected = set()
        self._addresses = {}
        self._errors = {}
        self._result = None
        self.attachments = []
        self.holiday_reader = TaskRunner(self)
        self.holiday_payload = None
        self.holiday_loading = False
        for company in self.companies:
            try:
                self._addresses[company['id']] = client_addresses(company.get('adres_email'))
            except ValueError as exc:
                self._errors[company['id']] = str(exc)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.account = QComboBox()
        for account in accounts:
            self.account.addItem(account.get('email') or str(account['id']), dict(account))
            if account['id'] == current_account_id:
                self.account.setCurrentIndex(self.account.count() - 1)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText('Np. Biuro zamknięte 10 listopada')
        self.body = QTextEdit()
        self.attach_button = QPushButton('Załączniki...')
        self.attach_button.clicked.connect(self._pick_attachments)

        self.attachments_label = QLabel('Brak załączników')
        self.attachments_label.setWordWrap(True)
        self.body.setAcceptRichText(False)
        self.body.setMinimumHeight(110)
        self.body.setMaximumHeight(180)
        form.addRow('Konto nadawcze:', self.account)
        form.addRow('Temat:', self.subject)
        form.addRow('Treść:', self.body)
        form.addRow('Załączniki:', self.attach_button)
        form.addRow('', self.attachments_label)
        layout.addLayout(form)

        filters = QHBoxLayout()
        self.activity, self.tax = QComboBox(), QComboBox()
        for label, widget, field in (('Działalność:', self.activity, 'forma_dzialalnosci'),
                                      ('Opodatkowanie:', self.tax, 'forma_opodatkowania')):
            widget.addItem('Wszystkie', None)
            for value in sorted({str(row.get(field) or '').strip() for row in self.companies}, key=str.casefold):
                widget.addItem(value or 'Brak danych', value)
            filters.addWidget(QLabel(label))
            filters.addWidget(widget)
            widget.currentIndexChanged.connect(self._render)
        self.active_only = QCheckBox('Tylko aktywne firmy')
        self.active_only.setChecked(True)
        self.active_only.toggled.connect(self._render)
        filters.addWidget(self.active_only)
        layout.addLayout(filters)
        holidays = QHBoxLayout()
        self.holiday_filter = QCheckBox('Wakacje ZUS — niewykorzystane')
        self.holiday_year = QSpinBox()
        self.holiday_year.setRange(2024,2100)
        today = datetime.now(ZoneInfo('Europe/Warsaw'))
        self.holiday_year.setValue(today.year + (today.month==12))
        self.holiday_month = QComboBox()
        for number,name in enumerate(MONTHS_PL,1):
            self.holiday_month.addItem(name,number)
        self.holiday_month.setCurrentIndex(today.month % 12)
        self.holiday_mode = QComboBox()
        self.holiday_mode.addItem('Warunki potwierdzone','eligible')
        self.holiday_mode.addItem('Do weryfikacji','review')
        self.holiday_edit = QPushButton('Sprawdź / uzupełnij warunki…')
        self.holiday_edit.clicked.connect(self._edit_holiday_conditions)
        for widget in (self.holiday_filter,QLabel('Rok ulgi:'),self.holiday_year,QLabel('Miesiąc ulgi:'),self.holiday_month,self.holiday_mode,self.holiday_edit):
            holidays.addWidget(widget)
        layout.addLayout(holidays)
        self.holiday_note = QLabel()
        self.holiday_note.setWordWrap(True)
        self.holiday_note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.holiday_note)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Szukaj firmy po nazwie, NIP-ie lub adresie e-mail…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._render)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 6)
        enable_header_sort(self.table)
        self.table.setHorizontalHeaderLabels(['Wyślij', 'Firma', 'Kontaktowy e-mail', 'Działalność', 'Opodatkowanie','Wakacje ZUS — osoby / wynik'])
        self.table.setColumnHidden(5,True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.cellChanged.connect(self._check_changed)
        layout.addWidget(self.table, 1)
        selection = QHBoxLayout()
        self.select_all = QPushButton('Zaznacz widoczne')
        self.select_none = QPushButton('Odznacz wszystkie')
        self.select_all.clicked.connect(self._select_visible)
        self.select_none.clicked.connect(self._select_none)
        self.count = QLabel()
        for widget in (self.select_all, self.select_none, self.count):
            selection.addWidget(widget)
        selection.addStretch()
        layout.addLayout(selection)
        hint = QLabel('Zmiana filtrów odznacza firmy poza wynikiem. Firmy bez poprawnego kontaktowego e-maila '
                      'nie mogą zostać zaznaczone. Powtarzające się adresy otrzymają wiadomość tylko raz.')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        pacing = QHBoxLayout()
        self.batch_size, self.pause = QSpinBox(), QSpinBox()
        self.batch_size.setRange(1, 50)
        self.batch_size.setValue(25)
        self.pause.setRange(10, 3600)
        self.pause.setValue(60)
        self.pause.setSuffix(' s')
        pacing.addWidget(QLabel('Adresów w partii:'))
        pacing.addWidget(self.batch_size)
        pacing.addWidget(QLabel('Przerwa między partiami:'))
        pacing.addWidget(self.pause)
        pacing.addStretch()
        layout.addLayout(pacing)
        privacy = QLabel('Adresy klientów trafią do ukrytej kopii (UDW). W polu „Do” będzie tylko konto nadawcze. '
                         'Dostosuj tempo do limitów swojego serwera pocztowego.')
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.status = QLabel('Przygotuj wiadomość i zaznacz firmy, a następnie sprawdź odbiorców przed wysłaniem.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(95)
        self.details.hide()
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        self.send_button = QPushButton('Sprawdź i wyślij…')
        self.stop_button = QPushButton('Zatrzymaj wysyłkę')
        self.stop_button.setEnabled(False)
        self.export_button = QPushButton('Zapisz raport…')
        self.export_button.setEnabled(False)
        close_button = QPushButton('Zamknij')
        self.send_button.clicked.connect(self._review_send)
        self.stop_button.clicked.connect(self._stop)
        self.export_button.clicked.connect(self._export_report)
        close_button.clicked.connect(self.reject)
        for widget in (self.send_button, self.stop_button, self.export_button, close_button):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self.sender = BulkMailSender(self)
        self.sender.progress.connect(self._progress)
        self.sender.finished.connect(self._finished)
        self._controls = (self.account, self.subject, self.body, self.activity, self.tax,
                          self.active_only, self.search, self.table, self.select_all, self.select_none, self.batch_size, self.pause,
                          self.holiday_filter,self.holiday_year,self.holiday_month,self.holiday_mode,self.holiday_edit)
        self.holiday_filter.toggled.connect(self._holiday_changed)
        self.holiday_year.valueChanged.connect(self._holiday_changed)
        self.holiday_month.currentIndexChanged.connect(self._holiday_changed)
        self.holiday_mode.currentIndexChanged.connect(self._holiday_mode_changed)
        self._holiday_changed()
        self._render()

    def _holiday_period(self):
        return f'{self.holiday_year.value():04d}-{self.holiday_month.currentData():02d}'

    def _holiday_mode_changed(self,*_):
        # The same SC company can represent different partners in each mode.
        self._selected.clear()
        self._render()

    def _holiday_changed(self,*_):
        enabled = self.holiday_filter.isChecked()
        for widget in (self.holiday_year,self.holiday_month,self.holiday_mode):
            widget.setEnabled(enabled)
        self.table.setColumnHidden(5,not enabled)
        self.holiday_reader.invalidate()
        self.holiday_payload = None
        self.holiday_loading = enabled
        if not enabled:
            self.holiday_note.clear()
            self._render()
            return
        month = self._holiday_period()
        self.holiday_note.setText('Sprawdzanie kodów, warunków i wpisów RWS…')
        self._render()
        self.holiday_reader.submit(lambda:list_candidates(self.actor_id,month),self._holiday_loaded,self._holiday_failed)

    def _holiday_loaded(self,payload):
        self.holiday_payload = payload
        self.holiday_loading = False
        from app.email.holiday_conditions_dialog import month_label
        period = payload['context']
        self.holiday_note.setText(f"Ulga: {month_label(period['month'])}; wniosek: {month_label(period['application_month'])}; "
            f"liczba osób i E/R/W: {month_label(period['reference_month'])}. "
            'Brak danych trafia do weryfikacji. Dla SC wybierani są osobno wspólnicy według indywidualnego NIP-u i e-maila. '
            'Kolumna wyniku wyjaśnia warunki. Przed każdą partią program ponownie sprawdza odbiorców.')
        self._render()

    def _holiday_failed(self,_):
        self.holiday_loading = False
        self.holiday_payload = None
        self.holiday_note.setText('Nie udało się sprawdzić uprawnień. Lista jest pusta; wyłącz i włącz filtr, aby ponowić odczyt.')
        self._render()

    def _edit_holiday_conditions(self):
        index = self.table.currentRow()
        if index<0:
            QMessageBox.information(self,'Wakacje ZUS','Wybierz wiersz firmy. W trybie „Do weryfikacji” uzupełnisz brakujące warunki.')
            return
        company_id = self.table.item(index,0).data(Qt.ItemDataRole.UserRole)
        from app.email.holiday_conditions_dialog import HolidayConditionsDialog
        dialog = HolidayConditionsDialog(self.actor_id,company_id,self._holiday_period(),self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self._holiday_changed()

    def _visible(self):
        rows = self.companies
        if self.holiday_filter.isChecked():
            mode = self.holiday_mode.currentData()
            payload = self.holiday_payload or {}
            if payload.get('context',{}).get('month')!=self._holiday_period():
                return []
            rows = [{**row,'adres_email':row['holiday_addresses'][mode]} for row in payload['companies']
                    if any(p['status']==mode for p in row['holiday_people'])]
        return filter_companies(rows, query=self.search.text(), activity=self.activity.currentData(),
                                tax=self.tax.currentData(),
                                active_only=self.active_only.isChecked())

    def _render(self, *_):
        rows = self._visible()
        self._addresses,self._errors = {},{}
        for row in rows:
            try:
                self._addresses[row['id']] = client_addresses(row.get('adres_email'))
            except ValueError as error:
                self._errors[row['id']] = str(error)
        self._selected.intersection_update(row['id'] for row in rows if row['id'] in self._addresses)
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            check = SortableItem()
            check.setData(Qt.ItemDataRole.UserRole, row['id'])
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if row['id'] in self._selected else Qt.CheckState.Unchecked)
            if row['id'] in self._errors:
                check.setFlags(Qt.ItemFlag.NoItemFlags)
                check.setToolTip(self._errors[row['id']])
            self.table.setItem(index, 0, check)
            for column, value in enumerate((company_display_name(row), row.get('adres_email') or 'Brak e-maila',
                                              row.get('forma_dzialalnosci'), row.get('forma_opodatkowania')), 1):
                self.table.setItem(index, column, SortableItem(str(value or '—')))
            detail = '\n'.join(p['name']+': '+'; '.join(p['reasons']) for p in row.get('holiday_people',[]))
            item = SortableItem(detail)
            item.setToolTip(detail)
            self.table.setItem(index,5,item)
        apply_header_sort(self.table)
        self.table.blockSignals(False)
        self._update_count()

    def _check_changed(self, row, column):
        if column != 0:
            return
        item = self.table.item(row, column)
        company_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked and company_id in self._addresses:
            self._selected.add(company_id)
        else:
            self._selected.discard(company_id)
        self._update_count()

    def _select_visible(self):
        self._selected.update(row['id'] for row in self._visible() if row['id'] in self._addresses)
        self._render()

    def _select_none(self):
        self._selected.clear()
        self._render()

    def _update_count(self):
        addresses = {address.casefold() for company_id in self._selected for address in self._addresses[company_id]}
        self.count.setText(f'Firmy: {len(self._selected)} · Unikalne adresy: {len(addresses)}')

    def _review_send(self):
        if self.sender.running or self._result is not None:
            return

        account = self.account.currentData() or {}

        try:
            plan = BulkMailPlan(
                self.actor_id,
                account.get('id'),
                account.get('email', ''),
                self.subject.text().strip(),
                self.body.toPlainText(),
                tuple(
                    (company_id, self._addresses[company_id])
                    for company_id in sorted(self._selected)
                ),
                self.batch_size.value(),
                self.pause.value(),
                tuple(self.attachments),
                self._holiday_period() if self.holiday_filter.isChecked() else None,
                self.holiday_mode.currentData()
            )

        except ValueError as exc:
            QMessageBox.warning(self, 'Mail masowy', str(exc))
            return

        review = QMessageBox(self)
        review.setWindowTitle('Sprawdź wysyłkę do klientów')

        review.setText(
            f'Wysłać „{plan.subject}” z {plan.sender}?\n'
            f'Firmy: {len(plan.companies)}, '
            f'unikalne adresy: {len(plan.recipients)}.\n'
            f'Partie: {ceil(len(plan.recipients) / plan.batch_size)}, '
            f'przerwy: {plan.pause_seconds} s.\n'
            f'Załączniki: {len(plan.attachments)}.\n'
            'Rozwiń szczegóły, aby sprawdzić pełną listę firm i adresów.'
        )

        names = {
            row['id']: company_display_name(row)
            for row in self.companies
        }

        attachments_text = (
            '\n'.join(Path(f).name for f in plan.attachments)
            if plan.attachments
            else 'Brak'
        )

        review.setDetailedText(
            '\n'.join(
                f'{names[company_id]} — {", ".join(addresses)}'
                for company_id, addresses in plan.companies
            )
            + '\n\nZałączniki:\n'
            + attachments_text
            + '\n\nTreść:\n'
            + plan.body
        )

        review.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.Cancel
        )

        review.setDefaultButton(
            QMessageBox.StandardButton.Cancel
        )

        if review.exec() != QMessageBox.StandardButton.Yes:
            return

        self.holiday_reader.invalidate()
        for control in self._controls:
            control.setEnabled(False)

        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        self.sender.start(plan)

    def _progress(self, progress):
        self.progress.setRange(0, progress['total'])
        self.progress.setValue(progress['done'])
        self.status.setText(f'Wysłano do {progress["done"]}/{progress["total"]} adresów. '
                            + (f'Przerwa {self.pause.value()} s przed kolejną partią.' if progress['waiting'] else 'Wysyłanie partii…'))

    def _stop(self):
        self.status.setText('Zatrzymywanie. Rozpoczęta partia zostanie dokończona; kolejne nie zostaną wysłane.')
        self.stop_button.setEnabled(False)
        self.sender.cancel()

    def _finished(self, result):
        self._result = result
        self.stop_button.setEnabled(False)
        self.export_button.setEnabled(True)
        sent = sum(len(entry['recipients']) for entry in result['entries'] if entry['status'] == 'sent')
        uncertain = sum(len(entry['recipients']) for entry in result['entries'] if entry['status'] == 'uncertain')
        self.progress.setValue(sent)
        self.status.setText(f'Wysłano: {sent}. Niepotwierdzony wynik: {uncertain}. Niewysłane: {result["remaining"]}. '
                            'Nie wykonano automatycznych ponowień. Raport zawiera szczegóły każdej partii.')
        if result.get('error'):
            self.status.setText(self.status.text() + ' ' + result['error'])
            if not result['entries']:
                for control in self._controls:
                    control.setEnabled(True)
                self.send_button.setEnabled(True)
        self.details.setPlainText('\n'.join(f'{STATUS_LABELS[entry["status"]]}: {", ".join(entry["recipients"])}\n{entry["info"]}'
                                            for entry in result['entries']))
        self.details.show()

    def _pick_attachments(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            'Wybierz załączniki',
            '',
            'Wszystkie pliki (*.*)'
        )

        if not files:
            return

        self.attachments = files

        names = [Path(f).name for f in files]

        if len(names) <= 3:
            self.attachments_label.setText(', '.join(names))
        else:
            self.attachments_label.setText(
                f'{len(names)} plików: ' +
                ', '.join(names[:3]) +
                ' ...'
            )

    def _export_report(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Zapisz raport', 'mail-masowy.csv', 'CSV (*.csv)')
        if not path:
            return
        try:
            statuses = {address: (entry['status'], entry['info']) for entry in self._result['entries'] for address in entry['recipients']}
            with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream, delimiter=';')
                writer.writerow(['Adres', 'Status', 'Informacja'])
                for address in self.sender.plan.recipients:
                    status, info = statuses.get(address, ('not_sent', ''))
                    writer.writerow([_csv_cell(address), STATUS_LABELS[status], _csv_cell(info)])
        except OSError as exc:
            QMessageBox.warning(self, 'Raport', f'Nie udało się zapisać raportu: {exc}')

    def reject(self):
        if self.sender.running:
            self._stop()
            return
        self.holiday_reader.invalidate()
        super().reject()

    def closeEvent(self, event):
        if self.sender.running:
            self._stop()
            event.ignore()
            return
        self.holiday_reader.invalidate()
        super().closeEvent(event)
