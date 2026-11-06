# app/companies/company_ui.py
import logging
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
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from app.companies.company_names import company_display_name
from app.utils.background import TaskRunner
from app.db.dao_companies import (
    list_companies,
    get_companies_filtered,
    delete_company,
    set_company_status,
)
from app.companies.company_form import CompanyForm


class CompanyUI(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self._reads = TaskRunner(self)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh_async)
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

        self.table = QTableWidget(0, 14)
        self.table.setHorizontalHeaderLabels([
            "ID", "Nazwa", "NIP", "Miasto", "Status",
            "Forma opodatkowania", "VAT",
            "Księgowa", "Kadrowa",
            "Cennik", "Limit dokumentów", "PEL", "UPL", "Ostatni backup"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setColumnHidden(0, True)
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
        self.btn_restore = QPushButton("Przywróć do aktywnych")
        self.btn_refresh = QPushButton("Odśwież")
        self.btn_documents = QPushButton("Komplet dokumentów…")

        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_edit)
        btn_layout.addWidget(self.btn_delete)
        btn_layout.addWidget(self.btn_restore)
        btn_layout.addWidget(self.btn_documents)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_refresh)

        layout.addLayout(btn_layout)

        # -------------------------
        # SYGNAŁY
        # -------------------------
        self.btn_search.clicked.connect(self.refresh_async)
        self.btn_clear.clicked.connect(self._clear_filters)
        self.btn_add.clicked.connect(self._add_company)
        self.btn_edit.clicked.connect(self._edit_company)
        self.btn_delete.clicked.connect(self._delete_company)
        self.btn_restore.clicked.connect(self._restore_company)
        self.btn_refresh.clicked.connect(self.refresh_async)
        self.btn_documents.clicked.connect(self._generate_documents)
        self.status_filter.currentIndexChanged.connect(self.refresh_async)
        self.search_input.textChanged.connect(self._queue_search)

        QTimer.singleShot(0, self.refresh_async)

    # ============================================================
    #  LOAD TABLE
    # ============================================================
    def _refresh(self):
        self._search()

    @staticmethod
    def _backup_by_company() -> Dict[str, Dict[str, Any]]:
        try:
            from app.utils.backup_status import get_backup_status
            return {b['firma']: b for b in get_backup_status()['bazy'] if b.get('firma')}
        except Exception:
            logging.getLogger(__name__).exception("Nie udało się odczytać statusu backupu")
            return {}

    def _load_table(self, rows: List[Dict[str, Any]], backups=None, stored=None):
        from app.invoices.table_state import preserve_table_view
        with preserve_table_view(self.table):
            self._populate_table(rows, backups, stored)

    def _populate_table(self, rows, backups, stored):
        self.table.setRowCount(0)
        backups = self._backup_by_company() if backups is None else backups
        try:
            from app.utils.backup_status import stored_backup_dates
            stored = stored_backup_dates() if stored is None else stored
        except Exception:
            stored = {}
        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(str(r.get("id", ""))))
            name_item = QTableWidgetItem(company_display_name(r))
            name_item.setToolTip(r.get("nazwa") or "")
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, QTableWidgetItem(r.get("nip") or ""))
            self.table.setItem(row, 3, QTableWidgetItem(r.get("miasto") or ""))
            self.table.setItem(row, 4, QTableWidgetItem(r.get("status") or ""))
            self.table.setItem(row, 5, QTableWidgetItem(r.get("forma_opodatkowania") or ""))
            self.table.setItem(row, 6, QTableWidgetItem("czynny" if r.get("vat_czynny") else "—"))
            self.table.setItem(row, 7, QTableWidgetItem(r.get("ksiegowa") or ""))
            self.table.setItem(row, 8, QTableWidgetItem(r.get("kadrowa") or ""))
            self.table.setItem(row, 9, QTableWidgetItem(r.get("glowny_cennik") or ""))
            self.table.setItem(row, 10, QTableWidgetItem(str(r.get("limit_dokumentow") or "")))
            self.table.setItem(row, 11, QTableWidgetItem(r.get("pel") or ""))
            self.table.setItem(row, 12, QTableWidgetItem(r.get("upl") or ""))
            self.table.setItem(row, 13, self._backup_item(backups.get(r.get("nazwa")), stored.get(r.get("id"))))
            if r.get("uslugi_zawieszone"):
                for col in range(self.table.columnCount()):
                    item = self.table.item(row, col)
                    if item is not None:
                        item.setBackground(QColor("#f4a6a6"))
                        item.setForeground(QColor("#000000"))

    @staticmethod
    def _backup_item(info, stored=None):
        if stored:
            item = QTableWidgetItem(stored[0])
            item.setToolTip(f"Kopia zapisana: {stored[1] or '?'} (z ostatniego maila)")
            return item
        if not info or not info.get('ostatnia_kopia'):
            return QTableWidgetItem('—')
        saved = info['ostatnia_kopia'].strftime('%Y-%m-%d %H:%M')
        file_date = info.get('data_bazy')
        item = QTableWidgetItem(file_date.strftime('%Y-%m-%d') if file_date else saved)
        item.setToolTip(f"Kopia zapisana: {saved}")
        if info.get('stan') != 'ok':
            item.setForeground(QColor('#c0392b'))
        return item

    def _restore_company(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, 'Firma', 'Wybierz firmę do przywrócenia.')
            return
        firma_id = int(self.table.item(row, 0).text())
        set_company_status(firma_id, 'Aktywne')
        self.refresh_async()

    # ============================================================
    #  FILTERS
    # ============================================================
    def _search(self):
        self._reads.invalidate()
        search = self.search_input.text().strip()
        status = self.status_filter.currentText()
        rows = get_companies_filtered(search, status, None, self.pracownik_id)
        self._load_table(rows)

    def _queue_search(self):
        self._reads.invalidate()
        self._search_timer.start()

    def refresh_async(self):
        self._search_timer.stop()
        request = (self.search_input.text().strip(), self.status_filter.currentText())
        actor = self.pracownik_id

        def read():
            from app.utils.backup_status import stored_backup_dates
            rows = get_companies_filtered(*request, None, actor)
            return rows, self._backup_by_company(), stored_backup_dates()

        def loaded(result):
            if request == (self.search_input.text().strip(), self.status_filter.currentText()):
                self._load_table(*result)

        self._reads.submit(read, loaded, lambda exc: logging.getLogger(__name__).warning('company_read failed: %s', exc))

    def _clear_filters(self):
        self.search_input.clear()
        self.status_filter.setCurrentIndex(0)
        self.refresh_async()

    # ============================================================
    #  CRUD
    # ============================================================
    def _add_company(self):
        dlg = CompanyForm(None, self, actor_id=self.pracownik_id)
        if dlg.exec():
            self.refresh_async()

    def _generate_documents(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, 'Komplet dokumentów', 'Wybierz klienta.')
            return
        company_id = int(self.table.item(row, 0).text())
        from app.documents.contract_pack_ui import ContractPackDialog
        try:
            dialog = ContractPackDialog(company_id, self.pracownik_id, self)
            dialog.exec()
        except (PermissionError, ValueError) as error:
            QMessageBox.warning(self, 'Komplet dokumentów', str(error))

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

        dlg = CompanyForm(firma_id, self, actor_id=self.pracownik_id)
        if dlg.exec():
            self.refresh_async()

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
            delete_company(firma_id, actor_id=self.pracownik_id)
            self.refresh_async()
        except PermissionError as exc:
            QMessageBox.warning(self, "Brak uprawnień", str(exc))
        except Exception as e:
            QMessageBox.critical(self, "Błąd", str(e))
