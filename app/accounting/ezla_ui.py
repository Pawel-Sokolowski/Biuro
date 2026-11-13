"""All-company e-ZLA report, backed by local XML previews, never an Enova import."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QFileDialog, QTableWidget, QTableWidgetItem, QAbstractItemView)
from app.utils.background import TaskRunner
from app.integrations.ezla.preview import preview_reports
from app.integrations.ezla.report import MAX_DOCUMENTS
from app.utils.months import month_bounds
from app.integrations.enova.accounting import decoded
from app.integrations.ezla.service import ServiceError


class EzlaPreviewPanel(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.runner = TaskRunner(self)
        self.companies, self.reports, self.remote_reports = {}, {}, {}
        self.remote_errors = []
        self.period, self.only_attention, self.errors = None, False, []
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.open_button = QPushButton('Wczytaj raporty e-ZLA XML z ZUS')
        self.open_button.clicked.connect(self.open_report)
        controls.addWidget(self.open_button)
        clear_button = QPushButton('Wyczyść wczytane raporty')
        clear_button.clicked.connect(self.clear)
        controls.addWidget(clear_button)
        layout.addLayout(controls)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(['Firma', 'Pracownik z XML', 'Identyfikator (ukryty)',
            'Dokument', 'Status ZUS', 'Data wystawienia', 'Od', 'Do', 'Do sprawdzenia / import'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        note = QLabel('Zestawienie raportów pobranych przez worker ZUS oraz lokalnie wczytanych XML. '
                      'Raporty z ZUS są zapisane w bazie w postaci zaszyfrowanej; lokalne XML nie są zapisywane. '
                      'Nie wykonano importu do Enovy ani dopasowania pracownika do jej kartoteki. '
                      'Anulowania i korekty wymagają odrębnej obsługi.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.clear()

    def set_companies(self, rows, period, only_attention=False):
        self.companies = {row['id']: row for row in rows}
        self.period, self.only_attention = period, only_attention
        self._render()

    def set_remote(self, batch):
        remote = {}
        for result in batch['reports']:
            remote.setdefault(result['company']['id'], []).append(result)
        self.remote_reports, self.remote_errors = remote, batch.get('errors', [])

    def clear(self):
        self.runner.invalidate()
        self.reports, self.errors = {}, []
        self._render()

    def open_report(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Raporty e-ZLA z ZUS', '', 'Raport XML (*.xml)')
        if not paths:
            return
        self.summary.setText('Odczytywanie raportów e-ZLA…')
        self.runner.submit(lambda: preview_reports(self.actor_id, paths), self.render_batch, self.failed)

    def failed(self, error):
        message = str(error) if isinstance(error, (ValueError, PermissionError)) else 'Nie udało się odczytać raportów e-ZLA.'
        self.summary.setText(message)

    def render(self, result):
        self.render_batch({'reports': [result], 'errors': []})

    def render_batch(self, batch):
        # Build the next state first: a rejected batch leaves the previous preview intact.
        next_reports = {company_id: dict(reports) for company_id, reports in self.reports.items()}
        for result in batch['reports']:
            report, company = result['report'], result['company']
            key = (report.generated_at, tuple((item.employee_name, item.employee_id, item.identifier_type,
                item.series, item.number, item.status, item.issued_at, item.start, item.end, tuple(item.issues))
                for item in report.certificates))
            next_reports.setdefault(company['id'], {})[key] = result
        if sum(len(result['report'].certificates) for reports in next_reports.values()
               for result in reports.values()) > MAX_DOCUMENTS:
            self.failed(ValueError('Zbyt wiele dokumentów w podglądzie. Wyczyść raporty przed wczytaniem kolejnych.'))
            return
        self.reports, self.errors = next_reports, batch.get('errors', [])
        self._render()

    def _render(self):
        records = []
        loaded = 0
        for company_id, company in sorted(self.companies.items(), key=lambda entry: str(entry[1].get('nazwa') or '').casefold()):
            reports = list(self.remote_reports.get(company_id, [])) + list(self.reports.get(company_id, {}).values())
            sync = decoded(company.get('ezla_sync'))
            failed = sync.get('state') == 'failed'
            failure = str(ServiceError(sync.get('error_code'), http_status=sync.get('http_status'))) if failed else ''
            if not reports:
                status = 'Błąd pobierania ZUS' if failed else ('Brak raportów dla miesiąca' if sync.get('last_success') else 'Nie wczytano raportu XML')
                review = failure or ('Brak dokumentów w pobranych raportach; nie potwierdza kompletności ZUS' if sync.get('last_success') else 'Brak danych z ZUS')
                info = 'Ostatnie poprawne sprawdzenie: ' + str(sync.get('last_success') or '—')
                records.append((company, None, status, review, info))
                continue
            loaded += 1
            seen, visible = {}, []
            for result in reports:
                for certificate in result['report'].certificates:
                    key = (certificate.employee_name, certificate.employee_id, certificate.identifier_type,
                           certificate.series, certificate.number, certificate.status, certificate.issued_at,
                           certificate.start, certificate.end, tuple(certificate.issues))
                    if key in seen:
                        continue
                    seen[key] = True
                    if self.period and not certificate.issues:
                        start, end = month_bounds(self.period)
                        if certificate.end < start or certificate.start >= end:
                            continue
                    visible.append((certificate, result))
            if not visible:
                if not self.only_attention:
                    stamp = max(result['report'].generated_at for result in reports)
                    records.append((company, None, 'Brak pozycji w raporcie dla miesiąca',
                                    'Nie oznacza potwierdzenia kompletności danych ZUS', f'Ostatni wczytany raport: {stamp}'))
                continue
            for certificate, result in visible:
                if self.only_attention and not failed and not certificate.issues and certificate.status == 'Wystawione':
                    continue
                database = result.get('database') or 'Brak mapowania bazy Enovy'
                info = f"NIP: {result['report'].nip}; baza Enovy: {database}; raport: {result['report'].generated_at}"
                records.append((company, certificate, '', certificate.review + ('; ' + failure if failed else ''), info))
        self.table.setRowCount(len(records))
        for row, (company, certificate, status, review, info) in enumerate(records):
            if certificate:
                values = [company['nazwa'], certificate.employee_name,
                          certificate.identifier_type + ': ' + certificate.masked_identifier,
                          certificate.series + ' ' + certificate.number, certificate.status,
                          certificate.issued_at, certificate.start, certificate.end, review]
            else:
                values = [company['nazwa'], '—', '—', '—', status, '—', '—', '—', review]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(info)
                if column in (4, 8) and (not certificate or certificate.issues or certificate.status != 'Wystawione'):
                    item.setBackground(QColor('#fef3c7'))
                    item.setForeground(QColor('#92400e'))
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        message = (f'Firmy: {len(self.companies)}; z wczytanym raportem: {loaded}; bez raportu: {len(self.companies) - loaded}. '
                   'Wyłącznie podgląd — nie wykonano importu do Enovy.')
        errors = self.remote_errors + self.errors
        if errors:
            message += '\n' + '\n'.join(errors)
        self.summary.setText(message)
