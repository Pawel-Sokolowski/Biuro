import calendar
from datetime import datetime
from PyQt6.QtCore import QDateTime, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QVBoxLayout, QWidget,
)

from app.db.dao_timers import (
    DEPARTMENTS, SOURCE_IMPORT, add_extra_service_to_catalogue, add_manual_time, delete_company_timers,
    list_company_timers, list_extra_service_catalogue, list_employee_timers, update_company_timer,
)

from app.monthly_view.month_picker import MonthPicker
from app.utils.months import default_month_for_employee
from app.utils.table_sorting import SortableItem, apply_header_sort, enable_header_sort

_DEPARTMENT_NAMES = dict(DEPARTMENTS)


def format_duration(seconds: int) -> str:
    hours, rest = divmod(max(0, int(seconds)), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}"


class EditTimerRecordDialog(QDialog):
    """Zmiana działu, opisu i czasu jednego rekordu czasomierza."""

    def __init__(self, entry, parent=None, actor_id=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.pause_seconds = int(entry.get("pauza_sekundy") or 0)
        self.setWindowTitle("Edytuj rekord czasu")
        form = QFormLayout(self)
        self.month = MonthPicker(entry["miesiac"])
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
        self.seconds = QSpinBox()
        self.seconds.setRange(0,59)
        self.seconds.setValue(seconds % 60)
        time_row = QHBoxLayout()
        time_row.addWidget(self.hours)
        time_row.addWidget(QLabel("godz."))
        time_row.addWidget(self.minutes)
        time_row.addWidget(QLabel("min"))
        time_row.addWidget(self.seconds)
        time_row.addWidget(QLabel("sek."))
        form.addRow("Dział / czynność:", self.department)
        self.activity_label = QLabel("Czynność dodatkowa:")
        form.addRow(self.activity_label, self._activity_row)
        form.addRow("Opis:", self.description)
        form.addRow("Miesiąc rozliczeniowy:", self.month)
        self.time_mode = QComboBox()
        self.time_mode.addItem("Od–do", "range")
        self.time_mode.addItem("Czas trwania", "duration")
        self.started_at = QDateTimeEdit()
        self.ended_at = QDateTimeEdit()
        for field in (self.started_at, self.ended_at):
            field.setCalendarPopup(True)
            field.setDisplayFormat("dd.MM.yyyy HH:mm:ss")
        valid_range = False
        try:
            start = datetime.fromisoformat(str(entry.get('start') or ''))
            end = datetime.fromisoformat(str(entry.get('koniec') or ''))
            valid_range = end > start and int((end-start).total_seconds())-self.pause_seconds == seconds
        except ValueError:
            start = end = datetime.now().replace(microsecond=0)
        self.started_at.setDateTime(QDateTime(start))
        self.ended_at.setDateTime(QDateTime(end))
        self.time_mode.setCurrentIndex(0 if valid_range else 1)
        form.addRow("Sposób podania czasu:", self.time_mode)
        form.addRow("Od:", self.started_at)
        form.addRow("Do:", self.ended_at)
        self.range_duration = QLabel()
        form.addRow("Czas pracy:", self.range_duration)
        form.addRow("Czas trwania:", time_row)
        self.time_mode.currentIndexChanged.connect(self._time_mode_changed)
        self.started_at.dateTimeChanged.connect(self._range_changed)
        self.ended_at.dateTimeChanged.connect(self._range_changed)
        self.hours.valueChanged.connect(lambda: self.time_mode.setCurrentIndex(1))
        self.minutes.valueChanged.connect(lambda: self.time_mode.setCurrentIndex(1))
        self.seconds.valueChanged.connect(lambda: self.time_mode.setCurrentIndex(1))
        self._time_mode_changed()
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

    def _time_mode_changed(self):
        use_range = self.time_mode.currentData() == 'range'
        self.started_at.setEnabled(use_range)
        self.ended_at.setEnabled(use_range)
        self.hours.setEnabled(not use_range)
        self.minutes.setEnabled(not use_range)
        self.seconds.setEnabled(not use_range)
        self._range_changed()

    def _range_seconds(self):
        start,end = self.started_at.dateTime().toPyDateTime(),self.ended_at.dateTime().toPyDateTime()
        return int((end-start).total_seconds()) - self.pause_seconds

    def _range_changed(self):
        seconds = self._range_seconds()
        text = format_duration(seconds) if seconds >= 0 else 'Nieprawidłowy przedział'
        if self.pause_seconds:
            text += f' (po odjęciu przerwy: {format_duration(self.pause_seconds)})'
        self.range_duration.setText(text)

    def accept(self):
        if self.time_mode.currentData() == 'range' and self._range_seconds() <= 0:
            QMessageBox.warning(self, 'Czas pracy', 'Koniec musi być późniejszy niż początek i uwzględniać przerwę.')
            return
        super().accept()

    def values(self):
        seconds = self._range_seconds() if self.time_mode.currentData() == 'range' else self.hours.value()*3600+self.minutes.value()*60+self.seconds.value()
        return self.department.currentData(), self.description.text().strip(), seconds

    def timestamps(self):
        if self.time_mode.currentData() != 'range':
            return {}
        return {'started_at': self.started_at.dateTime().toPyDateTime().isoformat(),
                'ended_at': self.ended_at.dateTime().toPyDateTime().isoformat()}

    def activity_id(self):
        return self.activity.currentData() if self.department.currentData() == "dodatkowe" else None

    def activity_name(self):
        return self.activity.currentText() if self.activity_id() else ""


class AddManualTimeDialog(QDialog):
    def __init__(self, actor_id: int, employees, parent=None, *, selected_employee=None, selected_month=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.setWindowTitle("Dodaj czas ręcznie")
        self.setMinimumWidth(560)
        form = QFormLayout(self)

        self.employee = QComboBox()
        for employee_id, label in employees:
            self.employee.addItem(label, employee_id)
        index = self.employee.findData(selected_employee or actor_id)
        self.employee.setCurrentIndex(max(index, 0))

        self.month = MonthPicker(selected_month or default_month_for_employee(selected_employee or actor_id))
        self.company = QComboBox()
        self.company.addItem("— bez firmy —", None)
        from app.remote.screen_data import company_identities
        for company in company_identities(short_names=True):
            self.company.addItem(company["nazwa"], company["id"])

        self.department = QComboBox()
        for key, label in DEPARTMENTS:
            self.department.addItem(label, key)

        self.activity = QComboBox()
        self.activity.addItem("— wybierz czynność —", None)
        for item in list_extra_service_catalogue():
            self.activity.addItem(item["nazwa"], item["id"])
        self.activity_label = QLabel("Czynność dodatkowa:")

        now = QDateTime.currentDateTime()
        if selected_month:
            parsed = QDateTime.fromString(selected_month + "-01T09:00:00", "yyyy-MM-ddTHH:mm:ss")
            if parsed.isValid():
                now = parsed
        self.started_at = QDateTimeEdit(now)
        self.started_at.setCalendarPopup(True)
        self.started_at.setDisplayFormat("dd.MM.yyyy HH:mm:ss")

        self.hours = QSpinBox()
        self.hours.setRange(0, 999)
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 59)
        self.seconds = QSpinBox()
        self.seconds.setRange(0, 59)
        time_row = QHBoxLayout()
        time_row.addWidget(self.hours)
        time_row.addWidget(QLabel("godz."))
        time_row.addWidget(self.minutes)
        time_row.addWidget(QLabel("min"))
        time_row.addWidget(self.seconds)
        time_row.addWidget(QLabel("sek."))

        self.description = QLineEdit()
        self.description.setPlaceholderText("Opis (opcjonalnie)")

        form.addRow("Pracownik:", self.employee)
        form.addRow("Miesiąc rozliczeniowy:", self.month)
        form.addRow("Firma:", self.company)
        form.addRow("Dział:", self.department)
        form.addRow(self.activity_label, self.activity)
        form.addRow("Początek:", self.started_at)
        form.addRow("Czas trwania:", time_row)
        form.addRow("Opis:", self.description)

        self.department.currentIndexChanged.connect(self._department_changed)
        self._department_changed()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.employee.currentIndexChanged.connect(self._employee_changed)
    def _department_changed(self):
        visible = self.department.currentData() == "dodatkowe"
        self.activity_label.setVisible(visible)
        self.activity.setVisible(visible)
        if not visible:
            self.activity.setCurrentIndex(0)

    def _save(self):
        seconds = self.hours.value() * 3600 + self.minutes.value() * 60 + self.seconds.value()
        if seconds <= 0:
            QMessageBox.warning(self, "Dodaj czas", "Podaj czas większy od zera.")
            return
        if self.department.currentData() != "ogolne" and self.company.currentData() is None:
            QMessageBox.warning(self, "Dodaj czas", "Dla księgowości, kadr lub czynności dodatkowej wybierz firmę.")
            return
        if self.department.currentData() == "dodatkowe" and self.activity.currentData() is None:
            QMessageBox.warning(self, "Dodaj czas", "Wybierz czynność dodatkową.")
            return
        selected = self.started_at.dateTime().toPyDateTime()
        year, month = (int(part) for part in self.month.monthText().split("-", 1))
        selected = selected.replace(
            year=year,
            month=month,
            day=min(selected.day, calendar.monthrange(year, month)[1]),
        )
        try:
            add_manual_time(
                self.employee.currentData(),
                self.company.currentData(),
                self.department.currentData(),
                self.description.text().strip(),
                seconds,
                self.actor_id,
                self.activity.currentData(),
                started_at=selected.isoformat(timespec="seconds"),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Dodaj czas", f"Nie udało się dodać czasu: {exc}")
            return
        self.accept()

    def _employee_changed(self):
        self.month.setCurrentText(default_month_for_employee(self.employee.currentData()))


class TimerRecordsDialog(QDialog):
    """Rekordy firmy lub wybranego pracownika; serwer sprawdza prawo do edycji."""

    def __init__(self, firma_id: int | None, actor_id: int, parent=None, *, employee_view=False):
        super().__init__(parent)
        self.firma_id = firma_id
        self.actor_id = actor_id
        self.employee_view = employee_view
        self._rows = []
        self.setWindowTitle("Czas pracownika" if employee_view else "Czasomierz klienta")
        self.resize(900, 480)
        layout = QVBoxLayout(self)
        if employee_view:
            from app.remote.screen_data import reportable_employees
            filters = QHBoxLayout()
            self.employee = QComboBox()
            self._employees = list(reportable_employees(actor_id))
            for employee_id, label in self._employees:
                self.employee.addItem(label, employee_id)
            self.employee.setCurrentIndex(max(0, self.employee.findData(actor_id)))
            self.month = MonthPicker(default_month_for_employee(self.employee.currentData()))
            filters.addWidget(QLabel('Pracownik:'))
            filters.addWidget(self.employee, 1)
            filters.addWidget(QLabel('Miesiąc:'))
            filters.addWidget(self.month)
            layout.addLayout(filters)
            self.employee.currentIndexChanged.connect(self._employee_changed)
            self.month.monthChanged.connect(self.reload)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Miesiąc", "Początek", "Koniec", "Firma", "Pracownik", "Dział / czynność", "Opis", "Czas"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        enable_header_sort(self.table)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_selected())
        layout.addWidget(self.table)
        self.total = QLabel("")
        layout.addWidget(self.total)
        buttons = QHBoxLayout()
        self.btn_add = QPushButton("Dodaj czas ręcznie")
        self.btn_edit = QPushButton("Zmień czas / czynność")
        self.btn_delete = QPushButton("Usuń zaznaczone")
        self.btn_close = QPushButton("Zamknij")
        self.btn_add.clicked.connect(self.add_manual_time)
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_close.clicked.connect(self.accept)
        if self.employee_view:
            buttons.addWidget(self.btn_add)
        for button in (self.btn_edit, self.btn_delete):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)
        self.reload()

    def reload(self):
        self._rows = (list_employee_timers(self.employee.currentData(), self.actor_id,
                                         self.month.monthText()) if self.employee_view
                      else list_company_timers(self.firma_id))
        self.table.setRowCount(0)
        for row, entry in enumerate(self._rows):
            self.table.insertRow(row)
            values = [entry["miesiac"], entry["start"], entry["koniec"], entry["firma"], entry["pracownik"],
                      " — ".join(filter(None, [_DEPARTMENT_NAMES.get(entry["dzial"], entry["dzial"]),
                                               entry.get("usluga")])), entry["opis"],
                      format_duration(entry["sekundy"])]
            for col, value in enumerate(values):
                item = SortableItem(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, col, item)
        apply_header_sort(self.table)
        total = sum(entry["sekundy"] for entry in self._rows)
        self.total.setText(f"Rekordów: {len(self._rows)}, łączny czas: {format_duration(total)}")

    def _selected_rows(self):
        return sorted({self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                       for index in self.table.selectionModel().selectedRows()})

    def add_manual_time(self):
        if not self.employee_view:
            return
        dialog = AddManualTimeDialog(
            self.actor_id,
            self._employees,
            self,
            selected_employee=self.employee.currentData(),
            selected_month=self.month.monthText(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.employee.setCurrentIndex(max(0, self.employee.findData(dialog.employee.currentData())))
        self.month.setCurrentText(dialog.month.monthText())
        self.reload()

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
            update_company_timer(entry.get('firma_id', self.firma_id), entry["zrodlo"], entry["id"], dzial, opis, seconds,
                                 self.actor_id, usluga_id, miesiac=dialog.month.monthText(),
                                 **dialog.timestamps())
        except Exception as exc:
            QMessageBox.critical(self, "Czasomierz", f"Nie udało się zapisać zmian: {exc}")
            return
        self.reload()

    def _employee_changed(self):
        self.month.setCurrentText(default_month_for_employee(self.employee.currentData()))
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
            if self.employee_view:
                # One company scope per deletion; the server independently checks each record owner.
                for company in {self._rows[row]['firma_id'] for row in selected}:
                    company_items = [(self._rows[row]['zrodlo'],self._rows[row]['id']) for row in selected
                                     if self._rows[row]['firma_id']==company]
                    delete_company_timers(company, company_items, self.actor_id)
            else:
                delete_company_timers(self.firma_id, items, self.actor_id)
        except Exception as exc:
            QMessageBox.critical(self, "Czasomierz", f"Nie udało się usunąć rekordów: {exc}")
            return
        self.reload()
