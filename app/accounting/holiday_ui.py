"""Yearly Płatnik relief records. No monthly navigation and no inferred payment status."""
from datetime import datetime
from zoneinfo import ZoneInfo
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QSpinBox,QLineEdit,
    QCheckBox,QPushButton,QTableWidget,QTableWidgetItem,QAbstractItemView)
from app.integrations.platnik.holidays import read_year,FIELDS,LABELS
from app.utils.background import TaskRunner


class ContributionHolidays(QWidget):
    def __init__(self,actor_id,parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.reader = TaskRunner(self)
        self.payload = {}
        self.company_query = ''
        self.loaded_year = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('Rok wakacji składkowych:'))
        self.year = QSpinBox()
        self.year.setRange(2024,2100)
        self.year.setValue(datetime.now(ZoneInfo('Europe/Warsaw')).year)
        self.year.valueChanged.connect(self.refresh)
        bar.addWidget(self.year)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Firma, NIP, imię, nazwisko lub kod ubezpieczenia')
        self.search.textChanged.connect(self.filter_rows)
        bar.addWidget(self.search)
        self.active = QCheckBox('Tylko aktualne (A)')
        self.active.toggled.connect(self.filter_rows)
        bar.addWidget(self.active)
        refresh = QPushButton('Odśwież zapisany odczyt')
        refresh.clicked.connect(self.refresh)
        bar.addWidget(refresh)
        layout.addLayout(bar)
        self.note = QLabel('Źródło: Płatnik. Miesiąc jest cechą ulgi, nie filtrem widoku. '
                           'A — aktualne zwolnienie; N — nieaktualny wpis, nie odmowa. '
                           'Niepotwierdzone informacje o powiadomieniu, oświadczeniu i płatności oznaczamy jako brak danych.')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QTableWidget(0,1+len(FIELDS))
        self.table.setHorizontalHeaderLabels(['Firma',*LABELS])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

    def showEvent(self,event):
        super().showEvent(event)
        self.refresh()

    def hideEvent(self,event):
        self.reader.invalidate()
        super().hideEvent(event)

    def refresh(self,*_):
        year = self.year.value()
        if self.loaded_year!=year:
            self.payload = {}
            self.table.setRowCount(0)
        self.status.setText(f'Odczytywanie zapisu za rok {year}…')
        self.reader.submit(lambda: read_year(self.actor_id,year),self.loaded,self.failed)

    def loaded(self,payload):
        self.payload = payload
        self.loaded_year = self.year.value()
        self.filter_rows()

    def failed(self,_):
        # Keep previous rows on read/decryption failure, explicitly mark them stale.
        self.status.setText('Nie udało się odczytać zestawienia. Widoczne dane mogą być nieaktualne. Sprawdź bazę i wspólny klucz.')

    def set_company_filter(self,query):
        self.company_query = query.casefold()
        self.filter_rows()

    def filter_rows(self,*_):
        rows = list(self.payload.get('rows',[]))
        # Missing records must remain visible, without implying ineligibility or refusal.
        present = {row['nip'] for row in rows}
        from app.integrations.enova.document_counts import normalize_nip
        for company in self.payload.get('companies',[]):
            try:
                nip = normalize_nip(company['nip'])
            except ValueError:
                nip = str(company.get('nip') or '')
            if nip not in present:
                rows.append({'firma':company['nazwa'],'nip':nip,'decyzja':'Brak wpisu RWS w zapisanym odczycie' if self.payload.get('imported_at') else 'Nie odczytano roku z Płatnika'})
        query = self.search.text().strip().casefold()
        rows = [row for row in rows if query in ' '.join(str(row.get(k) or '') for k in ('firma','nip',*FIELDS)).casefold()
                and self.company_query in (str(row.get('firma',''))+' '+str(row.get('nip',''))).casefold()
                and (not self.active.isChecked() or row.get('status_zrodlowy')=='A')]
        self.table.setRowCount(len(rows))
        for index,row in enumerate(rows):
            values = [row.get('firma'),*[row.get(key) for key in FIELDS]]
            amounts = ', '.join(key.removeprefix('skladka_').upper()+': '+str(row.get(key) or '—')
                               for key in ('skladka_em','skladka_ren','skladka_wyp','skladka_chor','skladka_fpfs'))
            info = f"NIP: {row.get('nip','—')}; wpis RWS: {row.get('source_id') or '—'}; status źródłowy: {row.get('status_zrodlowy') or '—'}. "
            info += 'Kwoty z wpisu RWS (nie potwierdzenie zapłaty): '+amounts
            if row.get('issues'):
                info += '\n'+'; '.join(row['issues'])
            for column,value in enumerate(values):
                item = QTableWidgetItem(str(value) if value not in (None,'') else 'Brak danych')
                item.setToolTip(info)
                if row.get('status_zrodlowy')=='N':
                    item.setForeground(QColor('#7c7c7c'))
                self.table.setItem(index,column,item)
        self.table.resizeColumnsToContents()
        stamp = self.payload.get('imported_at')
        message = f'Rok {self.year.value()}; wierszy: {len(rows)}. Ostatni poprawny odczyt: {stamp or "jeszcze nie wykonano"}.'
        if not self.payload.get('configured'):
            message += ' Połączenie Płatnika nie jest włączone w ustawieniach workera.'
        if self.payload.get('status',{}).get('state')=='failed':
            message += ' Ostatnia próba importu nie powiodła się — pokazano poprzedni zapis.'
        self.status.setText(message)
