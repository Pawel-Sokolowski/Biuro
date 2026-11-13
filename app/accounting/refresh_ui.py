"""Selection/review controls for the server worker's existing checking adapters."""
import json
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QDialog,
    QDialogButtonBox,QListWidget,QListWidgetItem,QAbstractItemView,QLineEdit,QMessageBox)
from app.utils.background import TaskRunner,CommandRunner
from app.services.checking_refresh import enqueue,latest

LABELS = {'enova':'Enova','ezla':'e-ZLA ZUS','platnik':'Płatnik', 'platnik_declarations':'Płatnik — formularze ZUS',
          'platnik_annual': 'Płatnik — IWA/ZSWA'}
STATES = {'queued':'W kolejce workera','running':'Pobieranie','completed':'Zakończono',
          'partial':'Zakończono z błędami części firm','failed':'Błąd pobierania'}


class CheckingRefreshControls(QWidget):
    def __init__(self,actor_id,companies,selected,period,refresh,parent=None):
        super().__init__(parent)
        self.actor_id,self.companies,self.selected,self.period,self.refresh_view = actor_id,companies,selected,period,refresh
        self.reader,self.writer = TaskRunner(self),CommandRunner(self)
        self.result = None
        self._observed = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.buttons = {}
        for kind,label in (('enova','Pobierz z Enovy…'),('ezla','Pobierz e-ZLA z ZUS…'),
                           ('platnik','Pobierz z Płatnika…'),('platnik_declarations','Pobierz formularze ZUS z Płatnika…'),
                           ('platnik_annual','Pobierz IWA/ZSWA z Płatnika…')):
            button = QPushButton(label)
            button.clicked.connect(lambda _,kind=kind:self.choose(kind))
            self.buttons[kind] = button
            bar.addWidget(button)
        self.details = QPushButton('Wynik pobierania…')
        self.details.clicked.connect(self.show_details)
        self.details.setEnabled(False)
        bar.addWidget(self.details)
        bar.addStretch()
        layout.addLayout(bar)
        self.status = QLabel('Wybierz firmy i zleć pobranie. Dane odczyta worker na serwerze; „Odśwież” pokazuje zapisane dane.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.poll)

    def choose(self,kind):
        companies = self.companies()
        if not companies:
            QMessageBox.information(self,'Pobieranie danych','Najpierw odśwież zestawienie firm.')
            return
        chosen = set(self.selected())
        dialog = QDialog(self)
        dialog.setWindowTitle('Pobierz z '+LABELS[kind]+' — wybierz firmy')
        dialog.resize(700,500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Zaznacz jedną lub kilka firm (Ctrl / Shift). Zaznaczenie z raportu jest przeniesione tutaj.'))
        if kind=='ezla':
            note = QLabel('Usługa ZUS udostępnia wygenerowane raporty z ostatnich 30 dni. '
                          'Zwolnienia pojawią się w zestawieniu według dat zawartych w XML; nie jest to odtworzenie dowolnego miesiąca z ZUS.')
            note.setWordWrap(True)
            layout.addWidget(note)
        search = QLineEdit()
        search.setPlaceholderText('Szukaj firmy lub NIP')
        layout.addWidget(search)
        listing = QListWidget()
        listing.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for company in companies:
            item = QListWidgetItem(f"{company['nazwa']} | {company.get('nip') or 'brak NIP-u'}")
            item.setData(Qt.ItemDataRole.UserRole,company['id'])
            listing.addItem(item)
            item.setSelected(company['id'] in chosen)
        def filter_items(text):
            for index in range(listing.count()):
                item = listing.item(index)
                item.setHidden(text.casefold() not in item.text().casefold())
        search.textChanged.connect(filter_items)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Zleć pobranie wybranych firm')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in listing.selectedItems() if not item.isHidden()]
        if not ids:
            QMessageBox.information(self,'Pobieranie danych','Nie wybrano firm.')
            return
        self.submit(kind,ids,self.period(kind))

    def submit(self,kind,ids,period):
        for button in self.buttons.values():
            button.setEnabled(False)
        def finish():
            for button in self.buttons.values():
                button.setEnabled(True)
        def done(value):
            finish()
            self.loaded(value)
        def failed(error):
            finish()
            QMessageBox.warning(self,'Pobieranie danych',str(error) if isinstance(error,(ValueError,PermissionError)) else 'Nie udało się zapisać żądania do kolejki workera.')
        try:
            self.writer.submit(lambda:enqueue(self.actor_id,kind,ids,period),done,failed)
        except Exception as error:
            failed(error)

    def poll(self):
        if self.isVisible():
            self.reader.submit(lambda:latest(self.actor_id),self.loaded,self.failed,key='checking-refresh-status')

    def loaded(self,value):
        if value is None:
            return
        self.result = value
        state = value['state']
        count = len(value['company_ids'])
        message = f"{LABELS[value['kind']]}: {STATES[state]}; firm: {count}; okres: {value['period']}."
        if state=='queued':
            message += ' Wymagany uruchomiony i włączony worker z obsługą ręcznych pobrań.'
        if state=='running':
            message += f" Przetworzono: {value.get('processed',0)}."
        if value.get('error'):
            message += ' '+value['error']
        self.status.setText(message)
        self.details.setEnabled(True)
        current = (value['id'],value['state'],value.get('finished_at'))
        if current!=self._observed and state in ('completed','partial'):
            self.refresh_view()
        self._observed = current

    def failed(self,error):
        self.status.setText('Nie udało się odczytać stanu pobierania. Sprawdź połączenie i uprawnienia.')

    def show_details(self):
        if not self.result:
            return
        box = QMessageBox(self)
        box.setWindowTitle('Wynik pobierania ze źródła')
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(self.status.text())
        companies = {row['id']:row['nazwa'] for row in self.companies()}
        lines = []
        for result in self.result.get('results',[]):
            company = companies.get(result.get('company_id'),str(result.get('company_id') or '—'))
            success = result.get('ok',result.get('state')=='ok')
            lines.append(company+': '+('pobrano' if success else 'nie pobrano')+'\n'+json.dumps(result,ensure_ascii=False))
        box.setDetailedText('\n\n'.join(lines) or self.result.get('error') or 'Worker jeszcze nie zakończył żądania.')
        box.exec()

    def showEvent(self,event):
        super().showEvent(event)
        self.timer.start()
        self.poll()

    def hideEvent(self,event):
        self.timer.stop()
        self.reader.invalidate()
        super().hideEvent(event)
