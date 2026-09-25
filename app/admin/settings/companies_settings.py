# app/admin/settings/companies_settings.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QMessageBox, QHBoxLayout, QDialog,
    QVBoxLayout as QVBoxLayoutDialog
)
from PyQt6.QtGui import QFont

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.companies.company_form import CompanyForm


class CompaniesSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Firmy")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ID", "Nazwa", "NIP", "Miasto"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)

        btn_add = QPushButton("Dodaj nową")
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
        rows = self.logic.get_companies()
        self.table.setRowCount(0)

        for f in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            def get_field(obj, key, default=""):
                if isinstance(obj, dict):
                    return obj.get(key, default)
                try:
                    return obj[key]
                except Exception:
                    return default

            fid = get_field(f, "id", "")
            nazwa = get_field(f, "nazwa", "")
            nip = get_field(f, "nip", "")
            miasto = get_field(f, "miasto", "")

            self.table.setItem(row, 0, QTableWidgetItem(str(fid)))
            self.table.setItem(row, 1, QTableWidgetItem(nazwa))
            self.table.setItem(row, 2, QTableWidgetItem(nip))
            self.table.setItem(row, 3, QTableWidgetItem(miasto))

    def _get_selected_id(self):
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
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj firmę")
        lay = QVBoxLayoutDialog(dlg)
        form = CompanyForm(pracownik_id=0, company_data=None, parent=dlg)
        lay.addWidget(form)

        def on_saved():
            self._refresh()
        dlg.on_saved = on_saved

        dlg.exec()

    def _edit_selected(self):
        fid = self._get_selected_id()
        if fid is None:
            QMessageBox.information(self, "Firmy", "Wybierz firmę.")
            return

        firma = self.logic.get_company(fid)
        if not firma:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono firmy.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Edytuj firmę")
        lay = QVBoxLayoutDialog(dlg)
        form = CompanyForm(pracownik_id=0, company_data=firma, parent=dlg)
        lay.addWidget(form)

        def on_saved():
            self._refresh()
        dlg.on_saved = on_saved

        dlg.exec()

    def _delete_selected(self):
        fid = self._get_selected_id()
        if fid is None:
            QMessageBox.information(self, "Firmy", "Wybierz firmę.")
            return

        ret = QMessageBox.question(
            self,
            "Usuń firmę",
            f"Czy na pewno chcesz usunąć firmę ID={fid}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        try:
            self.logic.delete_company(fid)
            self._refresh()
            QMessageBox.information(self, "OK", "Usunięto firmę.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć firmy: {e}")
