# app/companies/companies_ui.py
from PyQt6.QtWidgets import QApplication, QCheckBox
from PyQt6.QtCore import QRunnable, QThreadPool, pyqtSignal, QObject, QDate, Qt
from PyQt6.QtWidgets import QDateEdit, QProgressDialog

from app.utils.ui_imports import *

from typing import Optional, Dict, Any, List, Tuple
import json
import datetime

from app.db.dao_companies import get_conn, wybierz_cennik
from app.utils.parsing import parse_int_value, normalize_identifier

# constants (single source)
from app.companies.constants import (
    COMPANY_EXTRA_COLUMNS,
    COMPANY_FIELD_ORDER,
    COMPANY_IMPORT_HEADER_MAP,
    COMPANY_STAGE_OPTIONS,
)

# helpers
from app.companies.helpers import parse_num

# Note: CEIDG import is done inside worker to avoid heavy imports on module load


# -------------------------
# Worker / Signals for CEIDG
# -------------------------
class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(Exception)
    finished = pyqtSignal()


class CEIDGWorker(QRunnable):
    """
    QRunnable worker that fetches CEIDG data in a background thread and emits signals.
    """

    def __init__(self, nip: str):
        super().__init__()
        self.nip = nip
        self.signals = WorkerSignals()

    def run(self):
        try:
            # import inside thread in case module does heavy work on import
            from app.companies.ceidg_api import search_ceidg_by_nip
            res = search_ceidg_by_nip(self.nip)
            self.signals.result.emit(res)
        except Exception as e:
            self.signals.error.emit(e)
        finally:
            self.signals.finished.emit()


# =====================================================================
# CompaniesPage + CompanyForm
# =====================================================================

class CompaniesPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id

        self.stack = QStackedWidget(self)
        self.list_page = QWidget()
        self.form_page = None

        self._build_list_page()
        self.stack.addWidget(self.list_page)

        layout = QVBoxLayout(self)
        layout.addWidget(self.stack)

        # domyślnie pokazuj firmy przypisane do zalogowanego pracownika
        self._refresh_companies()

    # ---------------------------------------------------------
    # LISTA FIRM
    # ---------------------------------------------------------
    def _build_list_page(self):
        layout = QVBoxLayout(self.list_page)

        # FILTRY
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
        # pierwsza opcja: pokaż wszystkie firmy
        self.employee_filter.addItem("Wszystkie firmy", None)
        # druga opcja: pokaż tylko firmy przypisane do zalogowanego pracownika
        self.employee_filter.addItem("Tylko moje firmy", self.pracownik_id)
        # załaduj pozostałych pracowników (opcjonalnie)
        self._load_employees()
        self.employee_filter.currentIndexChanged.connect(self._refresh_companies)
        filter_row.addWidget(self.employee_filter)

        btn_add = QPushButton("Dodaj firmę")
        btn_add.clicked.connect(self._open_add_form)
        filter_row.addWidget(btn_add)

        layout.addLayout(filter_row)

        # LISTA
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._open_edit_form)
        layout.addWidget(self.list_widget)

    def _load_employees(self):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, imie, nazwisko FROM pracownicy WHERE aktywny = 1 ORDER BY imie, nazwisko")
        for row in cur.fetchall():
            # row may be dict or sqlite3.Row
            if isinstance(row, dict):
                eid = row.get("id")
                imie = row.get("imie")
                nazwisko = row.get("nazwisko")
            else:
                eid, imie, nazwisko = row[0], row[1], row[2]
            # unikaj duplikatu dla zalogowanego pracownika (już dodany jako 'Tylko moje firmy')
            if eid == self.pracownik_id:
                continue
            self.employee_filter.addItem(f"{imie} {nazwisko}", eid)
        conn.close()

        # ustaw domyślnie na "Tylko moje firmy"
        idx = self.employee_filter.findData(self.pracownik_id)
        if idx != -1:
            self.employee_filter.setCurrentIndex(idx)
        else:
            idx2 = self.employee_filter.findText("Tylko moje firmy")
            if idx2 != -1:
                self.employee_filter.setCurrentIndex(idx2)

    # ---------------------------------------------------------
    # ODSWIEZANIE LISTY
    # ---------------------------------------------------------
    def _refresh_companies(self):
        search = self.search_input.text().strip().lower()
        status = self.status_filter.currentText()
        employee_id = self.employee_filter.currentData()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nazwa, nip, miasto, status
            FROM firmy
            ORDER BY nazwa
        """)
        rows = cur.fetchall()
        conn.close()

        self.list_widget.clear()

        for row in rows:
            r = dict(row) if not isinstance(row, dict) else row

            if search and search not in (r.get("nazwa") or "").lower():
                continue

            if status != "Wszystkie" and (r.get("status") or "Aktywne") != status:
                continue

            # jeśli wybrano filtr pracownika (np. konkretne id), sprawdź przypisanie
            if employee_id:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM pracownik_firma WHERE firma_id = ? AND pracownik_id = ?",
                            (r["id"], employee_id))
                assigned = cur.fetchone()
                conn.close()
                if not assigned:
                    continue

            item = QListWidgetItem(f"{r.get('nazwa','')} — NIP {r.get('nip','')} — {r.get('miasto','')}")
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.list_widget.addItem(item)

    # ---------------------------------------------------------
    # FORMULARZ DODAWANIA / EDYCJI
    # ---------------------------------------------------------
    def _open_add_form(self):
        if self.form_page:
            self.stack.removeWidget(self.form_page)
            self.form_page.deleteLater()

        self.form_page = CompanyForm(self.pracownik_id, None, self)
        self.form_page.on_saved = self._on_form_saved
        self.stack.addWidget(self.form_page)
        self.stack.setCurrentWidget(self.form_page)

    def _open_edit_form(self, item):
        if self.form_page:
            self.stack.removeWidget(self.form_page)
            self.form_page.deleteLater()

        fid = item.data(Qt.ItemDataRole.UserRole)

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM firmy WHERE id = ?", (fid,))
        data = cur.fetchone()
        conn.close()

        self.form_page = CompanyForm(self.pracownik_id, dict(data) if data else None, self)
        self.form_page.on_saved = self._on_form_saved
        self.stack.addWidget(self.form_page)
        self.stack.setCurrentWidget(self.form_page)

    def _on_form_saved(self):
        self.stack.setCurrentWidget(self.list_page)
        self._refresh_companies()


# =====================================================================
#  FORMULARZ FIRMY (DODAWANIE / EDYCJA) - pełne pola zgodne z constants
# =====================================================================

class CompanyForm(QWidget):
    def __init__(self, pracownik_id: int, company_data=None, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.company_data = company_data or {}
        self.on_saved = None
        self.inputs: Dict[str, QWidget] = {}
        self._last_ceidg_raw: Optional[Dict[str, Any]] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._ceidg_in_progress = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        title = QLabel("Edytuj firmę" if self.company_data else "Dodaj firmę")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)

        group, grid = self._section("Identyfikatory")
        # NIP input + CEIDG button
        nip_widget = QWidget()
        nip_layout = QHBoxLayout(nip_widget)
        nip_layout.setContentsMargins(0, 0, 0, 0)

        nip_input = QLineEdit()
        nip_input.setText(str(self.company_data.get("nip", "")))
        nip_input.setPlaceholderText("NIP")
        self.inputs["nip"] = nip_input
        nip_layout.addWidget(self._field("NIP", nip_input))

        btn_ceidg = QPushButton("Pobierz z CEIDG")
        btn_ceidg.clicked.connect(self._on_ceidg_clicked)
        nip_layout.addWidget(btn_ceidg)
        grid.addWidget(nip_widget, 0, 0, 1, 3)

        # First row: Nazwa | Nazwa skrócona
        w_nazwa = QLineEdit()
        w_nazwa.setText(str(self.company_data.get("nazwa", "")))
        w_nazwa.setProperty("required", True)
        self.inputs["nazwa"] = w_nazwa
        grid.addWidget(self._field("Nazwa", w_nazwa), 1, 0)

        w_nazwa_sk = QLineEdit()
        w_nazwa_sk.setText(str(self.company_data.get("nazwa_skrocona", "")))
        self.inputs["nazwa_skrocona"] = w_nazwa_sk
        grid.addWidget(self._field("Nazwa skrócona", w_nazwa_sk), 1, 1)

        # Second row: NIP | REGON | PESEL
        w_nip = self.inputs["nip"]
        w_regon = QLineEdit()
        w_regon.setText(str(self.company_data.get("regon", "")))
        self.inputs["regon"] = w_regon
        grid.addWidget(self._field("REGON", w_regon), 2, 0)

        w_krs = QLineEdit()
        w_krs.setText(str(self.company_data.get("krs", "")))
        self.inputs["krs"] = w_krs
        grid.addWidget(self._field("KRS", w_krs), 2, 1)

        w_pesel = QLineEdit()
        w_pesel.setText(str(self.company_data.get("pesel", "")))
        self.inputs["pesel"] = w_pesel
        grid.addWidget(self._field("PESEL", w_pesel), 2, 2)

        content_layout.addWidget(group)

        # Adres siedziby
        group, grid = self._section("Adres siedziby")
        self._add_line_to_grid(grid, 0, 0, "Miasto", "miasto")
        self._add_line_to_grid(grid, 0, 1, "Kod pocztowy", "kod_pocztowy")
        self._add_line_to_grid(grid, 1, 0, "Ulica", "ulica")

        # Budynek / lokal (single field adres_budynek)
        w_budynek = QLineEdit()
        w_budynek.setText(str(self.company_data.get("adres_budynek", "")))
        self.inputs["adres_budynek"] = w_budynek
        grid.addWidget(self._field("Budynek / lokal", w_budynek), 1, 1)
        content_layout.addWidget(group)

        # Adresy dodatkowe
        group, grid = self._section("Adresy dodatkowe")
        self._add_line_to_grid(grid, 0, 0, "Zam. miasto", "adres_zam_miasto")
        self._add_line_to_grid(grid, 0, 1, "Zam. kod", "adres_zam_kod_pocztowy")
        self._add_line_to_grid(grid, 1, 0, "Zam. ulica", "adres_zam_ulica")
        self._add_line_to_grid(grid, 1, 1, "Koresp. miasto", "adres_koresp_miasto")
        self._add_line_to_grid(grid, 2, 0, "Koresp. kod", "adres_koresp_kod_pocztowy")
        self._add_line_to_grid(grid, 2, 1, "Koresp. ulica", "adres_koresp_ulica")
        content_layout.addWidget(group)

        # Kontakt
        group, grid = self._section("Kontakt")
        self._add_line_to_grid(grid, 0, 0, "Email", "adres_email")
        self._add_line_to_grid(grid, 0, 1, "Telefon", "nr_telefonu")
        self._add_line_to_grid(grid, 1, 0, "Osoba kontaktowa", "osoba_kontaktowa")
        content_layout.addWidget(group)

        # Dane CEIDG
        group, grid = self._section("Dane CEIDG")
        self._add_line_to_grid(grid, 0, 0, "Forma działalności", "forma_dzialalnosci")
        self._add_line_to_grid(grid, 0, 1, "Forma opodatkowania", "forma_opodatkowania")
        self._add_line_to_grid(grid, 1, 0, "Kody PKD", "kody_pkd")
        self._add_line_to_grid(grid, 1, 1, "Status VAT", "status_vat")

        # Data rozpoczęcia (QDateEdit) - wypełniana od razu po pobraniu CEIDG
        date_start = QDateEdit()
        date_start.setCalendarPopup(True)
        date_start.setDisplayFormat("yyyy-MM-dd")
        ds = self.company_data.get("data_rozpoczecia_dg")
        if ds:
            d = QDate.fromString(ds[:10], "yyyy-MM-dd")
            if d.isValid():
                date_start.setDate(d)
        self.inputs["data_rozpoczecia_dg"] = date_start
        grid.addWidget(self._field("Data rozpoczęcia", date_start), 2, 0)
        content_layout.addWidget(group)

        scroll.setWidget(content)
        layout.addWidget(scroll)

        btns = QHBoxLayout()
        btn_save = QPushButton("Zapisz")
        btn_cancel = QPushButton("Anuluj")
        btn_save.clicked.connect(self._save)
        btn_cancel.clicked.connect(self._cancel)
        btns.addWidget(btn_save)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

    # small helpers to avoid duplication
    def _section(self, title: str):
        group = QGroupBox(title)
        grid = QGridLayout()
        group.setLayout(grid)
        return group, grid

    def _field(self, label: str, widget: QWidget):
        c = QWidget()
        v = QVBoxLayout(c)
        if label:
            v.addWidget(QLabel(label))
        v.addWidget(widget)
        return c

    def _add_line_to_grid(self, grid, row, col, label, field, required=False):
        w = QLineEdit()
        w.setText(str(self.company_data.get(field, "")))
        if required:
            w.setProperty("required", True)
        self.inputs[field] = w
        grid.addWidget(self._field(label, w), row, col)

    def _on_ceidg_clicked(self):
        if self._ceidg_in_progress:
            return

        nip = (self.inputs.get("nip").text() or "").strip()
        if not nip:
            QMessageBox.warning(self, "Brak NIP", "Wpisz NIP.")
            return

        btn = self.sender()
        if btn:
            btn.setEnabled(False)
        self._ceidg_in_progress = True

        loading = QProgressDialog("Pobieram dane z CEIDG…", None, 0, 0, self)
        loading.setWindowModality(Qt.WindowModality.ApplicationModal)
        loading.setCancelButton(None)
        loading.show()

        # worker powinien wywoływać search_ceidg_by_nip(nip)
        worker = CEIDGWorker(nip)
        self._ceidg_result = None
        self._ceidg_error = None

        def on_result(res):
            self._ceidg_result = res

        def on_error(exc):
            self._ceidg_error = exc

        def on_finished():
            loading.close()
            self._ceidg_in_progress = False
            if btn:
                btn.setEnabled(True)

            if self._ceidg_error:
                print("CEIDG ERROR:", self._ceidg_error)
                QMessageBox.critical(self, "Błąd CEIDG", str(self._ceidg_error))
                return

            if not self._ceidg_result:
                QMessageBox.information(self, "Brak wyników", "Nie znaleziono firmy.")
                return

            self._last_ceidg_raw = self._ceidg_result.get("raw")
            self._fill_from_ceidg(self._ceidg_result)

            QMessageBox.information(self, "Gotowe", "Dane zostały pobrane.")
            self._ceidg_result = None

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        worker.signals.finished.connect(on_finished)
        self._thread_pool.start(worker)

    def _fill_from_ceidg(self, normalized: Dict[str, Any]):
        def set_if_empty(key, value):
            if value is None:
                return
            w = self.inputs.get(key)
            if not w:
                return
            if isinstance(w, QLineEdit):
                if not w.text().strip():
                    w.setText(str(value))
            elif isinstance(w, QComboBox):
                if not w.currentText().strip():
                    idx = w.findText(str(value))
                    if idx >= 0:
                        w.setCurrentIndex(idx)
                    else:
                        w.addItem(str(value), str(value))
                        w.setCurrentIndex(w.count() - 1)
            elif isinstance(w, QDateEdit):
                if isinstance(value, str) and len(value) >= 10:
                    d = QDate.fromString(value[:10], "yyyy-MM-dd")
                    if d.isValid():
                        w.setDate(d)

        set_if_empty("nazwa", normalized.get("nazwa"))
        set_if_empty("nazwa_skrocona", normalized.get("nazwa_skrocona"))
        set_if_empty("nip", normalized.get("nip"))
        set_if_empty("regon", normalized.get("regon"))
        set_if_empty("krs", normalized.get("krs"))
        set_if_empty("pesel", normalized.get("pesel"))
        set_if_empty("miasto", normalized.get("miasto"))
        set_if_empty("kod_pocztowy", normalized.get("kod_pocztowy"))
        set_if_empty("ulica", normalized.get("ulica"))

        # budynek + lokal -> adres_budynek
        bud = normalized.get("budynek")
        lok = normalized.get("lokal")
        if bud and lok:
            set_if_empty("adres_budynek", f"{bud}/{lok}")
        elif bud:
            set_if_empty("adres_budynek", bud)

        set_if_empty("adres_email", normalized.get("email"))
        set_if_empty("nr_telefonu", normalized.get("telefon"))
        set_if_empty("osoba_kontaktowa", normalized.get("osoba_kontaktowa"))
        set_if_empty("forma_dzialalnosci", normalized.get("forma_dzialalnosci"))
        set_if_empty("forma_opodatkowania", normalized.get("forma_opodatkowania"))
        set_if_empty("status_vat", normalized.get("status_vat"))

        # data rozpoczecia -> QDateEdit
        set_if_empty("data_rozpoczecia_dg", normalized.get("data_rozpoczecia"))

        if normalized.get("pkd"):
            set_if_empty("kody_pkd", ",".join(normalized.get("pkd")))

    def _save(self):
        if not self.inputs["nazwa"].text().strip():
            QMessageBox.warning(self, "Błąd", "Nazwa jest wymagana.")
            return

        payload = {}
        for k, w in self.inputs.items():
            if isinstance(w, QLineEdit):
                payload[k] = w.text().strip()
            elif isinstance(w, QComboBox):
                d = w.currentData()
                payload[k] = d if d not in (None, "") else w.currentText().strip()
            elif isinstance(w, QCheckBox):
                payload[k] = 1 if w.isChecked() else 0
            elif isinstance(w, QDateEdit):
                payload[k] = w.date().toString("yyyy-MM-dd")

        conn = get_conn()
        cur = conn.cursor()

        if self.company_data and self.company_data.get("id"):
            fid = self.company_data["id"]
            cur.execute("""
                UPDATE firmy SET
                    nazwa=?, nazwa_skrocona=?, nip=?, regon=?, krs=?, pesel=?,
                    miasto=?, kod_pocztowy=?, ulica=?, adres_email=?, nr_telefonu=?,
                    osoba_kontaktowa=?, forma_dzialalnosci=?, forma_opodatkowania=?,
                    kody_pkd=?, status=?, adres_budynek=?, data_rozpoczecia_dg=?
                WHERE id=?
            """, (
                payload.get("nazwa"), payload.get("nazwa_skrocona"), payload.get("nip"),
                payload.get("regon"), payload.get("krs"), payload.get("pesel"),
                payload.get("miasto"), payload.get("kod_pocztowy"), payload.get("ulica"),
                payload.get("adres_email"), payload.get("nr_telefonu"),
                payload.get("osoba_kontaktowa"), payload.get("forma_dzialalnosci"),
                payload.get("forma_opodatkowania"), payload.get("kody_pkd"),
                payload.get("status"), payload.get("adres_budynek"),
                payload.get("data_rozpoczecia_dg"), fid
            ))
        else:
            cur.execute("""
                INSERT INTO firmy (
                    nazwa, nazwa_skrocona, nip, regon, krs, pesel,
                    miasto, kod_pocztowy, ulica, adres_email, nr_telefonu,
                    osoba_kontaktowa, forma_dzialalnosci, forma_opodatkowania,
                    kody_pkd, status, adres_budynek, data_rozpoczecia_dg
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                payload.get("nazwa"), payload.get("nazwa_skrocona"), payload.get("nip"),
                payload.get("regon"), payload.get("krs"), payload.get("pesel"),
                payload.get("miasto"), payload.get("kod_pocztowy"), payload.get("ulica"),
                payload.get("adres_email"), payload.get("nr_telefonu"),
                payload.get("osoba_kontaktowa"), payload.get("forma_dzialalnosci"),
                payload.get("forma_opodatkowania"), payload.get("kody_pkd"),
                payload.get("status"), payload.get("adres_budynek"),
                payload.get("data_rozpoczecia_dg")
            ))
            fid = cur.lastrowid
            cur.execute("INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id) VALUES (?, ?, NULL)",
                        (self.pracownik_id, fid))

        conn.commit()
        conn.close()

        if self.on_saved:
            self.on_saved()

    def _cancel(self):
        if self.on_saved:
            self.on_saved()
