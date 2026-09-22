from typing import Optional, Dict, Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QMessageBox, QDialog
)
from PyQt6.QtGui import QFont

from app.companies.company_logic import CompanyLogic
from app.companies.company_form import CompanyForm


class CompaniesPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.logic = CompanyLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Firmy – pełna edycja")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "ID", "Nazwa", "NIP", "Miasto", "VAT czynny"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)

        btn_add = QPushButton("Dodaj firmę")
        btn_add.clicked.connect(self._add_company)
        btn_row.addWidget(btn_add)

        btn_edit = QPushButton("Edytuj wybraną")
        btn_edit.clicked.connect(self._edit_selected)
        btn_row.addWidget(btn_edit)

        btn_delete = QPushButton("Usuń wybraną")
        btn_delete.clicked.connect(self._delete_selected)
        btn_row.addWidget(btn_delete)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

    def _refresh(self):
        rows = self.logic.get_all_companies()
        self.table.setRowCount(0)
        for f in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            fid = f.get("id", "")
            nazwa = f.get("nazwa", "")
            nip = f.get("nip", "")
            miasto = f.get("miasto", "")
            vat_czynny = "Tak" if f.get("vat_czynny", 0) else "Nie"

            self.table.setItem(row, 0, QTableWidgetItem(str(fid)))
            self.table.setItem(row, 1, QTableWidgetItem(nazwa))
            self.table.setItem(row, 2, QTableWidgetItem(nip))
            self.table.setItem(row, 3, QTableWidgetItem(miasto))
            self.table.setItem(row, 4, QTableWidgetItem(vat_czynny))

    def _get_selected_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        try:
            return int(item.text())
        except Exception:
            return None

    def _add_company(self):
        self._open_form_dialog(None)

    def _edit_selected(self):
        fid = self._get_selected_id()
        if fid is None:
            QMessageBox.information(self, "Firmy", "Wybierz firmę.")
            return
        firma = self.logic.get_company(fid)
        if not firma:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono firmy.")
            return
        self._open_form_dialog(firma)

    def _delete_selected(self):
        fid = self._get_selected_id()
        if fid is None:
            QMessageBox.information(self, "Firmy", "Wybierz firmę.")
            return
        if QMessageBox.question(
            self,
            "Usuń firmę",
            "Czy na pewno chcesz usunąć tę firmę?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            try:
                self.logic.delete_company(fid)
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć: {e}")

    def _open_form_dialog(self, company_data: Optional[Dict[str, Any]]):
        dlg = QDialog(self)
        dlg.setWindowTitle("Edycja firmy")
        layout = QVBoxLayout(dlg)

        form = CompanyForm(self.pracownik_id, company_data, parent=dlg)

        def on_saved():
            dlg.accept()
            self._refresh()

        dlg.on_saved = on_saved
        layout.addWidget(form)

        dlg.resize(1000, 700)
        dlg.exec()


def open_company_form_dialog(pracownik_id: int, company_data: Optional[Dict[str, Any]], parent=None) -> QDialog:
    dlg = QDialog(parent)
    dlg.setWindowTitle("Edycja firmy")
    layout = QVBoxLayout(dlg)

    form = CompanyForm(pracownik_id, company_data, parent=dlg)

    def on_saved():
        dlg.accept()

    dlg.on_saved = on_saved
    layout.addWidget(form)

    dlg.resize(1000, 700)
    return dlg
