from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.widgets.flow_layout import FlowLayout
from app.companies.company_names import company_display_name
from app.db.dao_companies import list_companies
from app.db.dao_employees import list_employees
from app.db.dao_tasks import TASK_STATUSES, delete_tasks, list_tasks, save_task, set_tasks_status
from app.utils.background import TaskRunner

logger = logging.getLogger(__name__)


class TasksPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None, *, defer_load=False):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self._defer_load = defer_load
        self._references_loaded = False
        self._references_loading = False
        self._task_scope = None
        self._task_rows = None
        self._task_reads = TaskRunner(self)
        self._reference_reads = TaskRunner(self)
        self._build_ui()
        self.extra_activities = None
        self._activity_host = QWidget(self.tabs)
        QVBoxLayout(self._activity_host)
        self._activity_status = QLabel("Wczytywanie firm i pracowników…")
        self._activity_host.layout().addWidget(self._activity_status)
        self.tabs.addTab(self._activity_host, "Czynności dodatkowe")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        if defer_load:
            self._new_button.setEnabled(False)
            self._edit_button.setEnabled(False)
        else:
            self._populate_reference_data(self._read_reference_data())
            self._create_activity_page()
            self._refresh()

    def _create_activity_page(self):
        from app.tasks.extra_activities_ui import ExtraActivitiesPage
        self.extra_activities = ExtraActivitiesPage(
            self.pracownik_id, self.companies, self.employees, self._activity_host,
            defer_load=self._defer_load,
        )
        self.extra_activities.open_task.connect(self._open_task)
        if not self._defer_load:
            self.extra_activities.activities_changed.connect(self._refresh_tasks)
        self._activity_status.hide()
        self._activity_host.layout().addWidget(self.extra_activities)

    def _on_tab_changed(self, *_):
        if self._defer_load:
            self.refresh_async()
        elif self.tabs.currentWidget() is self._activity_host:
            self.extra_activities.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)
        tasks_tab = QWidget(self.tabs)
        self.tabs.addTab(tasks_tab, "Zadania")
        layout = QVBoxLayout(tasks_tab)
        layout.addWidget(QLabel("<b>Zadania</b>"))

        filters = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItem("Nowe i w toku", ("Nowe", "W toku"))
        self.status_filter.addItem("Wszystkie", None)
        for status in TASK_STATUSES:
            self.status_filter.addItem(status, status)
        self.status_filter.currentIndexChanged.connect(self._refresh_tasks)

        self.assignee_filter = QComboBox()
        self.assignee_filter.addItem("Wszyscy", None)
        self.assignee_filter.addItem("Moje zadania", self.pracownik_id)
        self.assignee_filter.setCurrentIndex(1)
        self.assignee_filter.currentIndexChanged.connect(self._refresh_tasks)

        filters.addWidget(QLabel("Status:"))
        filters.addWidget(self.status_filter)
        filters.addWidget(QLabel("Pracownik:"))
        filters.addWidget(self.assignee_filter)
        filters.addStretch()
        layout.addLayout(filters)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Tytuł", "Firma", "Pracownik", "Status", "Termin", "Opis"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(100)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((320, 180, 160, 120, 120, 520)):
            self.table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setMinimumSectionSize(44)
        layout.addWidget(self.table)
        self._load_status = QLabel()
        self._load_status.hide()
        layout.addWidget(self._load_status)

        actions = FlowLayout()
        btn_add = QPushButton("Nowe zadanie")
        btn_edit = QPushButton("Edytuj")
        self._new_button, self._edit_button = btn_add, btn_edit
        btn_delete = QPushButton("Usuń")
        btn_add.clicked.connect(self._new_task)
        btn_edit.clicked.connect(self._edit_selected)
        btn_delete.clicked.connect(self._delete_selected)
        actions.addWidget(btn_add)
        actions.addWidget(btn_edit)
        actions.addWidget(btn_delete)
        actions.addWidget(QLabel("Zaznaczone:"))
        self.bulk_status = QComboBox()
        self.bulk_status.addItems(TASK_STATUSES)
        btn_bulk_status = QPushButton("Zmień status")
        btn_bulk_status.clicked.connect(self._bulk_set_status)
        btn_select_all = QPushButton("Zaznacz wszystkie")
        btn_select_all.clicked.connect(self.table.selectAll)
        actions.addWidget(self.bulk_status)
        actions.addWidget(btn_bulk_status)
        actions.addWidget(btn_select_all)
        layout.addLayout(actions)

    def _read_reference_data(self):
        return list_companies(self.pracownik_id), list_employees()

    def _populate_reference_data(self, result):
        self.companies, self.employees = result
        selected = self.assignee_filter.currentData()
        blocked = self.assignee_filter.blockSignals(True)
        try:
            self.assignee_filter.clear()
            self.assignee_filter.addItem("Wszyscy", None)
            for employee in self.employees:
                label = f"{employee.get('imie', '')} {employee.get('nazwisko', '')}".strip()
                self.assignee_filter.addItem(label, employee["id"])
            if self.assignee_filter.findData(self.pracownik_id) < 0:
                self.assignee_filter.addItem("Moje zadania", self.pracownik_id)
            self.assignee_filter.setCurrentIndex(max(0, self.assignee_filter.findData(selected)))
        finally:
            self.assignee_filter.blockSignals(blocked)
        self._references_loaded = True
        self._references_loading = False
        self._new_button.setEnabled(True)
        self._edit_button.setEnabled(True)
        if self._defer_load and self.tabs.currentWidget() is self._activity_host:
            self._refresh_activities()

    def _load_reference_data_async(self):
        if self._references_loaded or self._references_loading:
            return
        self._references_loading = True
        self._new_button.setToolTip("Wczytywanie firm i pracowników…")
        self._edit_button.setToolTip(self._new_button.toolTip())
        self._activity_status.setText(self._new_button.toolTip())

        def loaded(result):
            self._new_button.setToolTip("")
            self._edit_button.setToolTip("")
            self._populate_reference_data(result)

        def failed(exc):
            self._references_loading = False
            message = "Nie udało się wczytać firm i pracowników. Otwórz zakładkę ponownie."
            self._new_button.setToolTip(message)
            self._edit_button.setToolTip(message)
            self._activity_status.setText(message)
            logger.warning("Nie można wczytać filtrów zadań: %s", exc)

        self._reference_reads.submit(self._read_reference_data, loaded, failed)

    def _refresh_activities(self):
        if not self._references_loaded:
            return
        if self.extra_activities is None:
            self._create_activity_page()
        self.extra_activities.refresh()

    def refresh_async(self):
        if self.tabs.currentWidget() is self._activity_host:
            self._refresh_activities()
        else:
            self._refresh_tasks_async()
        self._load_reference_data_async()

    def _refresh_tasks(self):
        if self._defer_load:
            self._refresh_tasks_async()
        else:
            self._refresh()

    def _refresh(self):
        status = self.status_filter.currentData()
        employee_id = self.assignee_filter.currentData()
        self._task_reads.invalidate()
        rows = list_tasks(status=status, pracownik_id=employee_id, viewer_id=self.pracownik_id)
        self._task_scope = status, employee_id
        self._render_tasks(rows)

    def _refresh_tasks_async(self):
        scope = self.status_filter.currentData(), self.assignee_filter.currentData()
        actor = self.pracownik_id
        if scope != self._task_scope:
            self._task_scope = scope
            self._task_rows = None
            self.table.setRowCount(0)
            self._load_status.setText("Wczytywanie zadań…")
            self._load_status.show()

        def failed(exc):
            self._load_status.setText("Nie udało się odświeżyć zadań.")
            self._load_status.show()
            logger.warning("Nie można wczytać zadań: %s", exc)

        self._task_reads.submit(
            lambda: list_tasks(status=scope[0], pracownik_id=scope[1], viewer_id=actor),
            self._render_tasks, failed, key=scope,
        )

    def _render_tasks(self, rows):
        self._load_status.hide()
        if rows == self._task_rows:
            return
        self._task_rows = rows
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for row, task in enumerate(rows):
            values = [
                task.get("tytul", ""),
                task.get("firma_nazwa") or "—",
                task.get("pracownik_nazwa") or "—",
                task.get("status", ""),
                task.get("termin") or "—",
                task.get("opis", ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, task["id"])
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()

    def show_assignment_notification(self, task_id):
        with QSignalBlocker(self.tabs), QSignalBlocker(self.status_filter), QSignalBlocker(self.assignee_filter):
            self.tabs.setCurrentIndex(0)
            self.status_filter.setCurrentIndex(self.status_filter.findData(None))
            self.assignee_filter.setCurrentIndex(max(0, self.assignee_filter.findData(self.pracownik_id)))
        self._refresh()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == task_id:
                self.table.setCurrentCell(row, 0)
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                self.table.setFocus()
                return True
        QMessageBox.information(self, "Powiadomienie", "Zadanie nie jest już Tobie przypisane lub zostało usunięte.")
        return False

    def _selected_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selected_ids(self) -> list:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        ids = []
        for row in rows:
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) is not None:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def _bulk_set_status(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Status", "Zaznacz zadania na liście.")
            return
        status = self.bulk_status.currentText()
        try:
            changed = set_tasks_status(ids, status, actor_id=self.pracownik_id)
        except (PermissionError, ValueError) as exc:
            QMessageBox.warning(self, "Błąd", str(exc))
            return
        self._refresh_tasks()
        if changed < len(ids):
            QMessageBox.warning(
                self, "Status", f"Zmieniono {changed} z {len(ids)} zadań (reszta bez uprawnień)."
            )

    def _new_task(self):
        TaskDialog(self, self.pracownik_id, self.companies, self.employees).exec()
        self._refresh_tasks()

    def _edit_selected(self):
        task_id = self._selected_id()
        if not task_id:
            return
        self._open_task(task_id)

    def _open_task(self, task_id):
        task = next(
            (t for t in list_tasks(viewer_id=self.pracownik_id) if t["id"] == task_id),
            None,
        )
        if task:
            TaskDialog(
                self,
                self.pracownik_id,
                self.companies,
                self.employees,
                task,
            ).exec()
            if self.tabs.currentWidget() is self._activity_host:
                self.extra_activities.refresh()
            else:
                self._refresh_tasks()
        else:
            QMessageBox.warning(self, "Zadanie", "Nie masz dostępu do powiązanego zadania lub zostało ono usunięte.")

    def _delete_selected(self):
        ids = self._selected_ids()
        if not ids:
            return
        question = "Usunąć wybrane zadanie?" if len(ids) == 1 else f"Usunąć zaznaczone zadania ({len(ids)})?"
        if QMessageBox.question(self, "Usuń zadania", question) != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = delete_tasks(ids, actor_id=self.pracownik_id)
            self._refresh_tasks()
            if deleted < len(ids):
                QMessageBox.warning(
                    self, "Usuwanie", f"Usunięto {deleted} z {len(ids)} zadań (reszta bez uprawnień)."
                )
        except (PermissionError, ValueError) as exc:
            QMessageBox.warning(self, "Usuwanie zadania", str(exc))


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
            self.company.addItem(company_display_name(company), company["id"])
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
                actor_id=self.created_by,
            )
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "Błąd", str(exc))
            return
        self.accept()
