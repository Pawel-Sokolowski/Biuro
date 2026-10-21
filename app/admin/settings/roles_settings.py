from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QMessageBox, QHBoxLayout,
    QDialog, QFormLayout, QLineEdit, QCheckBox, QDialogButtonBox
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt

from app.admin.global_settings_logic import GlobalSettingsLogic


class RolesSettings(QWidget):
    """Role: podgląd, dodawanie, edycja uprawnień."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Role i uprawnienia")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "ID", "Nazwa", "Faktury", "Firmy", "Użytkownicy",
            "Finanse", "Eksport"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)

        btn_add = QPushButton("Dodaj rolę")
        btn_add.clicked.connect(self._add)
        btn_row.addWidget(btn_add)

        btn_edit = QPushButton("Edytuj wybraną")
        btn_edit.clicked.connect(self._edit_selected)
        btn_row.addWidget(btn_edit)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        matrix_title = QLabel("Dostęp do zakładek (administrator widzi wszystkie)")
        matrix_title.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        layout.addWidget(matrix_title)

        from app.security.authorization import TABS
        self._tabs = TABS
        self.matrix = QTableWidget(0, len(TABS))
        self.matrix.setHorizontalHeaderLabels(TABS)
        self.matrix.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.matrix)

        btn_save_matrix = QPushButton("Zapisz dostęp do zakładek")
        btn_save_matrix.clicked.connect(self._save_matrix)
        layout.addWidget(btn_save_matrix)

        self._refresh()

    def _refresh_matrix(self):
        roles = self.logic.get_roles()
        self.matrix.setRowCount(0)
        self._matrix_role_ids = []
        labels = []
        for role in roles:
            rid = role["id"]
            allowed = set(self.logic.get_role_tabs(rid))
            row = self.matrix.rowCount()
            self.matrix.insertRow(row)
            self._matrix_role_ids.append(rid)
            labels.append(str(role["nazwa"]))
            for col, tab in enumerate(self._tabs):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Checked if tab in allowed else Qt.CheckState.Unchecked)
                self.matrix.setItem(row, col, item)
        self.matrix.setVerticalHeaderLabels(labels)

    def _save_matrix(self):
        try:
            for row, rid in enumerate(self._matrix_role_ids):
                tabs = [
                    tab for col, tab in enumerate(self._tabs)
                    if self.matrix.item(row, col).checkState() == Qt.CheckState.Checked
                ]
                self.logic.set_role_tabs(rid, tabs)
            QMessageBox.information(self, "OK", "Zapisano dostęp do zakładek.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")

    def _refresh(self):
        self._refresh_matrix()
        self._refresh_table()

    def _refresh_table(self):
        rows = self.logic.get_roles()
        self.table.setRowCount(0)

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # defensywny odczyt pól (działa zarówno dla dict jak i Row)
            try:
                rid = r["id"]
            except Exception:
                rid = r.get("id") if isinstance(r, dict) else None

            try:
                name = r["nazwa"]
            except Exception:
                name = r.get("nazwa") if isinstance(r, dict) else ""

            def safe_bool(key, default=0):
                if isinstance(r, dict):
                    return bool(r.get(key, default))
                try:
                    return bool(r[key])
                except Exception:
                    return bool(default)

            self.table.setItem(row, 0, QTableWidgetItem(str(rid)))
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem("Tak" if safe_bool("can_view_invoices") else "Nie"))
            self.table.setItem(row, 3, QTableWidgetItem("Tak" if safe_bool("can_edit_companies") else "Nie"))
            self.table.setItem(row, 4, QTableWidgetItem("Tak" if safe_bool("can_manage_users") else "Nie"))
            self.table.setItem(row, 5, QTableWidgetItem("Tak" if safe_bool("can_view_finances") else "Nie"))
            self.table.setItem(row, 6, QTableWidgetItem("Tak" if safe_bool("can_export_data") else "Nie"))

    def _add(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj rolę")
        form = QFormLayout(dlg)

        e_name = QLineEdit()

        cb_inv = QCheckBox("Faktury")
        cb_cmp = QCheckBox("Firmy")
        cb_usr = QCheckBox("Użytkownicy")
        cb_fin = QCheckBox("Finanse")
        cb_exp = QCheckBox("Eksport danych")

        form.addRow("Nazwa roli:", e_name)
        form.addRow(cb_inv)
        form.addRow(cb_cmp)
        form.addRow(cb_usr)
        form.addRow(cb_fin)
        form.addRow(cb_exp)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.add_role(
                    nazwa=e_name.text().strip(),
                    can_view_invoices=cb_inv.isChecked(),
                    can_edit_companies=cb_cmp.isChecked(),
                    can_manage_users=cb_usr.isChecked(),
                    can_view_finances=cb_fin.isChecked(),
                    can_export_data=cb_exp.isChecked()
                )
                self._refresh()
                QMessageBox.information(self, "OK", "Dodano rolę.")
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się dodać: {e}")

    def _edit_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Role", "Wybierz rolę.")
            return

        rid_item = self.table.item(row, 0)
        if not rid_item:
            QMessageBox.critical(self, "Błąd", "Nieprawidłowy wiersz.")
            return

        try:
            rid = int(rid_item.text())
        except Exception:
            QMessageBox.critical(self, "Błąd", "Nieprawidłowy identyfikator roli.")
            return

        role = None
        try:
            role = self.logic.get_role(rid)
        except Exception:
            # fallback: pobierz listę i znajdź ręcznie
            for rr in self.logic.get_roles():
                try:
                    if (isinstance(rr, dict) and rr.get("id") == rid) or (not isinstance(rr, dict) and rr["id"] == rid):
                        role = rr
                        break
                except Exception:
                    continue

        if not role:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono roli.")
            return

        # defensywny odczyt pól roli
        def rget(key, default=0):
            if isinstance(role, dict):
                return role.get(key, default)
            try:
                return role[key]
            except Exception:
                return default

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edytuj rolę: {rget('nazwa','')}")
        form = QFormLayout(dlg)

        e_name = QLineEdit(rget("nazwa", ""))

        cb_inv = QCheckBox("Faktury")
        cb_inv.setChecked(bool(rget("can_view_invoices", 0)))

        cb_cmp = QCheckBox("Firmy")
        cb_cmp.setChecked(bool(rget("can_edit_companies", 0)))

        cb_usr = QCheckBox("Użytkownicy")
        cb_usr.setChecked(bool(rget("can_manage_users", 0)))

        cb_fin = QCheckBox("Finanse")
        cb_fin.setChecked(bool(rget("can_view_finances", 0)))

        cb_exp = QCheckBox("Eksport danych")
        cb_exp.setChecked(bool(rget("can_export_data", 0)))

        form.addRow("Nazwa roli:", e_name)
        form.addRow(cb_inv)
        form.addRow(cb_cmp)
        form.addRow(cb_usr)
        form.addRow(cb_fin)
        form.addRow(cb_exp)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.update_role(
                    rid,
                    nazwa=e_name.text().strip(),
                    can_view_invoices=cb_inv.isChecked(),
                    can_edit_companies=cb_cmp.isChecked(),
                    can_manage_users=cb_usr.isChecked(),
                    can_view_finances=cb_fin.isChecked(),
                    can_export_data=cb_exp.isChecked()
                )
                self._refresh()
                QMessageBox.information(self, "OK", "Zapisano zmiany.")
            except Exception as e:
                QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")
