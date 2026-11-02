"""User-triggered full-mail downloads for any permitted email account."""
import threading
import time
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget
from app.email.email_storage import get_email_accounts_for_employee, get_all_email_accounts
from app.email.email_sync import sync_requested_accounts
from app.security.authorization import can


class MailSyncSettings(QWidget):
    progress = pyqtSignal(object)
    finished = pyqtSignal(object)

    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.running = False
        self.cancel_event = threading.Event()
        layout = QVBoxLayout(self)
        title = QLabel('Pobieranie pełnej poczty')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        layout.addWidget(title)
        description = QLabel('Pobierz pełne wiadomości i załączniki z ostatnich dwóch lat ze wszystkich folderów. '
                             'Pobieranie uruchamia się tylko tym przyciskiem, działa w tle i nie blokuje innych modułów. '
                             'Po przerwaniu uruchom tę opcję ponownie — zapisane wiadomości zostaną pominięte.')
        description.setWordWrap(True);layout.addWidget(description)
        self.accounts = QComboBox()
        layout.addWidget(self.accounts)
        self.start_button = QPushButton('Pobierz pełne wiadomości z 2 lat')
        self.start_button.clicked.connect(self.start)
        layout.addWidget(self.start_button)
        self.status = QLabel('Nie uruchomiono. Zwykłe sprawdzanie nowej poczty działa co dwie minuty.')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        layout.addStretch()
        self.progress.connect(self._progress)
        self.finished.connect(self._finished)
        self._load_accounts()

    def _permitted_accounts(self):
        return get_all_email_accounts() if can(self.actor_id,'can_access_admin_settings') else get_email_accounts_for_employee(self.actor_id)

    def _load_accounts(self):
        self.accounts.clear()
        self.accounts.addItem('Wszystkie dostępne konta',None)
        for account in self._permitted_accounts():
            self.accounts.addItem(account['email'],account['id'])
        self.start_button.setEnabled(self.accounts.count()>1)

    def start(self):
        if self.running:
            return
        selected = self.accounts.currentData()
        # Recheck permissions at click time; never trust the cached account list.
        accounts = [account for account in self._permitted_accounts() if selected is None or account['id']==selected]
        if not accounts:
            QMessageBox.information(self,'Synchronizacja poczty','Brak dostępnych kont e-mail.')
            return
        if QMessageBox.question(self,'Pobierz pełną pocztę',
            f'Pobrać pełne wiadomości i załączniki z ostatnich dwóch lat dla {len(accounts)} kont? '
            'Pobieranie obejmie wszystkie wybieralne foldery i będzie działało w tle.',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.running = True
        self.cancel_event.clear()
        self.started = time.monotonic()
        self.start_button.setEnabled(False)
        self.accounts.setEnabled(False)
        self.status.setText('Rozpoczynam pobieranie w tle… Możesz pracować w innych modułach.')
        def work():
            try:
                errors = sync_requested_accounts(accounts,cancel_event=self.cancel_event,
                    on_progress=lambda *data:self.progress.emit(data))
            except Exception as exc:
                errors = [str(exc)]
            try:
                self.finished.emit(errors)
            except RuntimeError:
                pass
        threading.Thread(target=work,daemon=True).start()

    def _progress(self,data):
        account,done,total,folder,headers = data
        elapsed = int(time.monotonic()-self.started)
        self.status.setText(f'{account} · foldery {done}/{total} · {folder} · nagłówki: {headers} · czas {elapsed//60}:{elapsed%60:02d}')

    def _finished(self,errors):
        self.running = False
        self.start_button.setEnabled(True)
        self.accounts.setEnabled(True)
        self.status.setText('Pobieranie zakończone.' if not errors else 'Pobieranie nieukończone. Uruchom ponownie, aby wznowić.\n'+'\n'.join(errors))

    def stop(self):
        self.cancel_event.set()
