from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMessageBox
)
from PyQt6.QtCore import Qt
from app.db.dao_notes import (
    get_notes_for_company,
    save_company_note,
    update_company_note,
    delete_company_note
)
from .note_edit_dialog import NoteEditDialog


class NotesWindow(QDialog):
    def __init__(self, firma_id: int, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.firma_id = firma_id
        self.pracownik_id = pracownik_id

        self.setWindowTitle("Notatki firmy")
        self.resize(900, 600)

        layout = QVBoxLayout(self)

        # tabela notatek
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Użytkownik", "Data", "Treść"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setDefaultSectionSize(56)
        self.table.itemDoubleClicked.connect(self._edit_note)
        layout.addWidget(self.table)

        # przyciski
        btns = QHBoxLayout()
        btn_add = QPushButton("Dodaj")
        btn_edit = QPushButton("Edytuj")
        btn_delete = QPushButton("Usuń")
        btn_cancel = QPushButton("Anuluj")

        btn_add.clicked.connect(self._add_note)
        btn_edit.clicked.connect(self._edit_note)
        btn_delete.clicked.connect(self._delete_note)
        btn_cancel.clicked.connect(self.reject)

        btns.addWidget(btn_add)
        btns.addWidget(btn_edit)
        btns.addWidget(btn_delete)
        btns.addStretch()
        btns.addWidget(btn_cancel)

        layout.addLayout(btns)

        self._load_notes()
        self.table.scrollToTop()

    def _load_notes(self):
        notes = get_notes_for_company(self.firma_id)

        self.table.setRowCount(0)

        for n in notes:
            row = self.table.rowCount()
            self.table.insertRow(row)

            user_item = QTableWidgetItem(n.get("autor_nazwa", ""))
            user_item.setData(
                Qt.ItemDataRole.UserRole,
                n["id"]
            )

            self.table.setItem(row, 0, user_item)
            self.table.setItem(
                row,
                1,
                QTableWidgetItem(n.get("data_utworzenia", ""))
            )

            text_item = QTableWidgetItem(
                n.get("tresc", "")[:200]
            )
            text_item.setToolTip(n.get("tresc", ""))

            self.table.setItem(row, 2, text_item)

        self.table.scrollToTop()

    def _add_note(self):
        dlg = NoteEditDialog(self.firma_id, self.pracownik_id, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load_notes()

    def _edit_note(self):
        row = self.table.currentRow()

        if row < 0:
            return

        note_id = self.table.item(
            row,
            0
        ).data(Qt.ItemDataRole.UserRole)

        notes = get_notes_for_company(self.firma_id)

        note = next(
            (
                n
                for n in notes
                if n["id"] == note_id
            ),
            None
        )

        if not note:
            return

        dlg = NoteEditDialog(
            self.firma_id,
            self.pracownik_id,
            note,
            parent=self
        )

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load_notes()

    def _delete_note(self):
        row = self.table.currentRow()

        if row < 0:
            return

        note_id = self.table.item(
            row,
            0
        ).data(Qt.ItemDataRole.UserRole)

        notes = get_notes_for_company(self.firma_id)

        note = next(
            (
                n
                for n in notes
                if n["id"] == note_id
            ),
            None
        )

        if not note:
            return

        confirm = QMessageBox.question(
            self,
            "Usuń notatkę",
            "Czy na pewno chcesz usunąć tę notatkę?"
        )

        if confirm != QMessageBox.StandardButton.Yes:
            return

        delete_company_note(note["id"])

        self._load_notes()