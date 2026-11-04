from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.db.dao_invoices import list_trashed_invoices, restore_trashed_invoice


class InvoiceTrashDialog(QDialog):
    HEADERS = ("ID", "Firma", "Miesiąc", "Numer", "Numer KSeF", "Usunięto")

    def __init__(self, actor_id: int, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.setWindowTitle("Kosz faktur")
        self.resize(900, 500)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setColumnHidden(0, True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        self.btn_restore = QPushButton("Przywróć zaznaczone")
        self.btn_restore.clicked.connect(self._restore)
        buttons.addWidget(self.btn_restore)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        try:
            rows = list_trashed_invoices(self.actor_id)
        except PermissionError as exc:
            QMessageBox.warning(self, "Kosz", str(exc))
            rows = []
        self.table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            values = (item["id"], item["firma"] or "", item["miesiac"] or "", item["numer"] or "",
                      item["ksef_numer"] or "", item["deleted_at"] or "")
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(value)))

    def _restore(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Kosz", "Zaznacz faktury do przywrócenia.")
            return
        for row in rows:
            try:
                restore_trashed_invoice(int(self.table.item(row, 0).text()), self.actor_id)
            except (ValueError, PermissionError) as exc:
                QMessageBox.warning(self, "Kosz", str(exc))
        self.refresh()
