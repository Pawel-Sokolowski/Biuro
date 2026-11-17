"""Visible, global one-shot filing import directly on the office server."""
from datetime import datetime
from zoneinfo import ZoneInfo
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QLabel,QDialog
from app.utils.background import TaskRunner
from app.accounting.refresh_ui import CheckingRefreshControls


class AnnualDeclarationImport(QWidget):
    def __init__(self,actor_id,parent=None,refresh=None,*,sources=None):
        super().__init__(parent)
        sources = list(sources or ('platnik','enova'))
        label = ' i '.join('Płatnik' if source=='platnik' else 'Enova' for source in sources)
        layout = QVBoxLayout(self)
        coverage = 'Obejmuje 12 miesięcy'+(' i JPK' if 'enova' in sources else '')+(' oraz IWA/ZSWA' if 'platnik' in sources else '')+'. '
        note = QLabel('Pobierz teraz wszystkie deklaracje za rok — '+label+'. '+coverage+
                      'Wykryte obowiązki uzupełnią '
                      'ustawienia miesięczne firm. Ręczne ustawienia pozostaną; brak dokumentu '
                      'nie wyłącza obowiązku. Kwoty i etapy pracy nie zostaną zmienione. '
                      'Pobranie uruchomi się bez kolejki na serwerze. Wymagane jest zapisane połączenie do wybranego źródła.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.companies = []
        year = datetime.now(ZoneInfo('Europe/Warsaw')).year
        self.controls = CheckingRefreshControls(actor_id,lambda:self.companies,
            lambda:[row['id'] for row in self.companies],lambda kind:year,
            refresh or (lambda:None),self,annual_only=True,year_sources=sources)
        layout.addWidget(self.controls)
        self.controls.buttons['declaration_year'].setText('Pobierz deklaracje za cały rok — '+label+'…')
        self.controls.buttons['declaration_year'].setEnabled(False)
        self.controls.status.setText('Wczytywanie wszystkich firm…')
        self.reader = TaskRunner(self)
        from app.remote.screen_data import company_identities
        def loaded(rows):
            self.companies = [dict(row) for row in rows]
            self.controls.buttons['declaration_year'].setEnabled(bool(rows))
            self.controls.status.setText(f'Firm: {len(rows)}. Domyślnie zaznaczone wszystkie; wybierz rok i pobierz dane.')
        self.reader.submit(company_identities,loaded,
            lambda error:self.controls.status.setText('Nie udało się wczytać listy firm. Otwórz import ponownie.'))


def open_declaration_year(actor_id,parent=None,refresh=None,*,sources=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle('Wszystkie deklaracje — jednorazowy import roczny')
    dialog.resize(800,400)
    QVBoxLayout(dialog).addWidget(AnnualDeclarationImport(actor_id,dialog,refresh,sources=sources))
    dialog.exec()
    dialog.deleteLater()
