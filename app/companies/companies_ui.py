# app/companies/companies_ui.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox,
    QPushButton, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt

from app.companies.company_form import CompanyForm
from app.db.dao_companies import (
    get_company,
    get_companies_filtered,
    get_employees_list
)


class CompaniesPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)

        self.pracownik_id = pracownik_id

        self.stack = None
        self.list_page = None
        self.form_page = None
        self.main_layout = None

        self._build_ui()
        self._refresh_companies()

    # ============================================================
    #  BUDOWA UI
    # ============================================================

    def _build_ui(self):
        self.main_layout = QVBoxLayout(self)

        self.list_page = QWidget()
        list_layout = QVBoxLayout(self.list_page)

        # ------------------------------
        # FILTRY
        # ------------------------------
        filter_row = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Szukaj firmy...")
        self.search_input.textChanged.connect(self._refresh_companies)
        filter_row.addWidget(self.search_input)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["Aktywne", "Zawieszone", "Archiwalne", "Wszystkie"])
        self.status_filter.currentIndexChanged.connect(self._refresh_companies)
        filter_row.addWidget(self.status_filter)

        self.employee_filter = QComboBox()
        self.employee_filter.addItem("Wszystkie firmy", None)
        self.employee_filter.addItem("Tylko moje firmy", self.pracownik_id)

        for emp in get_employees_list():
            if emp["id"] != self.pracownik_id:
                self.employee_filter.addItem(f"{emp['imie']} {emp['nazwisko']}", emp["id"])

        self.employee_filter.currentIndexChanged.connect(self._refresh_companies)
        filter_row.addWidget(self.employee_filter)

        btn_add = QPushButton("Dodaj firmę")
        btn_add.clicked.connect(self._open_add_form)
        filter_row.addWidget(btn_add)

        list_layout.addLayout(filter_row)

        # ------------------------------
        # LISTA FIRM
        # ------------------------------
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._open_edit_form)
        list_layout.addWidget(self.list_widget)

        self.main_layout.addWidget(self.list_page)

    # ============================================================
    #  ODCZYT LISTY FIRM
    # ============================================================

    def _refresh_companies(self):
        search = self.search_input.text().strip()
        status = self.status_filter.currentText()
        employee_id = self.employee_filter.currentData()

        rows = get_companies_filtered(search, status, employee_id)

        self.list_widget.clear()
        for r in rows:
            item = QListWidgetItem(f"{r['nazwa']} — NIP {r['nip']} — {r['miasto']}")
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.list_widget.addItem(item)

    # ============================================================
    #  OTWARCIE FORMULARZA
    # ============================================================

    def _open_add_form(self):
        self.form_page = CompanyForm(self.pracownik_id, None, self)
        self.form_page.on_saved = self._on_form_saved
        self._switch_to_form()

    def _open_edit_form(self, item):
        fid = item.data(Qt.ItemDataRole.UserRole)
        data = get_company(fid)

        self.form_page = CompanyForm(self.pracownik_id, data, self)
        self.form_page.on_saved = self._on_form_saved
        self._switch_to_form()

    def _switch_to_form(self):
        self.list_page.hide()
        if self.form_page is not None:
            self.main_layout.addWidget(self.form_page)

    def _on_form_saved(self):
        if self.form_page is not None:
            self.form_page.setParent(None)
            self.form_page = None
        self.list_page.show()
        self._refresh_companies()
