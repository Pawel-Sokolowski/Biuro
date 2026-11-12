from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, QCheckBox,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QSpinBox)
from app.db.dao_conn import get_conn
from app.integrations.ezla.settings import load_settings, save_settings
from app.utils.background import TaskRunner


class EzlaSettings(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self._require_admin()
        config = load_settings()
        self.runner = TaskRunner(self)
        layout = QVBoxLayout(self)
        note = QLabel('Raporty są już generowane w PUE/eZUS. Podaj wspólny login usługi raportów, '
                      'hasło usługi i osobne hasło szyfrowania ZIP. To nie jest login do PUE ani hasło poczty. '
                      'Hasła i pobrane raporty używają wspólnego klucza BIO_ENCRYPTION_KEY na komputerach i workerze.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.enabled = QCheckBox('Włącz automatyczne pobieranie dla zaznaczonych firm')
        self.enabled.setChecked(config['enabled'])
        layout.addWidget(self.enabled)
        form = QFormLayout()
        self.login = QLineEdit(config['login'])
        self.password, self.zip_password = QLineEdit(), QLineEdit()
        for widget in (self.password, self.zip_password):
            widget.setEchoMode(QLineEdit.EchoMode.Password)
            widget.setPlaceholderText('Puste pole zachowuje zapisane hasło')
        form.addRow('Dedykowany login / e-mail usługi', self.login)
        form.addRow('Hasło usługi raportów', self.password)
        form.addRow('Hasło szyfrowania ZIP', self.zip_password)
        self.pause, self.error_pause = QSpinBox(), QSpinBox()
        self.pause.setRange(1, 60)
        self.error_pause.setRange(5, 600)
        self.pause.setValue(config['pause_seconds'])
        self.error_pause.setValue(config['error_pause_seconds'])
        for widget in (self.pause, self.error_pause):
            widget.setSuffix(' s')
        form.addRow('Przerwa między firmami', self.pause)
        form.addRow('Przerwa po błędzie ZUS', self.error_pause)
        layout.addLayout(form)
        with get_conn() as conn:
            self.companies = conn.execute('SELECT id,nazwa,nip FROM firmy ORDER BY nazwa').fetchall()
        all_companies = QCheckBox('Zaznacz wszystkie firmy')
        layout.addWidget(all_companies)
        self.table = QTableWidget(len(self.companies), 3)
        self.table.setHorizontalHeaderLabels(['Pobieraj', 'Firma', 'NIP'])
        for row, company in enumerate(self.companies):
            for column, value in enumerate(('', company['nazwa'], company['nip'])):
                item = QTableWidgetItem(str(value or ''))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if company['id'] in config['companies'] else Qt.CheckState.Unchecked)
                self.table.setItem(row, column, item)
        all_companies.toggled.connect(self.select_all)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.status = QLabel('Włącz także Worker w tle. Zadanie e-ZLA działa niezależnie od importu Enovy; '
                             'domyślnie sprawdza usługę co godzinę. Import do Enovy nie jest wykonywany.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        for title, action in [('Zapisz ustawienia e-ZLA', self.save),
                              ('Sprawdź usługę i ZIP — firma z wybranego wiersza', self.check),
                              ('Pobierz teraz — zapisane ustawienia i zaznaczone firmy', self.download)]:
            button = QPushButton(title)
            button.clicked.connect(action)
            layout.addWidget(button)

    def _require_admin(self):
        from app.security.authorization import is_administrator
        if not is_administrator(self.actor_id):
            raise PermissionError('Brak uprawnień do usługi e-ZLA.')

    def select_all(self, checked):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def save(self):
        try:
            save_settings({'enabled': self.enabled.isChecked(), 'login': self.login.text(),
                           'pause_seconds': self.pause.value(), 'error_pause_seconds': self.error_pause.value(),
                           'companies': [company['id'] for row, company in enumerate(self.companies)
                               if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]},
                          actor_id=self.actor_id, password=self.password.text(), zip_password=self.zip_password.text())
            self.password.clear()
            self.zip_password.clear()
            self.status.setText('Zapisano. Worker odczyta ustawienia przy następnym przebiegu zadania e-ZLA. '
                                'Sprawdź, czy Worker w tle i zadanie e-ZLA są włączone.')
        except Exception as error:
            self.failed(error)

    def check(self):
        try:
            self._require_admin()
            row = self.table.currentRow()
            if row < 0:
                raise ValueError('Wybierz wiersz firmy do sprawdzenia połączenia.')
            old = load_settings()
            from app.utils.crypto import decrypt_password
            password = self.password.text() or (decrypt_password(old['password_enc']) if old['password_enc'] else '')
            zip_password = self.zip_password.text() or (decrypt_password(old['zip_password_enc']) if old['zip_password_enc'] else '')
            login = self.login.text().strip()
            from app.integrations.enova.document_counts import normalize_nip
            nip = normalize_nip(self.companies[row]['nip'])
            def work():
                self._require_admin()
                from app.integrations.ezla.service import fetch_reports
                today = datetime.now(ZoneInfo('Europe/Warsaw')).date()
                return len(fetch_reports(nip, login, password, zip_password, today - timedelta(days=30), today=today))
            self.status.setText('Sprawdzanie usługi raportów ZUS…')
            self.runner.submit(work, lambda count: self.status.setText(
                f'Połączenie poprawne. Raporty: {count}. ' + ('ZIP odczytano poprawnie.' if count else
                'Brak raportów w ostatnich 30 dniach — hasła ZIP nie można jeszcze zweryfikować.')), self.failed)
        except Exception as error:
            self.failed(error)

    def download(self):
        from app.integrations.ezla.sync import run_sync
        self.status.setText('Pobieranie raportów dla firm z zapisanej konfiguracji…')
        self.runner.submit(lambda: run_sync(actor_id=self.actor_id), lambda result: self.status.setText(
            f"Przetworzone firmy: {result['companies']}; błędy: {result['failed']}. "
            'Szczegóły w Kadry → Sprawdzanie → e-ZLA. Wyłączona konfiguracja nie pobiera raportów.'), self.failed)

    def failed(self, error):
        self.status.setText(str(error) if isinstance(error, (ValueError, PermissionError)) else
                            'Operacja e-ZLA nie powiodła się. Sprawdź ustawienia, połączenie i wspólny klucz.')
