from datetime import date

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView, QHeaderView,
)

from app.companies.constants import ROZLICZENIE_ROCZNE, ROZLICZENIE_ROCZNE_OPIS
from app.db.dao_year_end import list_year_end, save_declaration, set_company_declarations
from app.utils.background import TaskRunner
from app.utils.view_state import preserve_view_state

HEADERS = ["Klient", "NIP", "Forma opodatkowania", "Deklaracja", "Stan", "Data złożenia",
           "Kwota", "UPO", "Uwagi"]


class DeclarationDialog(QDialog):
    def __init__(self, title: str, row: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.filed = QCheckBox("Złożona")
        self.filed.setChecked(bool(row.get("zlozona")))
        self.date = QLineEdit(row.get("data_zlozenia") or "")
        self.date.setPlaceholderText("RRRR-MM-DD")
        self.amount = QDoubleSpinBox()
        self.amount.setRange(-1e9, 1e9)
        self.amount.setDecimals(2)
        self.amount.setValue(float(row.get("kwota") or 0))
        self.upo = QLineEdit(row.get("upo") or "")
        self.notes = QLineEdit(row.get("uwagi") or "")
        form.addRow(self.filed)
        form.addRow("Data złożenia:", self.date)
        form.addRow("Kwota (podatek / nadpłata):", self.amount)
        form.addRow("Nr UPO:", self.upo)
        form.addRow("Uwagi:", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.filed.toggled.connect(self._fill_date)

    def _fill_date(self, checked):
        if checked and not self.date.text().strip():
            self.date.setText(date.today().isoformat())

    def _accept(self):
        text = self.date.text().strip()
        if text:
            try:
                date.fromisoformat(text)
            except ValueError:
                QMessageBox.warning(self, "Data", "Podaj datę w formacie RRRR-MM-DD.")
                return
        self.accept()


class YearEndPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None, embedded: bool = False):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self._rows = []
        self._companies = []
        self._cache_year = None
        self._requested_year = None
        self._reads = TaskRunner(self)
        self._columns_sized = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.title = QLabel("<b>Zakończenie roku podatkowego</b>")
        top.addWidget(self.title)
        top.addStretch()
        self.year_label = QLabel("Rok:")
        top.addWidget(self.year_label)
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(date.today().year - 1)
        self.year.valueChanged.connect(self.refresh)
        top.addWidget(self.year)
        if embedded:
            for widget in (self.title, self.year_label, self.year):
                widget.setVisible(False)
        self.only_open = QCheckBox("Tylko niezłożone")
        self.only_open.toggled.connect(self._apply_filters)
        top.addWidget(self.only_open)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Szukaj klienta…")
        self.search.textChanged.connect(self._apply_filters)
        top.addWidget(self.search)
        self.btn_mark = QPushButton("Oznacz jako złożoną")
        self.btn_mark.clicked.connect(self._mark_filed)
        top.addWidget(self.btn_mark)
        self.btn_edit = QPushButton("Edytuj…")
        self.btn_edit.clicked.connect(self._edit_selected)
        top.addWidget(self.btn_edit)
        layout.addLayout(top)

        self.summary = QLabel()
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda *_: self._edit_selected())
        layout.addWidget(self.table)
        if not embedded:
            self.refresh()

    def set_year(self, year: int):
        if self.year.value() != year:
            self.year.setValue(year)
        else:
            self.refresh()

    def refresh(self, *_):
        year = self.year.value()
        if self._requested_year != year:
            self._companies = []
            self._cache_year = None
            self._apply_filters()
            self.summary.setText('Wczytywanie deklaracji…')
        self._requested_year = year

        def loaded(companies):
            if self.year.value() == year:
                self._companies = companies
                self._cache_year = year
                self._apply_filters()

        def failed(exc):
            if self.year.value() == year:
                self.summary.setText('Nie udało się odświeżyć deklaracji.')
                self.summary.setToolTip(str(exc))

        self._reads.submit(lambda: list_year_end(year), loaded, failed, key=year)

    def _apply_filters(self, *_):
        needle = self.search.text().strip().lower()
        rows = []
        missing = []
        for company in self._companies:
            name = company.get("nazwa_skrocona") or company.get("nazwa") or ""
            if needle and needle not in f"{name} {company.get('nazwa', '')} {company.get('nip', '')}".lower():
                continue
            if not company["deklaracje"]:
                missing.append(name)
                rows.append((company, name, None))
                continue
            for decl in company["deklaracje"]:
                if self.only_open.isChecked() and decl.get("zlozona"):
                    continue
                rows.append((company, name, decl))
        self._rows = rows
        with preserve_view_state(self.table, lambda index: index.data(Qt.ItemDataRole.UserRole)):
            self._fill_table(rows, missing)

    def _fill_table(self, rows, missing):
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.setRowCount(len(rows))
        done = 0
        for r, (company, name, decl) in enumerate(rows):
            if decl is None:
                values = [name, company.get("nip") or "", company.get("forma_opodatkowania") or "",
                          "BRAK", "", "", "", "", ""]
                for c, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if c == 0:
                        item.setData(Qt.ItemDataRole.UserRole, (company['id'], None, None))
                    if c == 3:
                        item.setForeground(Qt.GlobalColor.red)
                    self.table.setItem(r, c, item)
                continue
            filed = bool(decl.get("zlozona"))
            done += filed
            values = [
                name, company.get("nip") or "", company.get("forma_opodatkowania") or "",
                decl["kod"] + (" — " + decl["owner_name"] if decl.get("owner_name") else ""), "Złożona" if filed else "Do złożenia",
                decl.get("data_zlozenia") or "",
                "" if decl.get("kwota") is None else f"{float(decl['kwota']):.2f}",
                decl.get("upo") or "", decl.get("uwagi") or "",
            ]
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, (company['id'], decl['kod'], decl.get('owner_key', 0)))
                if c == 4:
                    item.setForeground(Qt.GlobalColor.darkGreen if filed else Qt.GlobalColor.red)
                self.table.setItem(r, c, item)
        if rows and not self._columns_sized:
            self.table.resizeColumnsToContents()
            self._columns_sized = True
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        text = f"Złożone: {done} / {len(rows) - len(missing)}"
        if missing:
            text += f"   |   Bez deklaracji (BRAK): {len(missing)} — zaznacz i kliknij „Edytuj…”"
        self.summary.setText(text)
        self.summary.setToolTip('')

    def _selected(self):
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _save(self, company, decl, values):
        try:
            save_declaration(self.pracownik_id, company["id"], self.year.value(), decl["kod"], owner_key=decl.get("owner_key", 0), **values)
        except Exception as exc:
            QMessageBox.critical(self, "Zakończenie roku", f"Nie udało się zapisać: {exc}")
            return
        self.refresh()

    def _mark_filed(self):
        sel = self._selected()
        if not sel:
            return
        company, _, decl = sel
        if decl is None:
            self._edit_selected()
            return
        self._save(company, decl, dict(
            filed=True, filed_date=decl.get("data_zlozenia") or date.today().isoformat(),
            amount=decl.get("kwota"), upo=decl.get("upo") or "", notes=decl.get("uwagi") or ""))

    def _edit_selected(self):
        sel = self._selected()
        if not sel:
            return
        company, name, decl = sel
        if decl is None:
            from app.companies.company_form import CompanyForm
            dlg = CompanyForm(company["id"], self, actor_id=self.pracownik_id)
            dlg.annual_year.setValue(self.year.value())
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.refresh()
            return
        dialog = DeclarationDialog(f"{name} — {decl['kod']} za {self.year.value()}", decl, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        amount = dialog.amount.value()
        self._save(company, decl, dict(
            filed=dialog.filed.isChecked(), filed_date=dialog.date.text().strip() or None,
            amount=amount if (amount or decl.get("kwota") is not None) else None,
            upo=dialog.upo.text().strip(), notes=dialog.notes.text().strip()))
