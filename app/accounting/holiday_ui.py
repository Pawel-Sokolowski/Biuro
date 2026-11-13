"""Annual office worksheet with separate, read-only Płatnik evidence."""
from datetime import datetime
from zoneinfo import ZoneInfo
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QSpinBox,QLineEdit,
    QCheckBox,QPushButton,QTableWidget,QTableWidgetItem,QAbstractItemView,
    QDialog,QDialogButtonBox,QPlainTextEdit,QMessageBox)
from app.integrations.platnik.holidays import FIELDS,LABELS
from app.integrations.platnik.holiday_tracking import read_tracking_year,save_field,WorksheetConflict
from app.utils.background import TaskRunner,CommandRunner


class ContributionHolidays(QWidget):
    def __init__(self,actor_id,parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.reader = TaskRunner(self)
        self.writer = CommandRunner(self)
        self.saving = False
        self.displayed_rows = []
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
        self.refresh_button = QPushButton('Odśwież zapisany odczyt')
        self.refresh_button.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_button)
        details = QPushButton('Wpisy źródłowe…')
        details.clicked.connect(self.show_sources)
        bar.addWidget(details)
        layout.addLayout(bar)
        self.note = QLabel('Jeden wiersz sprawy klienta na rok. Dwuklik edytuje komórkę; wpisy biura nie są nadpisywane przez import. '
                           '„Kto wysłał” to inicjały pracownika biura. Status RWS i szczegóły źródła są osobno. '
                           'A — aktualne zwolnienie; N — nieaktualny wpis, nie odmowa.')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QTableWidget(0,2+len(FIELDS))
        self.table.setHorizontalHeaderLabels(['Firma',*LABELS,'Status RWS (Płatnik)'])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.edit_cell)
        layout.addWidget(self.table)

    def showEvent(self,event):
        super().showEvent(event)
        self.refresh()

    def hideEvent(self,event):
        self.reader.invalidate()
        super().hideEvent(event)

    def refresh(self,*_):
        if self.saving:
            return
        year = self.year.value()
        if self.loaded_year!=year:
            self.payload = {}
            self.table.setRowCount(0)
        self.status.setText(f'Odczytywanie zapisu za rok {year}…')
        self.reader.submit(lambda: read_tracking_year(self.actor_id,year),self.loaded,self.failed)

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
        query = self.search.text().strip().casefold()
        rows = [row for row in rows if query in ' '.join(str(row.get(k) or '') for k in ('firma','nip',*FIELDS)).casefold()
                and self.company_query in (str(row.get('firma',''))+' '+str(row.get('nip',''))).casefold()
                and (not self.active.isChecked() or 'A' in row.get('statuses',[]))]
        self.displayed_rows = rows
        self.table.setRowCount(len(rows))
        for index,row in enumerate(rows):
            source_status = ', '.join(row.get('statuses',[])) or ('Brak wpisu RWS' if self.payload.get('imported_at') else 'Nie odczytano roku')
            values = [row.get('firma'),*[row.get(key) for key in FIELDS],source_status]
            info = f"NIP: {row.get('nip','—')}\n"
            for source in row.get('sources',[]):
                amounts = ', '.join(key.removeprefix('skladka_').upper()+': '+str(source.get(key) or '—')
                                   for key in ('skladka_em','skladka_ren','skladka_wyp','skladka_chor','skladka_fpfs'))
                info += (f"Wpis RWS #{source.get('source_id')}: {source.get('imiona_nazwiska') or 'brak nazwiska'}; "
                         f"miesiąc: {source.get('miesiac') or '—'}; status: {source.get('status_zrodlowy') or '—'}; "
                         f"kod ulgi: {source.get('kod_ulgi') or '—'}.\nKwoty RWS (nie dowód zapłaty): {amounts}\n")
                info += '; '.join(source.get('issues',[]))+'\n'
            for column,value in enumerate(values):
                item = QTableWidgetItem(str(value) if value not in (None,'') else 'Brak danych')
                detail = info
                if 1<=column<=len(FIELDS):
                    metadata = row.get('notes',{}).get(FIELDS[column-1],{})
                    if metadata.get('value') is not None:
                        detail += f"\nWpis biura: {metadata.get('actor_name','—')}, {metadata.get('updated_at','—')}."
                        item.setBackground(QColor('#e8f3ff'))
                        item.setForeground(QColor('#172b4d'))
                    else:
                        detail += '\nDane źródłowe / brak wpisu biura.'
                item.setToolTip(detail)
                if column==len(values)-1 and row.get('statuses')==['N']:
                    item.setForeground(QColor('#7c7c7c'))
                self.table.setItem(index,column,item)
        self.table.resizeColumnsToContents()
        stamp = self.payload.get('imported_at')
        message = f'Rok {self.year.value()}; wierszy: {len(rows)}. Ostatni poprawny odczyt: {stamp or "jeszcze nie wykonano"}.'
        message += f" Wpisy RWS dostępne w szczegółach: {self.payload.get('source_count',0)}."
        if not self.payload.get('configured'):
            message += ' Połączenie Płatnika nie jest włączone w ustawieniach workera.'
        if self.payload.get('status',{}).get('state')=='failed':
            message += ' Ostatnia próba importu nie powiodła się — pokazano poprzedni zapis.'
        if self.payload.get('unassigned'):
            message += f" Wpisy źródłowe bez jednoznacznego przypisania firmy: {self.payload['unassigned']}."
        self.status.setText(message)

    def show_sources(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(f'Płatnik — wpisy RWS ({self.loaded_year or self.year.value()})')
        dialog.resize(1100,550)
        layout = QVBoxLayout(dialog)
        rows = self.payload.get('source_rows',[])
        fields = ('firma','nip','imiona_nazwiska','bazowy_kod_ubezpieczenia','kod_ulgi','rok','miesiac','status_zrodlowy','source_id')
        table = QTableWidget(len(rows),len(fields))
        table.setHorizontalHeaderLabels(['Firma','NIP','Imię i nazwisko','Kod bazowy','Kod ulgi','Rok','Miesiąc','Status RWS','ID RWS'])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for index,row in enumerate(rows):
            for column,field in enumerate(fields):
                item = QTableWidgetItem(str(row.get(field) or '—'))
                item.setToolTip('; '.join(row.get('issues',[])))
                table.setItem(index,column,item)
        table.resizeColumnsToContents()
        layout.addWidget(table)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def edit_cell(self,index,column):
        if self.saving or not 1<=column<=len(FIELDS) or index>=len(self.displayed_rows):
            return
        row = self.displayed_rows[index]
        field = FIELDS[column-1]
        year = self.loaded_year
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{LABELS[column-1]} — {row['firma']} ({year})")
        layout = QVBoxLayout(dialog)
        source = row.get('source_values',{}).get(field)
        label = QLabel('Płatnik: '+str(source or 'Brak danych'))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        edit = QPlainTextEdit(str(row.get(field) or ''))
        layout.addWidget(edit)
        if field=='kto_wyslal':
            initials = self.payload.get('current_initials','')
            mine = QPushButton(f'Wstaw moje inicjały ({initials})')
            mine.setEnabled(bool(initials))
            mine.clicked.connect(lambda: edit.setPlainText(initials))
            layout.addWidget(mine)
        reset = QCheckBox('Przywróć dane źródłowe / usuń wpis biura')
        reset.toggled.connect(lambda checked: edit.setEnabled(not checked))
        layout.addWidget(reset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        value = None if reset.isChecked() else edit.toPlainText()
        if value is not None and len(value)>2000:
            QMessageBox.warning(self,'Zestawienie','Maksymalnie 2000 znaków w komórce.')
            return
        revision = row.get('notes',{}).get(field,{}).get('revision',0)
        company_id = row['firma_id']
        self.reader.invalidate()
        self.set_saving(True)
        try:
            self.writer.submit(lambda: save_field(self.actor_id,year,company_id,field,value,revision),
                               self.saved,self.save_failed)
        except Exception as error:
            self.save_failed(error)

    def set_saving(self,saving):
        self.saving = saving
        for widget in (self.table,self.year,self.refresh_button,self.search,self.active):
            widget.setEnabled(not saving)
        if saving:
            self.status.setText('Zapisywanie wpisu biura…')

    def saved(self,_):
        self.set_saving(False)
        self.refresh()

    def save_failed(self,error):
        self.set_saving(False)
        message = str(error) if isinstance(error,(WorksheetConflict,PermissionError)) else 'Nie zapisano zmiany. Sprawdź połączenie i wspólny klucz; ponów edycję.'
        QMessageBox.warning(self,'Zestawienie',message)
        self.refresh()
