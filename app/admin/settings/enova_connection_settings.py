from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, QCheckBox,
    QSpinBox, QComboBox, QTableWidget, QTableWidgetItem, QPushButton, QLabel, QMessageBox, QFileDialog,QProgressBar,QDialog)
from PyQt6.QtCore import Qt,pyqtSignal
from app.db.dao_conn import get_conn
from app.integrations.enova.connection_settings import load_connection, save_connection


class EnovaConnectionSettings(QWidget):
    discovery_progress = pyqtSignal(int,int,int,str)
    import_progress = pyqtSignal(int,int,str)

    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Brak uprawnień.')
        self.actor_id = actor_id
        self._discovery_token = 0
        self._discovery_running = False
        self.discovery_progress.connect(self._show_discovery_progress)
        self.import_progress.connect(self._show_import_progress)
        self._import_running = False
        self._discovery_result = {'databases': [], 'matches': {}, 'errors': []}
        self._mapping_server = None
        config = load_connection()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.server = QLineEdit(config.get('server', ''))
        self.server.setPlaceholderText(r'192.168.1.10\ENOVA lub sql.firma.local,1433')
        self.database = QLineEdit(config.get('database',''))
        self.database.setPlaceholderText('Np. Biuro_rachunkowe — baza do testu połączenia i wyszukiwania baz firm')
        from app.integrations.enova.odbc_support import SUPPORTED_DRIVERS
        self.driver = QComboBox()
        self.driver.addItem('Automatycznie — wykryj na tym komputerze', 'auto')
        for name in SUPPORTED_DRIVERS:
            self.driver.addItem(name, name)
        self.driver.setCurrentIndex(max(0, self.driver.findData(config.get('driver', 'auto'))))
        self.username = QLineEdit(config.get('username', ''))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Pozostaw puste, aby zachować zapisane hasło')
        self.trust_certificate = QCheckBox('Ufaj certyfikatowi serwera (własny certyfikat)')
        self.trust_certificate.setChecked(config.get('trust_server_certificate', False))
        self.trust_certificate.setToolTip('Połączenie pozostaje szyfrowane. Ta opcja pomija weryfikację certyfikatu i nazwy serwera.')
        self.declarations_query = QLineEdit(config.get('declarations_query', ''))
        self.declarations_query.setPlaceholderText('Plik SQL odczytu deklaracji i UPO na serwerze workera')
        self.payroll_query = QLineEdit(config.get('payroll_query', ''))
        self.payroll_query.setPlaceholderText('Plik SQL rodzajów dokumentów kadrowych')
        self.hr_events_query = QLineEdit(config.get('hr_events_query', ''))
        self.hr_events_query.setPlaceholderText('Plik SQL terminów umów, badań i BHP')
        self.jpk_query = QLineEdit(config.get('jpk_query', ''))
        self.jpk_query.setPlaceholderText(r'C:\BiuroConfig\jpk.sql (ścieżka na serwerze importu)')
        self.enabled = QCheckBox('Włącz codzienny import')
        self.enabled.setChecked(config['enabled'])
        self.months = QSpinBox()
        self.months.setRange(1, 24)
        self.months.setValue(config['months_back'])
        self.discovery_timeout = QSpinBox()
        self.discovery_timeout.setRange(30,1200)
        self.discovery_timeout.setSuffix(' s')
        self.discovery_timeout.setValue(config.get('discovery_query_timeout',120))
        self.discovery_timeout.setToolTip('Limit pojedynczego zapytania przy wyszukiwaniu baz i odczycie dokumentów, nie całego importu. Domyślnie 120 s.')
        for label, widget in [('Serwer SQL / instancja', self.server), ('Główna baza Enovy', self.database),
                              ('Sterownik SQL', self.driver), ('Login SQL', self.username),
                              ('Hasło SQL', self.password), ('Certyfikat TLS', self.trust_certificate),
                              ('Limit zapytania — wyszukiwanie baz', self.discovery_timeout),
                              ('Deklaracje i UPO', self.declarations_query), ('Dokumenty kadrowe', self.payroll_query), ('Umowy, badania i BHP', self.hr_events_query), ('Zapytanie statusu JPK', self.jpk_query), ('Liczba miesięcy wstecz', self.months),
                              ('Import', self.enabled)]:
            form.addRow(label, widget)
        layout.addLayout(form)
        local_note = QLabel('Test połączenia działa z tego komputera, bez uruchamiania workera. '
                            'Wpisz główną bazę Enovy, aby sprawdzić ją przed przypisaniem baz firm. '
                            'Sterownik musi być zainstalowany na tym komputerze; Enova może używać innego dostawcy SQL.')
        local_note.setWordWrap(True)
        layout.addWidget(local_note)
        note = QLabel('Wpisz nazwę bazy Enova przy każdej importowanej firmie. Puste pole pomija firmę. '
                      'Typ księgowości wynika z formy opodatkowania. Konto SQL wymaga praw tylko do odczytu. '
                      'Hasło jest szyfrowane kluczem BIO_ENCRYPTION_KEY wspólnym dla aplikacji i serwera importu.')
        note.setWordWrap(True)
        layout.addWidget(note)
        with get_conn() as conn:
            self.companies = conn.execute('SELECT id,nazwa,nip FROM firmy ORDER BY nazwa').fetchall()
        sources = {s['nip']: s['database'] for s in config['sources']}
        self.table = QTableWidget(len(self.companies), 3)
        self.table.setHorizontalHeaderLabels(['Firma', 'NIP', 'Baza Enova'])
        from PyQt6.QtCore import Qt
        for row, company in enumerate(self.companies):
            for col, value in enumerate((company['nazwa'], company['nip'])):
                item = QTableWidgetItem(str(value or ''))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
            self.table.setItem(row, 2, QTableWidgetItem(sources.get(company['nip'], '')))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(lambda item: self.update_mapping_status() if item.column() == 2 else None)
        layout.addWidget(self.table)
        from app.utils.background import TaskRunner, CommandRunner
        self.import_runner = CommandRunner(self)
        self.preview_runner = TaskRunner(self)
        self.connection_runner = TaskRunner(self)
        self.discovery_runner = TaskRunner(self)
        self.discovery_button = QPushButton('Znajdź bazy Enovy po NIP')
        self.discovery_button.clicked.connect(self.discover_databases)
        layout.addWidget(self.discovery_button)
        self.discovery_status = QLabel()
        self.discovery_status.setTextFormat(Qt.TextFormat.PlainText)
        self.discovery_status.setWordWrap(True)
        layout.addWidget(self.discovery_status)
        self.discovery_bar = QProgressBar()
        self.discovery_bar.hide()
        layout.addWidget(self.discovery_bar)
        self.mapping_status = QLabel('Ręczne powiązania: wpisz bazę w tabeli lub otwórz listę poniżej. Wyszukiwanie pokaże nierozpoznane bazy.')
        self.mapping_status.setWordWrap(True)
        self.mapping_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.mapping_status)
        self.mapping_button = QPushButton('Nierozpoznane bazy i firmy — ręczne powiązania')
        self.mapping_button.clicked.connect(self.review_mappings)
        layout.addWidget(self.mapping_button)
        self.connection_button = QPushButton('Sprawdź połączenie z bazą Enova')
        self.connection_button.clicked.connect(self.check_connection)
        layout.addWidget(self.connection_button)
        self.preview_button = QPushButton('Sprawdź zapisane ustawienia — podgląd importu')
        self.preview_button.clicked.connect(self.preview)
        layout.addWidget(self.preview_button)
        from datetime import datetime
        from zoneinfo import ZoneInfo
        self.import_year = QSpinBox()
        self.import_year.setRange(2000, 2100)
        self.import_year.setValue(datetime.now(ZoneInfo('Europe/Warsaw')).year)
        year_form = QFormLayout()
        year_form.addRow('Rok jednorazowego importu', self.import_year)
        layout.addLayout(year_form)
        self.import_button = QPushButton('Importuj jednorazowo liczbę dokumentów — cały rok do Sprawdzania')
        self.import_button.clicked.connect(self.import_year_counts)
        layout.addWidget(self.import_button)
        import_note = QLabel('Pobiera 12 miesięcy i wszystkie rodzaje dokumentów dla przypisanych firm, '
                            'także przy wyłączonym harmonogramie. Zapisuje wyłącznie dane Sprawdzania. '
                            'Nie zmienia widoku miesięcznego, faktur, etapów, deklaracji ani danych kadrowych. '
                            'Zwykły import do widoku miesięcznego obejmuje okresy od miesiąca uruchomienia tej konfiguracji '
                            'i zachowuje ręczne wpisy.')
        import_note.setWordWrap(True)
        layout.addWidget(import_note)
        self.import_status = QLabel()
        self.import_status.setWordWrap(True)
        self.import_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.import_status)
        self.import_bar = QProgressBar()
        self.import_bar.hide()
        layout.addWidget(self.import_bar)
        self.schema_runner = TaskRunner(self)
        self.schema_button = QPushButton('Eksportuj strukturę Enova — deklaracje i kadry (wybrana firma)')
        self.schema_button.clicked.connect(self.export_schema)
        layout.addWidget(self.schema_button)
        save = QPushButton('Zapisz połączenie Enova')
        save.clicked.connect(self.save)
        layout.addWidget(save)
        note = QLabel('Uruchamianie bez otwartej aplikacji: zainstaluj wspólny worker przez '
                      'scripts/install_background_worker.cmd na serwerze. '
                      'Harmonogram ustaw w „Worker w tle”. Licznik rozliczeniowy respektuje etap „Wysłane wyniki”.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self._saved_state = self.form_state()
        self.update_mapping_status()

    def review_mappings(self):
        from app.admin.settings.enova_mapping_dialog import EnovaMappingDialog
        if self._mapping_server is not None and self._mapping_server != self.server.text().strip():
            QMessageBox.warning(self, 'Powiązania Enova', 'Serwer zmienił się od wyszukiwania. Wyszukaj bazy ponownie.')
            return
        current = {company['id']: self.table.item(row, 2).text().strip() for row, company in enumerate(self.companies)}
        dialog = EnovaMappingDialog(self.companies, current, self._discovery_result, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for row, company in enumerate(self.companies):
                self.table.item(row, 2).setText(dialog.mappings.get(company['id'], ''))
            self.update_mapping_status()

    def update_mapping_status(self):
        from app.integrations.enova.discovery import mapping_review
        current = {company['id']: self.table.item(row, 2).text().strip() for row, company in enumerate(self.companies)}
        unmatched = sum(row['company_id'] is None for row in mapping_review(self.companies, current, self._discovery_result))
        missing = sum(not value for value in current.values())
        database_status = f'Nieprzypisane bazy: {unmatched}.' if self._mapping_server is not None else 'Nie wyszukano jeszcze baz.'
        self.mapping_status.setText(database_status + f' Firmy bez bazy: {missing}. '
                                   'Otwórz „Nierozpoznane bazy i firmy”, aby sprawdzić powody i powiązać ręcznie. '
                                   'Zmiany przypisań wymagają zapisania połączenia.')

    def _show_import_progress(self, done, total, database):
        if not self._import_running:
            return
        self.import_bar.setRange(0, total)
        self.import_bar.setValue(done)
        self.import_status.setText(f'Import roczny: {done}/{total} baz. {database}')

    def import_year_counts(self):
        if self._import_running:
            return
        try:
            # Use exactly the fields/mappings on screen, including a newly typed password.
            if self.is_dirty():
                self.persist()
            config = load_connection()
            if not config['sources']:
                raise ValueError('Przypisz bazy do firm i zapisz połączenie przed importem.')
        except Exception as exc:
            QMessageBox.warning(self, 'Import roczny Enova', str(exc) if isinstance(exc, (ValueError, PermissionError)) else type(exc).__name__)
            return
        year, actor_id = self.import_year.value(), self.actor_id
        self._import_running = True
        self.import_button.setEnabled(False)
        self.import_bar.setRange(0, 0)
        self.import_bar.show()
        self.import_status.setText(f'Rozpoczynanie importu za {year}…')
        def progress(done, total, database):
            try:
                self.import_progress.emit(done, total, database)
            except RuntimeError:
                pass
        def reset():
            self._import_running = False
            self.import_button.setEnabled(True)
            self.import_bar.hide()
        def done(results):
            reset()
            successes = sum(result['ok'] for result in results)
            summary = f'Rok {year}: zaimportowano {successes}/{len(results)} baz do Sprawdzania. Widok miesięczny bez zmian.'
            self.import_status.setText(summary)
            details = []
            for result in results:
                if result['ok']:
                    details.append(result['database'] + ' — ' + result['company'] + '\n' +
                                   ', '.join(month + ': ' + str(count) for month, count in result['counts'].items()))
                else:
                    details.append(result['database'] + ': BŁĄD — ' + result['error'])
            box = QMessageBox(self)
            box.setWindowTitle('Import roczny Enova — wynik')
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setIcon(QMessageBox.Icon.Information if successes == len(results) else QMessageBox.Icon.Warning)
            box.setText(summary)
            box.setDetailedText('\n\n'.join(details))
            box.exec()
        def failed(exc):
            reset()
            self.import_status.setText('Import nie zakończył się poprawnie.')
            QMessageBox.warning(self, 'Import roczny Enova', str(exc) if isinstance(exc, (ValueError, PermissionError)) else type(exc).__name__)
        def work():
            from app.integrations.enova.checking import import_document_year
            return import_document_year(config, year, actor_id=actor_id, progress=progress)
        try:
            self.import_runner.submit(work, done, failed)
        except Exception as exc:
            failed(exc)

    def form_config(self):
        config = dict(server=self.server.text().strip(), database=self.database.text().strip(),
                          discovery_query_timeout=self.discovery_timeout.value(),
                          driver=self.driver.currentData(), username=self.username.text().strip(),
                          trust_server_certificate=self.trust_certificate.isChecked(),
                          hr_events_query=self.hr_events_query.text().strip(),
                          declarations_query=self.declarations_query.text().strip(), payroll_query=self.payroll_query.text().strip(),
                          jpk_query=self.jpk_query.text().strip(), enabled=self.enabled.isChecked(), months_back=self.months.value(), sources=[])
        for row, company in enumerate(self.companies):
            database = self.table.item(row, 2).text().strip()
            if database:
                config['sources'].append(dict(nip=company['nip'], database=database))
        return config

    def form_state(self):
        import json
        return json.dumps(self.form_config(),sort_keys=True),bool(self.password.text())

    def is_dirty(self):
        return self.form_state()!=self._saved_state

    def persist(self):
        save_connection(self.form_config(),actor_id=self.actor_id,password=self.password.text())
        self.password.clear()
        self._saved_state = self.form_state()

    def reload_saved(self):
        if self.is_dirty():
            return
        config = load_connection()
        for field,key in ((self.server,'server'),(self.database,'database'),(self.username,'username'),
                          (self.declarations_query,'declarations_query'),(self.payroll_query,'payroll_query'),
                          (self.hr_events_query,'hr_events_query'),(self.jpk_query,'jpk_query')):
            field.setText(config.get(key,''))
        self.driver.setCurrentIndex(max(0,self.driver.findData(config['driver'])))
        self.trust_certificate.setChecked(config['trust_server_certificate'])
        self.discovery_timeout.setValue(config['discovery_query_timeout'])
        self.months.setValue(config['months_back'])
        self.enabled.setChecked(config['enabled'])
        sources = {source['nip']:source['database'] for source in config['sources']}
        for row,company in enumerate(self.companies):
            self.table.item(row,2).setText(sources.get(company['nip'],''))
        self._saved_state = self.form_state()

    def save(self):
        try:
            self.persist()
        except Exception as exc:
            QMessageBox.warning(self, 'Enova', str(exc))
            return
        message = 'Zapisano połączenie. Harmonogram na serwerze użyje tych ustawień.'
        if not self.form_config()['sources']:
            message += ' Przed importem znajdź bazy firm po NIP lub przypisz je ręcznie i zapisz.'
        QMessageBox.information(self, 'Enova', message)

    def preview(self):
        from app.integrations.enova.document_counts import run_import
        self.preview_button.setEnabled(False)
        def done(results):
            self.preview_button.setEnabled(True)
            lines = []
            for result in results:
                if not result['ok']:
                    lines.append(result['database'] + ': ' + result['error'])
                elif result.get('kind') == 'checking':
                    lines.append(result['database'] + ': Sprawdzanie — ' + str(result.get('months', 0)) + ' miesięcy')
                else:
                    lines.append(result['database'] + ': ' + str(result['counts']) +
                                 ('; JPK: ' + str(result['jpk']) if result.get('jpk') else '; JPK: brak zapytania'))
            QMessageBox.information(self, 'Podgląd Enova', '\n'.join(lines) or 'Brak skonfigurowanych baz.')
        def failed(exc):
            self.preview_button.setEnabled(True)
            QMessageBox.warning(self, 'Enova', str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        def work():
            from app.integrations.enova.checking import run_checking_import
            config = load_connection()
            return run_import(config) + run_checking_import(config)
        self.preview_runner.submit(work, done, failed)

    def check_connection(self):
        from app.integrations.enova.connection_settings import check_connection
        # Use the fields currently on screen; saving and enabling import are unnecessary.
        config = load_connection()
        config.update(trust_server_certificate=self.trust_certificate.isChecked(), server=self.server.text().strip(),
                      database=self.database.text().strip(),driver=self.driver.currentData(), username=self.username.text().strip(), sources=[])
        for row in range(self.table.rowCount()):
            database = self.table.item(row, 2).text().strip()
            if database:
                config['sources'].append({'database': database})
        password = self.password.text()
        self.connection_button.setEnabled(False)
        def done(results):
            self.connection_button.setEnabled(True)
            text = '\n'.join(result['database'] + (': połączono' if result['ok'] else ': brak połączenia') for result in results)
            if all(result['ok'] for result in results):
                QMessageBox.information(self, 'Połączenie Enova', text)
            else:
                from PyQt6.QtCore import Qt
                failures = [result for result in results if not result['ok']]
                hints, details = [], []
                for result in failures:
                    error = result.get('error', {})
                    code = error.get('sqlstate') or 'brak SQLSTATE'
                    hints.append(result['database'] + ' [' + code + ']: ' + error.get('hint', 'Błąd połączenia.'))
                    details.append(result['database'] + '\n' + error.get('details', 'Brak szczegółów.'))
                box = QMessageBox(self)
                box.setWindowTitle('Połączenie Enova')
                box.setIcon(QMessageBox.Icon.Warning)
                box.setTextFormat(Qt.TextFormat.PlainText)
                box.setText('\n'.join(hints))
                box.setDetailedText('\n\n'.join(details))
                box.exec()
        def failed(exc):
            self.connection_button.setEnabled(True)
            QMessageBox.warning(self, 'Połączenie Enova', str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        self.connection_runner.submit(lambda: check_connection(config, password=password), done, failed)

    def export_schema(self):
        from app.integrations.enova.schema_probe import inspect_schema
        import json
        from pathlib import Path
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, 'Struktura Enova', 'Wybierz firmę w tabeli i wpisz jej bazę Enova.')
            return
        database = self.table.item(row, 2).text().strip()
        if not database:
            QMessageBox.warning(self, 'Struktura Enova', 'Wpisz nazwę bazy przy wybranej firmie.')
            return
        destination, _ = QFileDialog.getSaveFileName(self, 'Eksport struktury bez danych firmowych', 'enova-schema.json', 'JSON (*.json)')
        if not destination:
            return
        config = load_connection()
        config.update(server=self.server.text().strip(), username=self.username.text().strip(),
                      driver=self.driver.currentData(), trust_server_certificate=self.trust_certificate.isChecked())
        password = self.password.text()
        self.schema_button.setEnabled(False)
        def done(result):
            self.schema_button.setEnabled(True)
            try:
                Path(destination).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError:
                QMessageBox.warning(self, 'Struktura Enova', 'Nie udało się zapisać pliku.')
                return
            QMessageBox.information(self, 'Struktura Enova', 'Zapisano nazwy tabel, kolumn i relacje. Eksport nie zawiera haseł ani treści dokumentów.')
        def failed(exc):
            self.schema_button.setEnabled(True)
            QMessageBox.warning(self, 'Struktura Enova', str(exc))
        self.schema_runner.submit(lambda: inspect_schema(config, database, password=password), done, failed)

    def discovery_fields(self):
        return (self.server.text().strip(), self.username.text().strip(), self.driver.currentData(),
                self.trust_certificate.isChecked(), self.password.text(),self.database.text().strip(),self.discovery_timeout.value())

    def _show_discovery_progress(self,token,done,total,database):
        if token!=self._discovery_token or not self._discovery_running:
            return
        self.discovery_bar.setRange(0,total)
        self.discovery_bar.setValue(done)
        if total:
            self.discovery_status.setText(f'Sprawdzono baz: {done}/{total}. '+(f'Odczyt: {database}' if database else ''))
        else:
            self.discovery_status.setText(f'Odczytywanie listy baz: {database}…')

    def discover_databases(self):
        from app.security.authorization import is_administrator
        from app.integrations.enova.discovery import discover, propose
        fields = self.discovery_fields()
        config = load_connection()
        config.update(server=fields[0],username=fields[1],driver=fields[2],trust_server_certificate=fields[3],
                      database=fields[5],discovery_query_timeout=fields[6])
        self._discovery_token += 1
        token = self._discovery_token
        self._discovery_running = True
        self.discovery_bar.setRange(0,0)
        self.discovery_bar.show()
        self.discovery_status.setText('Odczytywanie listy baz…')
        self.discovery_button.setEnabled(False)
        self.discovery_button.setText('Wyszukiwanie baz po NIP…')
        def reset():
            self._discovery_running = False
            self.discovery_bar.hide()
            self.discovery_status.clear()
            self.discovery_button.setEnabled(True)
            self.discovery_button.setText('Znajdź bazy Enovy po NIP')
        def progress(done,total,database):
            try:
                self.discovery_progress.emit(token,done,total,database)
            except RuntimeError:
                pass  # The settings page was closed while the read was running.
        def work():
            if not is_administrator(self.actor_id):
                raise PermissionError('Brak uprawnień do konfiguracji Enovy.')
            return discover(config,password=fields[4],progress=progress)
        def done(result):
            reset()
            if self.discovery_fields()!=fields or not is_administrator(self.actor_id):
                QMessageBox.warning(self,'Enova','Ustawienia zmieniły się podczas wyszukiwania. Uruchom wyszukiwanie ponownie.')
                return
            current = {company['id']:self.table.item(row,2).text().strip() for row,company in enumerate(self.companies)}
            proposals,conflicts = propose(self.companies,current,result)
            for row,company in enumerate(self.companies):
                if company['id'] in proposals:
                    self.table.item(row,2).setText(proposals[company['id']])
            self._discovery_result = result
            self._mapping_server = fields[0]
            self.update_mapping_status()
            details = conflicts + result['warnings'] + result['errors']
            for nip,databases in result['matches'].items():
                details.append(nip + ' → ' + ', '.join(databases))
            box = QMessageBox(self)
            box.setWindowTitle('Bazy Enovy — dopasowanie po NIP')
            from PyQt6.QtCore import Qt
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(f"Widoczne bazy: {result['visible']}; bazy z rozpoznaną strukturą Enovy: {result['scanned']}. "
                        f"Uzupełniono pustych przypisań: {len(proposals)}. "
                        'Nierozpoznane bazy oraz firmy bez bazy zobaczysz w „Nierozpoznane bazy i firmy — ręczne powiązania”. '
                        'Sprawdź wynik i kliknij Zapisz połączenie Enova. '
                        'Ręczne przypisania pozostawiono. Kopie baz i powielone NIP-y wymagają wyboru ręcznego. '
                        'Wyszukiwanie obejmuje tylko bazy widoczne i dostępne dla podanego konta SQL.')
            box.setDetailedText('\n'.join(details) or 'Brak odczytanych NIP-ów. Sprawdź dostęp do baz i tabeli dbo.OddzialyFirmy.')
            box.exec()
        def failed(error):
            reset()
            from app.integrations.enova.odbc_support import SqlOperationError
            if isinstance(error,SqlOperationError):
                detail = error.diagnostic
                from PyQt6.QtCore import Qt
                box = QMessageBox(self)
                box.setWindowTitle('Wyszukiwanie Enova — błąd SQL')
                box.setIcon(QMessageBox.Icon.Warning)
                box.setTextFormat(Qt.TextFormat.PlainText)
                box.setText(str(error))
                box.setDetailedText(f"Serwer: {detail['server']}\nBaza: {detail['database']}\n"
                                    f"Sterownik: {detail['driver']}\n\n{detail['details']}")
                box.exec()
                return
            QMessageBox.warning(self,'Wyszukiwanie Enova',str(error) if isinstance(error,(ValueError,PermissionError)) else
                                'Nie udało się odczytać listy baz. Sprawdź połączenie SQL i uprawnienia do bazy startowej oraz baz firm.')
        self.discovery_runner.submit(work,done,failed)
