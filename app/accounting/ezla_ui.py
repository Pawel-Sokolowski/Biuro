"""Local report preview in Sprawdzanie/Kadry; never records an Enova import."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QFileDialog, QTableWidget, QTableWidgetItem, QAbstractItemView)
from app.utils.background import TaskRunner
from app.integrations.ezla.preview import preview_report


class EzlaPreviewPanel(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.runner = TaskRunner(self)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.open_button = QPushButton('Otwórz raport e-ZLA XML z ZUS')
        self.open_button.clicked.connect(self.open_report)
        controls.addWidget(self.open_button)
        clear_button = QPushButton('Wyczyść podgląd')
        clear_button.clicked.connect(self.clear)
        controls.addWidget(clear_button)
        layout.addLayout(controls)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(['Firma', 'Pracownik z XML', 'Identyfikator (ukryty)',
            'Dokument', 'Status ZUS', 'Data wystawienia', 'Od', 'Do', 'Do sprawdzenia / import'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        note = QLabel('Podgląd pliku, bez zapisu do Enovy. Pracownik nie został jeszcze dopasowany do kartoteki Enovy. '
                      'Anulowania i korekty wymagają odrębnej obsługi. Plik pozostaje na tym komputerze; '
                      'podgląd nie zapisuje jego zawartości w bazie Biuro.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.clear()

    def clear(self):
        self.runner.invalidate()
        self.table.clearContents()
        self.table.setRowCount(0)
        self.summary.setText('Wybierz oryginalny raport XML wygenerowany przez PUE/eZUS. '
                             'Firma zostanie rozpoznana po NIP z raportu.')

    def open_report(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Raport e-ZLA z ZUS', '', 'Raport XML (*.xml)')
        if not path:
            return
        self.clear()
        self.summary.setText('Odczytywanie raportu e-ZLA…')
        self.runner.submit(lambda: preview_report(self.actor_id, path), self.render, self.failed)

    def failed(self, error):
        # Do not expose XML contents, identifiers, database errors or filenames.
        message = str(error) if isinstance(error, (ValueError, PermissionError)) else 'Nie udało się odczytać raportu e-ZLA.'
        self.summary.setText(message)

    def render(self, result):
        report, company = result['report'], result['company']
        database = result['database'] or 'Brak jednoznacznego mapowania bazy Enovy'
        self.summary.setText(f"Firma: {company['nazwa']} | NIP: {report.nip} | Baza Enovy: {database}\n"
                             f'Raport z {report.generated_at}; dokumenty: {len(report.certificates)}. '
                             'Wyłącznie podgląd — nie wykonano importu do Enovy.')
        self.table.setRowCount(len(report.certificates))
        for row, certificate in enumerate(report.certificates):
            values = [company['nazwa'], certificate.employee_name,
                      certificate.identifier_type + ': ' + certificate.masked_identifier,
                      certificate.series + ' ' + certificate.number, certificate.status,
                      certificate.issued_at, certificate.start, certificate.end, certificate.review]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column in (4, 8) and (certificate.issues or certificate.status != 'Wystawione'):
                    item.setBackground(QColor('#fef3c7'))
                    item.setForeground(QColor('#92400e'))
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
