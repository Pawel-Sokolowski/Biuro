"""Companies management page UI."""

import logging
from typing import Optional

from app.utils.ui_imports import *

from app.companies.company_form import CompanyForm
from app.db.dao_companies import (
    get_company,
    get_companies_filtered,
    get_employees_list
)

logger = logging.getLogger(__name__)


class CompaniesPage(QWidget):
    """Companies list and management page."""
    
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.stack = None
        self.list_page = None
        self.form_page = None
        self.main_layout = None
        self.search_input = None
        self.status_filter = None
        self.employee_filter = None
        self.list_widget = None

        self._build_ui()
        self._refresh_companies()

    def _build_ui(self):
        """Build companies page UI."""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL)
        self.main_layout.setSpacing(SPACING_NORMAL)

        # Title
        title = create_section_header("🏢 Klienci")
        self.main_layout.addWidget(title)

        self.list_page = QWidget()
        list_layout = QVBoxLayout(self.list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(SPACING_NORMAL)

        # Filters
        filter_row = QHBoxLayout()
        filter_row.setSpacing(SPACING_NORMAL)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Szukaj firmy..."
        self.search_input.textChanged.connect(self._refresh_companies)
        filter_row.addWidget(self.search_input, 2)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["Aktywne", "Zawieszone", "Archiwalne", "Wszystkie"])
        self.status_filter.currentIndexChanged.connect(self._refresh_companies)
        filter_row.addWidget(QLabel("Status:"), 0)
        filter_row.addWidget(self.status_filter, 1)

        self.employee_filter = QComboBox()
        self.employee_filter.addItem("Wszystkie firmy", None)
        self.employee_filter.addItem("Tylko moje firmy", self.pracownik_id)

        for emp in get_employees_list():
            if emp["id"] != self.pracownik_id:
                self.employee_filter.addItem(f"{emp['imie']} {emp['nazwisko']}", emp["id"])

        self.employee_filter.currentIndexChanged.connect(self._refresh_companies)
        filter_row.addWidget(QLabel("Pracownik:"), 0)
        filter_row.addWidget(self.employee_filter, 1)

        btn_add = create_primary_button("➕ Dodaj firmę")
        btn_add.clicked.connect(self._open_add_form)
        filter_row.addWidget(btn_add, 0)

        list_layout.addLayout(filter_row)

        # Company list
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._open_edit_form)
        list_layout.addWidget(self.list_widget)

        self.main_layout.addWidget(self.list_page)

    def _refresh_companies(self):
        """Refresh company list based on filters."""
        try:
            search = self.search_input.text().strip()
            status = self.status_filter.currentText()
            employee_id = self.employee_filter.currentData()

            rows = get_companies_filtered(search, status, employee_id)

            self.list_widget.clear()
            for r in rows:
                item_text = f"{r['nazwa']} — NIP {r.get('nip', 'N/A')} — {r.get('miasto', 'N/A')}"
                item = QListWidgetItem(item_text)
                item.setData(Qt.ItemDataRole.UserRole, r["id"])
                self.list_widget.addItem(item)
            
            logger.debug(f"Loaded {len(rows)} companies")
        except Exception as e:
            logger.error(f"Failed to refresh companies: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się załadować firm: {str(e)}")

    def _open_add_form(self):
        """Open form to add new company."""
        try:
            self.form_page = CompanyForm(self.pracownik_id, None, self)
            self.form_page.on_saved = self._on_form_saved
            self._switch_to_form()
            logger.debug("Opened add company form")
        except Exception as e:
            logger.error(f"Failed to open add form: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się otworzyć formularza: {str(e)}")

    def _open_edit_form(self, item: QListWidgetItem):
        """Open form to edit existing company."""
        try:
            fid = item.data(Qt.ItemDataRole.UserRole)
            data = get_company(fid)

            self.form_page = CompanyForm(self.pracownik_id, data, self)
            self.form_page.on_saved = self._on_form_saved
            self._switch_to_form()
            logger.debug(f"Opened edit form for company {fid}")
        except Exception as e:
            logger.error(f"Failed to open edit form: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się otworzyć formularza: {str(e)}")

    def _switch_to_form(self):
        """Switch view to form page."""
        self.list_page.hide()
        if self.form_page is not None:
            self.main_layout.addWidget(self.form_page)

    def _on_form_saved(self):
        """Handle form save."""
        self.list_page.show()
        if self.form_page:
            self.form_page.deleteLater()
            self.form_page = None
        self._refresh_companies()
        logger.info("Company form saved, refreshing list")
