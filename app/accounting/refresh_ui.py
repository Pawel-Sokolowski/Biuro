"""Selection/review controls for the server worker's existing checking adapters."""
import json
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QDialog,
    QDialogButtonBox,QListWidget,QListWidgetItem,QAbstractItemView,QLineEdit,QMessageBox,QSpinBox)
from app.utils.background import TaskRunner,CommandRunner
from app.widgets.flow_layout import FlowLayout
from app.services.checking_refresh import run_now,latest

LABELS = {'enova':'Enova','ezla':'e-ZLA ZUS','platnik':'Płatnik', 'platnik_declarations':'Płatnik — formularze ZUS',
          'platnik_annual': 'Płatnik — IWA/ZSWA','declaration_year':'Deklaracje za cały rok'}
STATES = {'queued':'W kolejce workera','running':'Pobieranie','completed':'Zakończono',
          'partial':'Zakończono z błędami części firm','failed':'Błąd pobierania'}


class CheckingRefreshControls(QWidget):
    def __init__(self,actor_id,companies,selected,period,refresh,parent=None,*,annual_only=False,year_sources=None):
        super().__init__(parent)
        self.actor_id,self.companies,self.selected,self.period,self.refresh_view = actor_id,companies,selected,period,refresh
        self.reader,self.writer = TaskRunner(self),CommandRunner(self)
        self.year_sources = list(year_sources or ('platnik','enova'))
        self.annual_only = annual_only
        self.result = None
        self._observed = None
        layout = QVBoxLayout(self)
        bar = FlowLayout()
        self.buttons = {}
        for kind,label in (('enova','Pobierz z Enovy…'),('ezla','Pobierz e-ZLA z ZUS…'),
                           ('platnik','Pobierz z Płatnika…'),('platnik_declarations','Pobierz formularze ZUS z Płatnika…'),
                           ('platnik_annual','Pobierz IWA/ZSWA z Płatnika…')):
            button = QPushButton(label,self)
            button.clicked.connect(lambda _,kind=kind:self.choose(kind))
            self.buttons[kind] = button
            if not annual_only:
                bar.addWidget(button)
            else:
                button.hide()
        self.details = QPushButton('Wynik pobierania…')
        self.details.clicked.connect(self.show_details)
        self.details.setEnabled(False)
        bar.addWidget(self.details)
        layout.addLayout(bar)
        annual = QPushButton('Pobierz deklaracje za cały rok — Enova i Płatnik…')
        annual.clicked.connect(lambda:self.choose('declaration_year'))
        self.buttons['declaration_year'] = annual
        layout.addWidget(annual)
        self.status = QLabel('Wybierz firmy i pobierz dane ze źródła. Pobranie uruchomi się od razu na serwerze; „Odśwież” pokazuje zapisane dane.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.poll)

    def choose(self,kind):
        if kind=='declaration_year' and not self.annual_only:
            from app.accounting.declaration_year_ui import open_declaration_year
            open_declaration_year(self.actor_id,self,self.refresh_view)
            return
        companies = self.companies()
        if not companies:
            QMessageBox.information(self,'Pobieranie danych','Najpierw odśwież zestawienie firm.')
            return
        chosen = set(self.selected())
        dialog = QDialog(self)
        dialog.setWindowTitle('Pobierz z '+LABELS[kind]+' — wybierz firmy')
        dialog.resize(700,500)
        layout = QVBoxLayout(dialog)
        year = None
        if kind=='declaration_year':
            year = QSpinBox()
            year.setRange(2000,2100)
            year.setValue(int(str(self.period(kind))[:4]))
            years = QHBoxLayout()
            years.addWidget(QLabel('Rok deklaracji:'))
            years.addWidget(year)
            layout.addLayout(years)
            note = QLabel('Pobierze deklaracje dla 12 miesięcy z wybranego źródła oraz jego formularze roczne. '
                          'Doda wykryte deklaracje do ustawienia miesięcznego. Brak dokumentu nie wyłącza obowiązku; '
                          'ręczne ustawienia pozostają. Nie pobiera kwot ani nie zmienia etapów pracy.')
            note.setWordWrap(True)
            layout.addWidget(note)
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
        selection = QHBoxLayout()
        select_all = QPushButton('Zaznacz wszystkie widoczne firmy')
        clear = QPushButton('Odznacz wszystkie')
        def select_visible():
            for index in range(listing.count()):
                item = listing.item(index)
                item.setSelected(not item.isHidden())
        select_all.clicked.connect(select_visible)
        clear.clicked.connect(listing.clearSelection)
        selection.addWidget(select_all)
        selection.addWidget(clear)
        layout.addLayout(selection)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Pobierz teraz dla wybranych firm')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in listing.selectedItems() if not item.isHidden()]
        if not ids:
            QMessageBox.information(self,'Pobieranie danych','Nie wybrano firm.')
            return
        self.submit(kind,ids,year.value() if year is not None else self.period(kind))

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
            self.timer.stop()
            QMessageBox.warning(self,'Pobieranie danych',str(error) if isinstance(error,(ValueError,PermissionError)) else 'Nie udało się pobrać danych. Sprawdź bieżące logi serwera.')
        from app.remote.state import client
        connection = client()
        if connection is not None and 'checking_run_now' not in connection.server_features:
            finish()
            self.status.setText('Serwer wymaga aktualizacji do obsługi pobierania bez kolejki. Zaktualizuj worker/API i zaloguj się ponownie.')
            return
        self.status.setText(f'Pobieranie danych na serwerze: firm {len(ids)}, okres {period}…')
        sources = self.year_sources if kind=='declaration_year' else None
        self.timer.start()
        try:
            self.writer.submit(lambda:run_now(self.actor_id,kind,ids,period,sources),done,failed)
        except Exception as error:
            failed(error)

    def poll(self):
        if self.isVisible():
            self.reader.submit(lambda:latest(self.actor_id),self.loaded,self.failed,key='checking-refresh-status')

    def loaded(self,value):
        if value is None:
            self.timer.stop()
            return
        self.result = value
        state = value['state']
        if state in ('queued','running'):
            self.timer.start()
        else:
            self.timer.stop()
        count = len(value['company_ids'])
        message = f"{LABELS[value['kind']]}: {STATES[state]}; firm: {count}; okres: {value['period']}."
        if state=='queued':
            message += ' To wcześniejsze zlecenie oczekujące w kolejce. Nowe pobranie można uruchomić przyciskiem.'
        if state=='running':
            message += f" Przetworzono: {value.get('processed',0)}."
        if state in ('completed','partial','failed') and value.get('results'):
            results = value['results']
            platnik = [row for row in results if row.get('source')=='platnik' and (row.get('ok') or row.get('monthly_imported'))]
            if platnik:
                message += f" Płatnik: firm {len(platnik)}, dokumentów miesięcznych {platnik[0].get('batch_documents',0)}, rocznych {platnik[0].get('batch_annual_documents',0)}."
            failed = [row for row in results if not row.get('ok',row.get('state')=='ok')]
            if failed:
                message += ' Błędy źródeł: '+str(len(failed))+'. '+str(failed[0].get('error') or failed[0].get('error_type') or 'Sprawdź wynik pobrania.')
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
