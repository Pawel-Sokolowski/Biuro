"""Company selection and review for paced office announcements."""
import csv
from math import ceil

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from app.db.dao_companies import list_mailing_companies
from app.email.bulk_mail import BulkMailPlan, BulkMailSender, client_addresses, filter_companies

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
        self.body.setAcceptRichText(False)
        self.body.setMinimumHeight(110)
        self.body.setMaximumHeight(180)
        form.addRow('Konto nadawcze:', self.account)
        form.addRow('Temat:', self.subject)
        form.addRow('Treść:', self.body)
        layout.addLayout(form)

        filters = QHBoxLayout()
        self.activity, self.settlement, self.tax = QComboBox(), QComboBox(), QComboBox()
        for label, widget, field in (('Działalność:', self.activity, 'forma_dzialalnosci'),
                                      ('Rozliczanie:', self.settlement, 'typ_rozliczenia'),
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
        self.search = QLineEdit()
        self.search.setPlaceholderText('Szukaj firmy po nazwie, NIP-ie lub adresie e-mail…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._render)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Wyślij', 'Firma', 'Kontaktowy e-mail', 'Działalność', 'Rozliczanie', 'Opodatkowanie'])
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
        self._controls = (self.account, self.subject, self.body, self.activity, self.settlement, self.tax,
                          self.active_only, self.search, self.table, self.select_all, self.select_none, self.batch_size, self.pause)
        self._render()

    def _visible(self):
        return filter_companies(self.companies, query=self.search.text(), activity=self.activity.currentData(),
                                settlement=self.settlement.currentData(), tax=self.tax.currentData(),
                                active_only=self.active_only.isChecked())

    def _render(self, *_):
        rows = self._visible()
        self._selected.intersection_update(row['id'] for row in rows if row['id'] in self._addresses)
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            check = QTableWidgetItem()
            check.setData(Qt.ItemDataRole.UserRole, row['id'])
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if row['id'] in self._selected else Qt.CheckState.Unchecked)
            if row['id'] in self._errors:
                check.setFlags(Qt.ItemFlag.NoItemFlags)
                check.setToolTip(self._errors[row['id']])
            self.table.setItem(index, 0, check)
            for column, value in enumerate((row.get('nazwa_skrocona') or row['nazwa'], row.get('adres_email') or 'Brak e-maila',
                                              row.get('forma_dzialalnosci'), row.get('typ_rozliczenia'), row.get('forma_opodatkowania')), 1):
                self.table.setItem(index, column, QTableWidgetItem(str(value or '—')))
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
            plan = BulkMailPlan(self.actor_id, account.get('id'), account.get('email', ''),
                                self.subject.text().strip(), self.body.toPlainText(),
                                tuple((company_id, self._addresses[company_id]) for company_id in sorted(self._selected)),
                                self.batch_size.value(), self.pause.value())
        except ValueError as exc:
            QMessageBox.warning(self, 'Mail masowy', str(exc))
            return
        review = QMessageBox(self)
        review.setWindowTitle('Sprawdź wysyłkę do klientów')
        review.setText(f'Wysłać „{plan.subject}” z {plan.sender}?\nFirmy: {len(plan.companies)}, '
                       f'unikalne adresy: {len(plan.recipients)}.\n'
                       f'Partie: {ceil(len(plan.recipients) / plan.batch_size)}, przerwy: {plan.pause_seconds} s.\n'
                       'Rozwiń szczegóły, aby sprawdzić pełną listę firm i adresów.')
        names = {row['id']: row.get('nazwa_skrocona') or row['nazwa'] for row in self.companies}
        review.setDetailedText('\n'.join(f'{names[company_id]} — {", ".join(addresses)}' for company_id, addresses in plan.companies)
                                + '\n\nTreść:\n' + plan.body)
        review.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        review.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if review.exec() != QMessageBox.StandardButton.Yes:
            return
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
        self.details.setPlainText('\n'.join(f'{STATUS_LABELS[entry["status"]]}: {", ".join(entry["recipients"])}\n{entry["info"]}'
                                            for entry in result['entries']))
        self.details.show()

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
        super().reject()

    def closeEvent(self, event):
        if self.sender.running:
            self._stop()
            event.ignore()
            return
        super().closeEvent(event)
