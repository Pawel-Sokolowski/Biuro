from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, QCheckBox,
    QSpinBox, QComboBox, QTableWidget, QTableWidgetItem, QPushButton, QLabel, QMessageBox)
from app.db.dao_conn import get_conn
from app.integrations.enova.connection_settings import load_connection, save_connection


class EnovaConnectionSettings(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Brak uprawnień.')
        self.actor_id = actor_id
        config = load_connection()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.server = QLineEdit(config.get('server', ''))
        self.server.setPlaceholderText(r'192.168.1.10\ENOVA lub sql.firma.local,1433')
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
        self.jpk_query = QLineEdit(config.get('jpk_query', ''))
        self.jpk_query.setPlaceholderText(r'C:\BiuroConfig\jpk.sql (ścieżka na serwerze importu)')
        self.enabled = QCheckBox('Włącz codzienny import')
        self.enabled.setChecked(config['enabled'])
        self.months = QSpinBox()
        self.months.setRange(1, 24)
        self.months.setValue(config['months_back'])
        for label, widget in [('Serwer SQL / instancja', self.server), ('Sterownik SQL', self.driver), ('Login SQL', self.username),
                              ('Hasło SQL', self.password), ('Zapytanie statusu JPK', self.jpk_query), ('Liczba miesięcy wstecz', self.months),
                              ('Import', self.enabled)]:
            form.addRow(label, widget)
        layout.addLayout(form)
        local_note = QLabel('Test połączenia działa z tego komputera, bez uruchamiania workera. '
                            'Możesz sprawdzić sam serwer przed wpisaniem baz firm. '
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
        layout.addWidget(self.table)
        from app.utils.background import TaskRunner
        self.preview_runner = TaskRunner(self)
        self.connection_runner = TaskRunner(self)
        self.connection_button = QPushButton('Sprawdź połączenie z bazą Enova')
        self.connection_button.clicked.connect(self.check_connection)
        layout.addWidget(self.connection_button)
        self.preview_button = QPushButton('Sprawdź zapisane ustawienia — podgląd importu')
        self.preview_button.clicked.connect(self.preview)
        layout.addWidget(self.preview_button)
        save = QPushButton('Zapisz połączenie Enova')
        save.clicked.connect(self.save)
        layout.addWidget(save)
        note = QLabel('Uruchamianie bez otwartej aplikacji: zainstaluj na serwerze codzienne zadanie '
                      'scripts/run_enova_import.ps1 -SavedSettings -Apply. '
                      'Import respektuje etap „Wysłane wyniki”; szczegóły w docs/enova/README.md.')
        note.setWordWrap(True)
        layout.addWidget(note)

    def save(self):
        try:
            config = dict(server=self.server.text().strip(), driver=self.driver.currentData(), username=self.username.text().strip(),
                          jpk_query=self.jpk_query.text().strip(), enabled=self.enabled.isChecked(), months_back=self.months.value(), sources=[])
            for row, company in enumerate(self.companies):
                database = self.table.item(row, 2).text().strip()
                if database:
                    config['sources'].append(dict(nip=company['nip'], database=database))
            save_connection(config, actor_id=self.actor_id, password=self.password.text())
            self.password.clear()
        except Exception as exc:
            QMessageBox.warning(self, 'Enova', str(exc))
            return
        QMessageBox.information(self, 'Enova', 'Zapisano połączenie. Harmonogram na serwerze użyje tych ustawień.')

    def preview(self):
        from app.integrations.enova.document_counts import run_import
        self.preview_button.setEnabled(False)
        def done(results):
            self.preview_button.setEnabled(True)
            lines = []
            for result in results:
                if not result['ok']:
                    lines.append(result['database'] + ': ' + result['error'])
                else:
                    lines.append(result['database'] + ': ' + str(result['counts']) +
                                 ('; JPK: ' + str(result['jpk']) if result.get('jpk') else '; JPK: brak zapytania'))
            QMessageBox.information(self, 'Podgląd Enova', '\n'.join(lines) or 'Brak skonfigurowanych baz.')
        def failed(exc):
            self.preview_button.setEnabled(True)
            QMessageBox.warning(self, 'Enova', str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        self.preview_runner.submit(lambda: run_import(load_connection()), done, failed)

    def check_connection(self):
        from app.integrations.enova.connection_settings import check_connection
        # Use the fields currently on screen; saving and enabling import are unnecessary.
        config = load_connection()
        config.update(server=self.server.text().strip(), driver=self.driver.currentData(), username=self.username.text().strip(), sources=[])
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
