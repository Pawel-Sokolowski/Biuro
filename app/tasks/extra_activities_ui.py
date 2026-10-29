"""Ewidencja czynności dodatkowych powiązanych z zadaniami klientów."""
from __future__ import annotations

import logging
from datetime import date

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from app.db.dao_global_settings import get_extra_services
from app.db import dao_services
from app.db.dao_tasks import TASK_STATUSES, list_tasks, set_tasks_status
from app.monthly_view.month_picker import MonthPicker
from app.security.authorization import is_administrator

logger = logging.getLogger(__name__)
STATES = {"pending": "Do wykonania", "done": "Wykonane", "cancelled": "Anulowane", "unlinked": "Bez zadania"}


class TaskInputs(QWidget):
    def __init__(self, actor_id, employees, title="", parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.title = QLineEdit(title)
        self.description = QTextEdit()
        self.description.setMaximumHeight(85)
        self.status = QComboBox()
        self.status.addItems(TASK_STATUSES)
        self.due = QLineEdit()
        self.due.setPlaceholderText("RRRR-MM-DD (opcjonalnie)")
        self.employee = QComboBox()
        self.employee.addItem("Nieprzypisane", None)
        admin = is_administrator(actor_id)
        for employee in employees:
            if admin or employee["id"] == actor_id:
                label = f"{employee.get('imie', '')} {employee.get('nazwisko', '')}".strip()
                self.employee.addItem(label, employee["id"])
        self.employee.setCurrentIndex(max(0, self.employee.findData(actor_id)))
        for label, widget in (("Tytuł zadania:", self.title), ("Opis:", self.description),
                              ("Status:", self.status), ("Termin:", self.due), ("Pracownik:", self.employee)):
            form.addRow(label, widget)


class ExtraActivityDialog(QDialog):
    def __init__(self, actor_id, companies, employees, month, company_id=None, parent=None, activity=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.activity = activity
        self.setWindowTitle("Zadanie do czynności" if activity else "Dodaj czynność dodatkową i zadanie")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        self.company = QComboBox()
        for company in companies:
            self.company.addItem(company.get("nazwa_skrocona") or company["nazwa"], company["id"])
        if company_id is not None:
            self.company.setCurrentIndex(max(0, self.company.findData(company_id)))
        self.month = MonthPicker(month)
        self.services = {row["id"]: dict(row) for row in get_extra_services()} if not activity else {}
        self.service = QComboBox()
        for row in self.services.values():
            self.service.addItem(row["nazwa"], row["id"])
        self.quantity = QDoubleSpinBox()
        self.quantity.setDecimals(3)
        self.quantity.setRange(0.001, 1_000_000)
        self.quantity.setValue(1)
        self.unit = QComboBox()
        self.unit.addItems(["szt.", "godz.", "mies."])
        self.unit.setEditable(True)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 100_000_000)
        self.price.setDecimals(2)
        self.price.setSuffix(" PLN")
        self.billable = QCheckBox("Uwzględnij na fakturze po wykonaniu")
        self.billable.setChecked(True)
        if not activity:
            form = QFormLayout()
            for label, widget in (("Klient:", self.company), ("Miesiąc:", self.month), ("Czynność z cennika:", self.service),
                                  ("Ilość:", self.quantity), ("Jednostka:", self.unit), ("Cena netto za jednostkę:", self.price)):
                form.addRow(label, widget)
            form.addRow(self.billable)
            layout.addLayout(form)
        else:
            layout.addWidget(QLabel(f"{activity['firma_nazwa']} — {activity['nazwa']} ({activity['miesiac']})"))
        self.task_inputs = TaskInputs(actor_id, employees, activity["nazwa"] if activity else "", self)
        layout.addWidget(self.task_inputs)
        self._title_edited = bool(activity)
        self.task_inputs.title.textEdited.connect(self._title_changed)
        self.service.currentIndexChanged.connect(self._service_changed)
        self._service_changed()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(activity or (companies and self.services)))
        layout.addWidget(buttons)
        if not activity and not self.services:
            layout.addWidget(QLabel("Dodaj najpierw usługę w Ustawienia → Usługi dodatkowe."))

    def _title_changed(self):
        self._title_edited = True

    def _service_changed(self):
        service = self.services.get(self.service.currentData())
        if service:
            self.price.setValue(float(service["cena_netto"]))
            if not self._title_edited:
                self.task_inputs.title.setText(service["nazwa"])

    def _save(self):
        fields = self.task_inputs
        try:
            if self.activity:
                dao_services.create_task_for_extra_activity(
                    self.activity["id"], self.actor_id, task_title=fields.title.text(),
                    pracownik_id=fields.employee.currentData(), status=fields.status.currentText(),
                    termin=fields.due.text().strip() or None, opis=fields.description.toPlainText(),
                )
            else:
                dao_services.create_extra_activity(
                    self.company.currentData(), self.month.currentText(), self.service.currentData(),
                    self.quantity.value(), self.price.value(), self.billable.isChecked(), self.unit.currentText(),
                    actor_id=self.actor_id, task_title=fields.title.text(),
                    task_description=fields.description.toPlainText(), task_employee_id=fields.employee.currentData(),
                    task_due=fields.due.text().strip() or None, task_status=fields.status.currentText(),
                )
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "Czynności dodatkowe", str(exc))
            return
        except Exception:
            logger.exception("Błąd zapisu czynności dodatkowej z zadaniem")
            QMessageBox.warning(self, "Czynności dodatkowe", "Nie udało się zapisać czynności. Szczegóły w logach.")
            return
        self.accept()


