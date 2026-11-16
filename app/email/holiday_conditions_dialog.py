"""Recorded, person-specific facts used by the ZUS holiday mailing filter."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox,QDialog,QDialogButtonBox,QFormLayout,QLabel,QLineEdit,
                            QMessageBox,QSpinBox,QVBoxLayout)
from app.companies.holiday_eligibility import read_conditions,save_conditions,CONDITIONS,EligibilityConflict
from app.utils.background import TaskRunner,CommandRunner
from app.utils.month_names import MONTHS_PL


def month_label(month):
    year,number = month.split('-')
    return f'{MONTHS_PL[int(number)-1]} {year}'


class HolidayConditionsDialog(QDialog):
    def __init__(self,actor_id,company_id,month,parent=None):
        super().__init__(parent)
        self.actor_id,self.company_id,self.month = actor_id,company_id,month
        self.reader,self.writer = TaskRunner(self),CommandRunner(self)
        self.people = []
        self.saving = False
        self.setWindowTitle('Warunki wakacji ZUS — '+month_label(month))
        self.resize(780,650)
        layout = QVBoxLayout(self)
        self.status = QLabel('Odczytywanie warunków…')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        form = QFormLayout()
        self.person = QComboBox()
        self.person.currentIndexChanged.connect(self.select_person)
        form.addRow('Osoba / indywidualny płatnik:',self.person)
        self.code = QLineEdit()
        self.code.setPlaceholderText('Np. 05 10 00 — kod bazowy, nie kod ulgi')
        form.addRow('Bazowy kod ubezpieczenia:',self.code)
        self.count = QSpinBox()
        self.count.setRange(-1,1000000)
        self.count.setSpecialValueText('Brak danych')
        self.count.setValue(-1)
        self.count.setToolTip('Unikalne osoby zgłoszone choć przez dzień w całym miesiącu odniesienia, wliczając przedsiębiorcę. '
                              'Dla wspólnika SC tylko jego indywidualne konto płatnika, bez pracowników spółki.')
        form.addRow('Liczba ubezpieczonych:',self.count)
        self.answers = {}
        for name,label in CONDITIONS.items():
            widget = QComboBox()
            for text,value in (('Brak danych',None),('Tak',True),('Nie',False)):
                widget.addItem(text,value)
            self.answers[name] = widget
            form.addRow(label+':',widget)
        layout.addLayout(form)
        self.period = QLabel()
        self.period.setWordWrap(True)
        self.period.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.period)
        note = QLabel('Kod nie potwierdza wszystkich warunków. Przychód: brak przychodów w obu właściwych latach lub limit '
                      '2 mln EUR spełniony w co najmniej jednym. Limit de minimis musi wystarczyć na składki. '
                      'Niewykorzystanie potwierdź dla osoby za rok ulgi; brak RWS w Płatniku sam tego nie dowodzi. '
                      'Ubezpieczenia E/R/W — choć jeden dzień w miesiącu odniesienia, także dobrowolne. '
                      'Zawieszenie w miesiącu wniosku samo nie wyklucza ulgi; sprawdź zgłoszenie w miesiącu ulgi.')
        note.setWordWrap(True)
        layout.addWidget(note)
        source = QLabel('<a href="https://www.zus.pl/-/zwolnienie-z-op%C5%82acania-sk%C5%82adek-zus-wakacje-sk%C5%82adkowe">Zasady wakacji składkowych — ZUS</a>')
        source.setOpenExternalLinks(True)
        layout.addWidget(source)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        layout.addWidget(self.buttons)
        self.reader.submit(lambda:read_conditions(actor_id,company_id,month),self.loaded,self.failed)

    def loaded(self,payload):
        self.people = payload['people']
        self.person.blockSignals(True)
        self.person.clear()
        for person in self.people:
            self.person.addItem(person['name']+' — NIP '+(person['payer_nip'] or 'brak'),person['id'])
        self.person.blockSignals(False)
        period = payload['context']
        years = ' i '.join(map(str,period['income_years']))
        self.period.setText(f"Miesiąc ulgi: {month_label(self.month)}. Wniosek: {month_label(period['application_month'])}. "
                            f"Liczbę osób i E/R/W sprawdź za {month_label(period['reference_month'])}. "
                            f"Przychody: {years}; brak zakazanej współpracy z byłym pracodawcą: "
                            f"rok {int(period['application_month'][:4])-1} i rok wniosku do dnia jego złożenia.")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(self.people))
        self.select_person()

    def select_person(self,*_):
        person = next((p for p in self.people if p['id']==self.person.currentData()),None)
        if not person:
            self.status.setText('Brak osób. Uzupełnij wspólników spółki cywilnej w kartotece firmy.')
            return
        values = person['values']
        self.code.setText(values.get('insurance_code') or person.get('suggested_code') or '')
        self.count.setValue(values.get('insured_count') if values.get('insured_count') is not None else -1)
        for name,widget in self.answers.items():
            index = next((i for i in range(widget.count()) if widget.itemData(i) is values.get(name)),0)
            widget.setCurrentIndex(index)
        self.status.setText('; '.join(person['reasons']) +
                            (f"\nWeryfikacja: pracownik #{values['actor_id']}, {values.get('updated_at','')}" if values.get('actor_id') else ''))

    def save(self):
        if self.saving or not self.people:
            return
        person = next(p for p in self.people if p['id']==self.person.currentData())
        values = {name:widget.currentData() for name,widget in self.answers.items()}
        values.update({'insurance_code':self.code.text(),'insured_count':None if self.count.value()<0 else self.count.value()})
        self.saving = True
        self.buttons.setEnabled(False)
        for control in (self.person,self.code,self.count,*self.answers.values()):
            control.setEnabled(False)
        self.writer.submit(lambda:save_conditions(self.actor_id,self.company_id,self.month,person['id'],values,person['revision']),
                           lambda _:self.accept(),self.failed)

    def failed(self,error):
        self.saving = False
        self.buttons.setEnabled(True)
        for control in (self.person,self.code,self.count,*self.answers.values()):
            control.setEnabled(True)
        message = str(error) if isinstance(error,(ValueError,PermissionError,EligibilityConflict)) else 'Nie udało się odczytać lub zapisać warunków.'
        self.status.setText(message)
        QMessageBox.warning(self,'Wakacje ZUS',message)

    def reject(self):
        if not self.saving:
            self.reader.invalidate()
            super().reject()
