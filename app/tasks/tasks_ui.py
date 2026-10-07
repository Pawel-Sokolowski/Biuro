from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.db.dao_companies import list_companies
from app.db.dao_employees import list_employees
from app.db.dao_tasks import TASK_STATUSES, delete_task, list_tasks, save_task


class TasksPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self._build_ui()
        self._load_reference_data()
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Zadania</b>"))

        filters = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItem("Wszystkie", None)
        for status in TASK_STATUSES:
            self.status_filter.addItem(status, status)
        self.status_filter.currentIndexChanged.connect(self._refresh)

        self.assignee_filter = QComboBox()
        self.assignee_filter.addItem("Wszyscy", None)
        self.assignee_filter.currentIndexChanged.connect(self._refresh)

        filters.addWidget(QLabel("Status:"))
        filters.addWidget(self.status_filter)
        filters.addWidget(QLabel("Pracownik:"))
        filters.addWidget(self.assignee_filter)
        filters.addStretch()
        layout.addLayout(filters)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Tytuł", "Firma", "Pracownik", "Status", "Termin", "Opis", "ID"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        actions = QHBoxLayout()
        btn_add = QPushButton("Nowe zadanie")
        btn_edit = QPushButton("Edytuj")
        btn_delete = QPushButton("Usuń")
        btn_add.clicked.connect(self._new_task)
        btn_edit.clicked.connect(self._edit_selected)
        btn_delete.clicked.connect(self._delete_selected)
        actions.addWidget(btn_add)
        actions.addWidget(btn_edit)
        actions.addWidget(btn_delete)
        actions.addStretch()
        layout.addLayout(actions)

    def _load_reference_data(self):
        self.companies = list_companies()
        self.employees = list_employees()
        for employee in self.employees:
            label = f"{employee.get('imie', '')} {employee.get('nazwisko', '')}".strip()
            self.assignee_filter.addItem(label, employee["id"])

    def _refresh(self):
        status = self.status_filter.currentData()
        employee_id = self.assignee_filter.currentData()
        rows = list_tasks(status=status, pracownik_id=employee_id)
        self.table.setRowCount(0)
        for task in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                task.get("tytul", ""),
                task.get("firma_nazwa") or "—",
                task.get("pracownik_nazwa") or "—",
                task.get("status", ""),
                task.get("termin") or "—",
                task.get("opis", ""),
                str(task.get("id", "")),
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))

    def _selected_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 6)
        return int(item.text()) if item else None

    def _new_task(self):
        TaskDialog(self, self.pracownik_id, self.companies, self.employees).exec()
        self._refresh()

    def _edit_selected(self):
        task_id = self._selected_id()
        if not task_id:
            return
        task = next((t for t in list_tasks() if t["id"] == task_id), None)
        if task:
            TaskDialog(self, self.pracownik_id, self.companies, self.employees, task).exec()
            self._refresh()

    def _delete_selected(self):
        task_id = self._selected_id()
        if not task_id:
            return
        if QMessageBox.question(self, "Usuń zadanie", "Usunąć wybrane zadanie?") != QMessageBox.StandardButton.Yes:
            return
        delete_task(task_id)
        self._refresh()


class TaskDialog(QDialog):
    def __init__(self, parent, created_by, companies, employees, task=None):
        super().__init__(parent)
        self.setWindowTitle("Zadanie")
        self.setMinimumWidth(520)
        self.created_by = created_by
        self.task = task or {}

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(self.task.get("tytul", ""))
        self.description = QTextEdit(self.task.get("opis", ""))
        self.status = QComboBox()
        self.status.addItems(TASK_STATUSES)
        self.status.setCurrentText(self.task.get("status", "Nowe"))
        self.due = QLineEdit(self.task.get("termin", ""))
        self.due.setPlaceholderText("YYYY-MM-DD")
        self.company = QComboBox()
        self.company.addItem("Brak przypisania", None)
        for company in companies:
            self.company.addItem(company["nazwa"], company["id"])
        self.company.setCurrentIndex(max(0, self.company.findData(self.task.get("firma_id"))))
        self.employee = QComboBox()
        self.employee.addItem("Brak przypisania", None)
        for employee in employees:
            name = f"{employee.get('imie', '')} {employee.get('nazwisko', '')}".strip()
            self.employee.addItem(name, employee["id"])
        self.employee.setCurrentIndex(max(0, self.employee.findData(self.task.get("pracownik_id"))))

        form.addRow("Tytuł:", self.title)
        form.addRow("Opis:", self.description)
        form.addRow("Status:", self.status)
        form.addRow("Termin:", self.due)
        form.addRow("Firma:", self.company)
        form.addRow("Pracownik:", self.employee)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        save = QPushButton("Zapisz")
        cancel = QPushButton("Anuluj")
        save.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _save(self):
        try:
            save_task(
                self.title.text(),
                self.description.toPlainText(),
                self.status.currentText(),
                self.due.text().strip() or None,
                self.company.currentData(),
                self.employee.currentData(),
                self.created_by,
                self.task.get("id"),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Błąd", str(exc))
            return
        self.accept()