class ExtraActivitiesPage(QWidget):
    open_task = pyqtSignal(int)
    activities_changed = pyqtSignal()

    def __init__(self, actor_id, companies, employees, parent=None):
        super().__init__(parent)
        self.actor_id, self.companies, self.employees = actor_id, companies, employees
        self.rows = []
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.company_filter = QComboBox()
        self.company_filter.addItem("Wszyscy klienci", None)
        for company in companies:
            self.company_filter.addItem(company.get("nazwa_skrocona") or company["nazwa"], company["id"])
        self.month = MonthPicker(date.today().strftime("%Y-%m"))
        self.state_filter = QComboBox()
        self.state_filter.addItem("Wszystkie", None)
        for state, label in STATES.items():
            self.state_filter.addItem(label, state)
        for label, widget in (("Klient:", self.company_filter), ("Miesiąc:", self.month), ("Stan czynności:", self.state_filter)):
            filters.addWidget(QLabel(label))
            filters.addWidget(widget)
        filters.addStretch()
        layout.addLayout(filters)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["Klient", "Miesiąc", "Czynność dodatkowa", "Ilość", "Stan", "Zadanie", "Status zadania", "Pracownik", "Fakturować po wykonaniu"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda *_: self._open_related_task())
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        for label, callback in (("Dodaj czynność i zadanie", self._add), ("Utwórz zadanie", self._create_task),
                                ("Powiąż z zadaniem", self._link), ("Otwórz zadanie", self._open_related_task),
                                ("Oznacz jako wykonane", self._complete), ("Odśwież", self.refresh)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        hint = QLabel("Stan czynności wynika ze statusu zadania. Wpisy historyczne bez powiązania mają stan „Bez zadania”.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.company_filter.currentIndexChanged.connect(self.refresh)
        self.state_filter.currentIndexChanged.connect(self.refresh)
        self.month.monthChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        try:
            self.rows = dao_services.list_extra_activities(
                self.actor_id, firma_id=self.company_filter.currentData(), miesiac=self.month.currentText(),
                state=self.state_filter.currentData(),
            )
        except (PermissionError, ValueError) as exc:
            QMessageBox.warning(self, "Czynności dodatkowe", str(exc))
            return
        except Exception:
            logger.exception("Błąd odczytu czynności dodatkowych")
            QMessageBox.warning(self, "Czynności dodatkowe", "Nie udało się odczytać czynności. Szczegóły w logach.")
            return
        self.table.setRowCount(len(self.rows))
        for row_number, row in enumerate(self.rows):
            values = [row["firma_nazwa"], row["miesiac"], row["nazwa"], f"{row['ilosc']:g} {row['jednostka']}",
                      STATES[row["state"]], row.get("task_title") or "—", row.get("task_status") or "—",
                      row.get("task_employee_name") or "—", "Tak" if row["fakturowac"] else "Nie"]
            for col, value in enumerate(values):
                self.table.setItem(row_number, col, QTableWidgetItem(str(value)))

    def _selected(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.rows):
            QMessageBox.information(self, "Czynności dodatkowe", "Wybierz czynność na liście.")
            return None
        return self.rows[row]

    def _changed(self):
        self.refresh()
        self.activities_changed.emit()

    def _add(self):
        dialog = ExtraActivityDialog(self.actor_id, self.companies, self.employees, self.month.currentText(),
                                     self.company_filter.currentData(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._changed()

    def _create_task(self):
        row = self._selected()
        if row is None:
            return
        if row.get("zadanie_id"):
            QMessageBox.information(self, "Zadanie", "Czynność ma już powiązane zadanie. Użyj „Otwórz zadanie”.")
            return
        dialog = ExtraActivityDialog(self.actor_id, self.companies, self.employees, row["miesiac"],
                                     row["firma_id"], self, activity=row)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._changed()

    def _link(self):
        row = self._selected()
        if row is None:
            return
        tasks = list_tasks(firma_id=row["firma_id"], viewer_id=self.actor_id)
        if not tasks:
            QMessageBox.information(self, "Zadanie", "Brak dostępnych zadań tego klienta. Użyj „Utwórz zadanie”.")
            return
        choices = {f"#{task['id']} — {task['tytul']} ({task['status']})": task["id"] for task in tasks}
        label, accepted = QInputDialog.getItem(self, "Powiąż z zadaniem", "Zadanie tego klienta:", list(choices), 0, False)
        if not accepted:
            return
        try:
            dao_services.link_extra_activity_task(row["id"], choices[label], self.actor_id)
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "Zadanie", str(exc))
            return
        self._changed()

    def _open_related_task(self):
        row = self._selected()
        if row is None:
            return
        if not row.get("zadanie_id"):
            QMessageBox.information(self, "Zadanie", "Najpierw utwórz lub powiąż zadanie tej czynności.")
            return
        self.open_task.emit(row["zadanie_id"])

    def _complete(self):
        row = self._selected()
        if row is None:
            return
        if not row.get("zadanie_id"):
            QMessageBox.information(self, "Zadanie", "Aby oznaczyć wykonanie, najpierw utwórz lub powiąż zadanie.")
            return
        try:
            if not set_tasks_status([row["zadanie_id"]], "Zakończone", actor_id=self.actor_id):
                raise PermissionError("Brak uprawnień do zmiany tego zadania.")
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "Zadanie", str(exc))
            return
        self._changed()
