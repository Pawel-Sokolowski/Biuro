# app/companies/company_ui.py
from typing import List, Dict, Any, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
)
from PyQt6.QtCore import Qt

from app.db.dao_companies import (
    list_companies,
    get_companies_filtered,
    delete_company,
)
from app.companies.company_form import CompanyForm


class CompanyUI(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Firmy")
        self.resize(1100, 700)

        layout = QVBoxLayout(self)

        # -------------------------
        # FILTRY
        # -------------------------
        filter_layout = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Szukaj po nazwie lub NIP...")

        self.status_filter = QComboBox()
        self.status_filter.addItems(["Wszystkie", "Aktywne", "Zawieszone", "Archiwalne"])

        self.btn_search = QPushButton("Szukaj")
        self.btn_clear = QPushButton("Wyczyść")

        filter_layout.addWidget(self.search_input)
        filter_layout.addWidget(self.status_filter)
        filter_layout.addWidget(self.btn_search)
        filter_layout.addWidget(self.btn_clear)

        layout.addLayout(filter_layout)

        # -------------------------
        # TABELA
        # -------------------------
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "ID", "Nazwa", "NIP", "Miasto", "Status",
            "Cennik", "Limit dokumentów"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout.addWidget(self.table)

        # -------------------------
        # PRZYCISKI
        # -------------------------
        btn_layout = QHBoxLayout()

        self.btn_add = QPushButton("Dodaj firmę")
        self.btn_edit = QPushButton("Edytuj firmę")
        self.btn_delete = QPushButton("Usuń firmę")
        self.btn_refresh = QPushButton("Odśwież")

        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_edit)
        btn_layout.addWidget(self.btn_delete)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_refresh)

        layout.addLayout(btn_layout)

        # -------------------------
        # SYGNAŁY
        # -------------------------
        self.btn_search.clicked.connect(self._search)
        self.btn_clear.clicked.connect(self._clear_filters)
        self.btn_add.clicked.connect(self._add_company)
        self.btn_edit.clicked.connect(self._edit_company)
        self.btn_delete.clicked.connect(self._delete_company)
        self.btn_refresh.clicked.connect(self._refresh)

        self._refresh()

    # ============================================================
    #  LOAD TABLE
    # ============================================================
    def _refresh(self):
        rows = list_companies()
        self._load_table(rows)

    def _load_table(self, rows: List[Dict[str, Any]]):
        self.table.setRowCount(0)
        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(str(r.get("id", ""))))
            self.table.setItem(row, 1, QTableWidgetItem(r.get("nazwa") or ""))
            self.table.setItem(row, 2, QTableWidgetItem(r.get("nip") or ""))
            self.table.setItem(row, 3, QTableWidgetItem(r.get("miasto") or ""))
            self.table.setItem(row, 4, QTableWidgetItem(r.get("status") or ""))
            self.table.setItem(row, 5, QTableWidgetItem(r.get("glowny_cennik") or ""))
            self.table.setItem(row, 6, QTableWidgetItem(str(r.get("limit_dokumentow") or "")))

    # ============================================================
    #  FILTERS
    # ============================================================
    def _search(self):
        search = self.search_input.text().strip()
        status = self.status_filter.currentText()
        rows = get_companies_filtered(search, status, None)
        self._load_table(rows)

    def _clear_filters(self):
        self.search_input.clear()
        self.status_filter.setCurrentIndex(0)
        self._refresh()

    # ============================================================
    #  CRUD
    # ============================================================
    def _add_company(self):
        dlg = CompanyForm(None, self)
        if dlg.exec():
            self._refresh()

    def _edit_company(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Firma", "Wybierz firmę do edycji.")
            return
        try:
            firma_id = int(self.table.item(row, 0).text())
        except Exception:
            QMessageBox.warning(self, "Firma", "Nie można odczytać ID firmy.")
            return

        dlg = CompanyForm(firma_id, self)
        if dlg.exec():
            self._refresh()

    def _delete_company(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Firma", "Wybierz firmę do usunięcia.")
            return
        try:
            firma_id = int(self.table.item(row, 0).text())
        except Exception:
            QMessageBox.warning(self, "Firma", "Nie można odczytać ID firmy.")
            return

        confirm = QMessageBox.question(
            self,
            "Usuń firmę",
            f"Czy na pewno chcesz usunąć firmę ID {firma_id}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            delete_company(firma_id)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", str(e))
