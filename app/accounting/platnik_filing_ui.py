"""Operator controls for sending selected sets and importing ZUS receipts."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QVBoxLayout)

from app.integrations.platnik.gui_filing import automation, start
from app.integrations.platnik.gui_evidence import ERRORS, ReadError
from app.integrations.platnik.gui_pin import forget_pin, read_pin, save_pin
from app.utils.background import CommandRunner


def open_pin_settings(parent):
    dialog = QDialog(parent)
    dialog.setWindowTitle('Płatnik — PIN certyfikatu')
    layout = QVBoxLayout(dialog)
    notice = QLabel('PIN jest przechowywany w Menedżerze poświadczeń bieżącego konta Windows. '
                    'Zapisz PIN certyfikatu, którego używasz w Płatniku na tym komputerze.')
    notice.setWordWrap(True)
    layout.addWidget(notice)
    form = QFormLayout()
    pin = QLineEdit()
    pin.setEchoMode(QLineEdit.EchoMode.Password)
    pin.setMaxLength(64)
    form.addRow('Nowy PIN', pin)
    layout.addLayout(form)
    status = QLabel()
    layout.addWidget(status)

    def update_status():
        try:
            status.setText('PIN zapisany.' if read_pin() is not None else 'Brak zapisanego PIN-u.')
        except (OSError, UnicodeError):
            status.setText('Menedżer poświadczeń Windows jest niedostępny.')

    def save():
        try:
            save_pin(pin.text())
        except ValueError as error:
            QMessageBox.warning(dialog, 'Płatnik', str(error))
        except OSError:
            QMessageBox.warning(dialog, 'Płatnik', 'Nie udało się zapisać PIN-u.')
        finally:
            pin.clear()
        update_status()

    def forget():
        pin.clear()
        try:
            forget_pin()
        except (OSError, UnicodeError):
            QMessageBox.warning(dialog, 'Płatnik', 'Nie udało się usunąć PIN-u.')
        update_status()

    for title, action in (('Zapisz PIN', save), ('Usuń zapisany PIN', forget), ('Zamknij', dialog.accept)):
        button = QPushButton(title)
        button.clicked.connect(action)
        layout.addWidget(button)
    update_status()
    dialog.exec()


class PlatnikFilingDialog(QDialog):
    def __init__(self, sync_results, show_result, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Płatnik — wysyłka i potwierdzenia')
        self.resize(540, 340)
        self.runner = CommandRunner(self)
        self.session = None
        self.running = False
        self.sync_results, self.show_result = sync_results, show_result
        layout = QVBoxLayout(self)
        instructions = QLabel(
            'W Płatniku wybierz Narzędzia → Wyślij za wielu płatników. Wybierz zestawy '
            'i zatwierdź listę przyciskiem „OK”. Gdy „Dalej” stanie się aktywny, '
            'kliknij w Biuro „Wyślij wybrane zestawy do ZUS”.\n\n'
            'Po wysyłce wybierz w Płatniku Narzędzia → Pobierz za wielu płatników. '
            'Wskaż wysłane zestawy i kliknij w Biuro „Pobierz i zarejestruj potwierdzenia”.'
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.status = QLabel('Czekam na wybór zestawów w Płatniku.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.send = self._button(layout, 'Wyślij wybrane zestawy do ZUS', lambda: self._start('send'))
        self.receipts = self._button(layout, 'Pobierz i zarejestruj potwierdzenia', lambda: self._start('receipt'))
        self.resume = self._button(layout, 'Wznów otwarty kreator', self._resume)
        self.refresh = self._button(layout, 'Pobierz wyniki do Biuro przez SQL', self._sync)
        self.pin_settings = self._button(layout, 'PIN certyfikatu…', lambda: open_pin_settings(self))
        self._set_running(False)

    @staticmethod
    def _button(layout, title, action):
        button = QPushButton(title)
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def _set_running(self, running):
        self.running = running
        for button in (self.send, self.receipts, self.refresh, self.pin_settings):
            button.setEnabled(not running)
        self.resume.setEnabled(not running and self.session is not None)

    def reject(self):
        if not self.running:
            super().reject()

    def _start(self, mode):
        if self.session is not None:
            self.status.setText('Najpierw wznów otwarty kreator lub zamknij go w Płatniku i wznów, aby zakończyć ten przebieg.')
            return

        def work():
            with automation():
                session = start(mode)
                return session, self._advance(session)

        self._run(work, self._wizard_done, 'Trwa obsługa kreatora Płatnika…')

    def _resume(self):
        session = self.session
        if session is not None:
            def work():
                with automation():
                    return session, self._advance(session)

            self._run(work, self._wizard_done, 'Wznawiam kreator Płatnika…')

    @staticmethod
    def _advance(session):
        try:
            return session.advance()
        except ReadError as error:
            return {'closed': False, 'message': error.detail or ERRORS[error.code]}
        except Exception:
            return {'closed': False, 'message': 'Nie udało się wykonać kroku. Sprawdź stan kreatora w Płatniku przed wznowieniem.'}

    def _wizard_done(self, outcome):
        session, result = outcome
        self.session = None if result['closed'] else session
        self._set_running(False)
        if not result['closed']:
            self.status.setText(result['message'])
        elif not result['finished']:
            self.status.setText('Kreator został zamknięty. Sprawdź wynik w Płatniku.')
        elif session.wizard.mode == 'receipt':
            self._sync()
        else:
            self.status.setText('Kreator wysyłki zakończył się. Sprawdź raport w Płatniku i otwórz kreator pobierania potwierdzeń.')

    def _sync(self):
        def done(result):
            from app.accounting.refresh_ui import STATES
            self.show_result(result)
            self.status.setText('Płatnik SQL — ' + STATES[result['state']])

        self._run(self.sync_results, done, 'Pobieram statusy wysyłek i UPO do Biuro…')

    def _run(self, work, done, message):
        self._set_running(True)
        self.status.setText(message)

        def finished(result):
            self._set_running(False)
            done(result)

        def failed(error):
            self._set_running(False)
            if isinstance(error, ReadError):
                text = error.detail or ERRORS[error.code]
            elif isinstance(error, ValueError):
                text = str(error)
            else:
                text = 'Operacja nie powiodła się. Sprawdź Płatnika i połączenie z Biuro.'
            self.status.setText(text)

        self.runner.submit(work, finished, failed)
