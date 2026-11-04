from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db.dao_timers import (
    DEPARTMENTS, SOURCE_IMPORT, add_extra_service_to_catalogue, delete_company_timers, list_company_timers,
    list_extra_service_catalogue, update_company_timer,
)

_DEPARTMENT_NAMES = dict(DEPARTMENTS)


def format_duration(seconds: int) -> str:
    hours, rest = divmod(max(0, int(seconds)), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}"


class EditTimerRecordDialog(QDialog):
    """Zmiana działu, opisu i czasu jednego rekordu czasomierza."""

    def __init__(self, entry, parent=None, actor_id=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.setWindowTitle("Edytuj rekord czasu")
        form = QFormLayout(self)
        self.department = QComboBox()
        for key, label in DEPARTMENTS:
            self.department.addItem(label, key)
        index = self.department.findData(entry["dzial"])
        self.department.setCurrentIndex(index if index >= 0 else self.department.count() - 1)
        self.description = QLineEdit(entry["opis"])
        self.description.setPlaceholderText("Opis (opcjonalnie)")
        self.activity = QComboBox()
        self.btn_add_activity = QPushButton("Dodaj czynność…")
        self.btn_add_activity.clicked.connect(self.add_activity)
        self._activity_row = QWidget()
        activity_layout = QHBoxLayout(self._activity_row)
        activity_layout.setContentsMargins(0, 0, 0, 0)
        activity_layout.addWidget(self.activity, 1)
        activity_layout.addWidget(self.btn_add_activity)
        self._load_activities(entry.get("usluga_id"))
        self.department.currentIndexChanged.connect(self._department_changed)
        seconds = int(entry["sekundy"])
        self.hours = QSpinBox()
        self.hours.setRange(0, 999)
        self.hours.setValue(seconds // 3600)
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 59)
        self.minutes.setValue(seconds % 3600 // 60)
        time_row = QHBoxLayout()
        time_row.addWidget(self.hours)
        time_row.addWidget(QLabel("godz."))
        time_row.addWidget(self.minutes)
        time_row.addWidget(QLabel("min"))
        form.addRow("Dział / czynność:", self.department)
        self.activity_label = QLabel("Czynność dodatkowa:")
        form.addRow(self.activity_label, self._activity_row)
        form.addRow("Opis:", self.description)
        form.addRow("Czas:", time_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._department_changed()

    def _load_activities(self, selected_id=None):
        self.activity.clear()
        for item in list_extra_service_catalogue():
            self.activity.addItem(item["nazwa"], item["id"])
        index = self.activity.findData(selected_id) if selected_id else 0
        self.activity.setCurrentIndex(max(index, 0))

    def _department_changed(self):
        extra = self.department.currentData() == "dodatkowe"
        self._activity_row.setVisible(extra)
        self.activity_label.setVisible(extra)

    def add_activity(self):
        name, ok = QInputDialog.getText(self, "Nowa czynność dodatkowa", "Nazwa czynności:")
        if not ok or not name.strip():
            return
        try:
            new_id = add_extra_service_to_catalogue(name, self.actor_id)
        except Exception as exc:
            QMessageBox.critical(self, "Czynność dodatkowa", f"Nie udało się dodać czynności: {exc}")
            return
        self._load_activities(new_id)

    def values(self):
        return (self.department.currentData(), self.description.text().strip(),
                self.hours.value() * 3600 + self.minutes.value() * 60)

    def activity_id(self):
        return self.activity.currentData() if self.department.currentData() == "dodatkowe" else None

    def activity_name(self):
        return self.activity.currentText() if self.activity_id() else ""


class TimerRecordsDialog(QDialog):
    """Rekordy czasu klienta: edycja i usuwanie dostępne dla każdego pracownika."""

    def __init__(self, firma_id: int, actor_id: int, parent=None):
        super().__init__(parent)
        self.firma_id = firma_id
        self.actor_id = actor_id
        self._rows = []
        self.setWindowTitle("Czasomierz klienta")
        self.resize(900, 480)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Miesiąc", "Początek", "Pracownik", "Dział / czynność", "Opis", "Czas"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_selected())
        layout.addWidget(self.table)
        self.total = QLabel("")
        layout.addWidget(self.total)
        buttons = QHBoxLayout()
        self.btn_edit = QPushButton("Zmień czas / czynność")
        self.btn_delete = QPushButton("Usuń zaznaczone")
        self.btn_close = QPushButton("Zamknij")
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_close.clicked.connect(self.accept)
        for button in (self.btn_edit, self.btn_delete):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)
        self.reload()

    def reload(self):
        self._rows = list_company_timers(self.firma_id)
        self.table.setRowCount(0)
        for entry in self._rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [entry["miesiac"], entry["start"], entry["pracownik"],
                      " — ".join(filter(None, [_DEPARTMENT_NAMES.get(entry["dzial"], entry["dzial"]),
                                               entry.get("usluga")])), entry["opis"],
                      format_duration(entry["sekundy"])]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
        total = sum(entry["sekundy"] for entry in self._rows)
        self.total.setText(f"Rekordów: {len(self._rows)}, łączny czas: {format_duration(total)}")

    def _selected_rows(self):
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def edit_selected(self):
        selected = self._selected_rows()
        if len(selected) != 1:
            QMessageBox.information(self, "Czasomierz", "Zaznacz jeden rekord do zmiany.")
            return
        entry = self._rows[selected[0]]
        dialog = EditTimerRecordDialog(entry, self, self.actor_id)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        dzial, opis, seconds = dialog.values()
        usluga_id = dialog.activity_id()
        if usluga_id and entry["zrodlo"] == SOURCE_IMPORT:
            opis = " — ".join(filter(None, [dialog.activity_name(), opis]))
        try:
            update_company_timer(self.firma_id, entry["zrodlo"], entry["id"], dzial, opis, seconds,
                                 self.actor_id, usluga_id)
        except Exception as exc:
            QMessageBox.critical(self, "Czasomierz", f"Nie udało się zapisać zmian: {exc}")
            return
        self.reload()

    def delete_selected(self):
        selected = self._selected_rows()
        if not selected:
            QMessageBox.information(self, "Czasomierz", "Zaznacz rekordy do usunięcia.")
            return
        items = [(self._rows[row]["zrodlo"], self._rows[row]["id"]) for row in selected]
        answer = QMessageBox.question(self, "Usuń rekordy czasu", f"Usunąć zaznaczone rekordy ({len(items)})?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_company_timers(self.firma_id, items, self.actor_id)
        except Exception as exc:
            QMessageBox.critical(self, "Czasomierz", f"Nie udało się usunąć rekordów: {exc}")
            return
        self.reload()
