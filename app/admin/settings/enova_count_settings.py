"""Office-wide and per-client rules for Enova monthly bank position counts."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, QSpinBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, QMessageBox)
from app.db.dao_conn import get_conn
from app.integrations.enova.counting_rules import load_rules, save_rules
from app.security.authorization import is_administrator


class EnovaCountSettings(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        if not is_administrator(actor_id):
            raise PermissionError('Brak uprawnień do reguł importu Enova.')
        self.actor_id = actor_id
        rules = load_rules()
        layout = QVBoxLayout(self)
        text = QLabel('Enova — miesięczna liczba dokumentów księgowości')
        layout.addWidget(text)
        note = QLabel('KH: pozostałe dokumenty + pozycje WBEW ÷ dzielnik; '
                      'zaokrąglenie następuje raz, po zsumowaniu miesiąca. '
                      'Dzielnik 1 oznacza liczenie każdej pozycji bankowej w całości. '
                      'Reguła nie zmienia liczników KPIR ani ryczałtu.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.default_divisor = QSpinBox()
        self.default_divisor.setRange(1, 1000)
        self.default_divisor.setValue(rules['bank_divisor'])
        form.addRow('Globalny dzielnik pozycji WBEW', self.default_divisor)
        layout.addLayout(form)
        note = QLabel('Wyjątki dla firm: „Globalny” korzysta z powyższej wartości. '
                      'Możesz wybrać np. 10 dla konkretnej firmy. '
                      'Reguła jest stosowana, gdy jej źródło Enova ma typ KH.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Szukaj firmy lub NIP…')
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Firma', 'NIP', 'Dzielnik WBEW'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(300)
        with get_conn() as conn:
            companies = conn.execute("SELECT id,COALESCE(NULLIF(nazwa_skrocona,''),nazwa) AS nazwa,nip "
                                     'FROM firmy ORDER BY lower(nazwa)').fetchall()
        self.editors = {}
        self.table.setRowCount(len(companies))
        for row, company in enumerate(companies):
            for column, value in enumerate((company['nazwa'], company['nip'])):
                item = QTableWidgetItem(str(value or ''))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
            editor = QSpinBox()
            editor.setRange(0, 1000)
            editor.setSpecialValueText('Globalny')
            editor.setValue(rules['company_overrides'].get(str(company['id']), 0))
            self.editors[company['id']] = editor
            self.table.setCellWidget(row, 2, editor)
        layout.addWidget(self.table)
        self.search.textChanged.connect(self._filter)
        note = QLabel('Codzienny worker sprawdza tylko miesiące z etapem księgowości „Wysłane wyniki”. '
                      'Godzinę uruchomienia ustaw w Harmonogramie zadań Windows. '
                      'Zapisanie tych reguł nie uruchamia importu ani nie włącza harmonogramu.')
        note.setWordWrap(True)
        layout.addWidget(note)
        save = QPushButton('Zapisz reguły Enova')
        save.clicked.connect(self._save)
        layout.addWidget(save)

    def _filter(self, text):
        text = text.casefold().strip()
        for row in range(self.table.rowCount()):
            values = ' '.join(self.table.item(row, column).text() for column in (0, 1)).casefold()
            self.table.setRowHidden(row, text not in values)

    def _save(self):
        try:
            overrides = {str(fid): editor.value() for fid, editor in self.editors.items() if editor.value()}
            save_rules(self.default_divisor.value(), overrides, actor_id=self.actor_id)
        except Exception as exc:
            QMessageBox.warning(self, 'Enova', str(exc))
            return
        QMessageBox.information(self, 'Enova', 'Zapisano reguły. Zostaną użyte przy następnym uruchomieniu workera.')
