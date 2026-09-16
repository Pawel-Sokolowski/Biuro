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

    def _build_ui(self):
        self.main_layout = QVBoxLayout(self)

        self.list_page = QWidget()
        list_layout = QVBoxLayout(self.list_page)

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

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._open_edit_form)
        list_layout.addWidget(self.list_widget)

        self.main_layout.addWidget(self.list_page)

    def _refresh_companies(self):
        search = self.search_input.text().strip()
        status = self.status_filter.currentText()
        employee_id = self.employee_filter.currentData()

        rows = get_companies_filtered(search, status, employee_id)

        self.list_widget.clear()
        for r in rows:
            item = QListWidgetItem(f"{r['nazwa']} — NIP {r.get('nip','')} — {r.get('miasto','')}")
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.list_widget.addItem(item)

    def _open_add_form(self):
        """
        Otwiera formularz dodawania firmy.
        Tworzy instancję CompanyForm, podłącza callback on_saved i przełącza widok na formularz.
        """
        # Usuń istniejący formularz jeśli jest (bez zapisu)
        if self.form_page is not None:
            try:
                self.form_page.hide()
                self.form_page.setParent(None)
                self.form_page.deleteLater()
            except Exception:
                pass
            self.form_page = None

        # Tworzymy nowy formularz (nowy rekord -> company_data = None)
        self.form_page = CompanyForm(self.pracownik_id, None, self)
        # callback po zapisaniu formularza
        self.form_page.on_saved = self._on_form_saved
        self._switch_to_form()

    def _open_edit_form(self, item):
        """
        Otwiera formularz edycji firmy dla wybranego elementu listy.
        Pobiera dane firmy z DAO i tworzy CompanyForm z tymi danymi.
        """
        # pobierz id firmy z itemu
        fid = item.data(Qt.ItemDataRole.UserRole)
        data = get_company(fid)

        # Usuń istniejący formularz jeśli jest (zapobiega duplikatom)
        if self.form_page is not None:
            try:
                self.form_page.hide()
                self.form_page.setParent(None)
                self.form_page.deleteLater()
            except Exception:
                pass
            self.form_page = None

        # Tworzymy formularz z danymi firmy
        self.form_page = CompanyForm(self.pracownik_id, data, self)
        self.form_page.on_saved = self._on_form_saved
        self._switch_to_form()

    def _switch_to_form(self):
        """
        Przełącza widok z listy firm na formularz.
        Ukrywa list_page i dodaje form_page do layoutu tylko raz.
        """
        # Ukryj listę
        if self.list_page is not None:
            self.list_page.hide()

        # Jeśli formularz już dodany do layoutu, tylko pokaż go
        if self.form_page is None:
            return

        # Sprawdź czy form_page już jest w layoutcie (unikamy wielokrotnego dodawania)
        already_added = False
        for i in range(self.main_layout.count()):
            w = self.main_layout.itemAt(i).widget()
            if w is self.form_page:
                already_added = True
                break

        if not already_added:
            self.main_layout.addWidget(self.form_page)

        self.form_page.show()
        # Ustaw fokus na pierwszym polu formularza jeśli istnieje
        try:
            first_child = self.form_page.findChild(QLineEdit)
            if first_child:
                first_child.setFocus()
        except Exception:
            pass

    def _on_form_saved(self):
        """
        Callback wywoływany po zapisaniu formularza.
        Usuwa formularz z layoutu, odświeża listę i pokazuje list_page.
        """
        # Usuń formularz z layoutu i zniszcz widget
        if self.form_page is not None:
            try:
                # Ukryj i usuń widget z layoutu
                self.form_page.hide()
                self.form_page.setParent(None)
                self.form_page.deleteLater()
            except Exception:
                pass
            self.form_page = None

        # Pokaż listę i odśwież dane
        if self.list_page is not None:
            self.list_page.show()
        self._refresh_companies()
