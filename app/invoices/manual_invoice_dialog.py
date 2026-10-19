from __future__ import annotations

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QAbstractItemView,
)

from app.db.dao_conn import get_conn
from app.db.dao_kontrahenci import (
    delete_kontrahent, get_kontrahent, list_kontrahenci, save_kontrahent, search_kontrahenci,
)
from app.db.dao_invoices import create_manual_invoice
from app.invoices.positions import normalize_position
from app.utils.parsing import to_float


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    return table


class KontrahentDialog(QDialog):
    def __init__(self, kontrahent=None, parent=None):
        super().__init__(parent)
        self.kontrahent = kontrahent or {}
        self.setWindowTitle("Kontrahent")
        layout = QFormLayout(self)
        self.typ = QComboBox()
        self.typ.addItem("Osoba fizyczna", "osoba_fizyczna")
        self.typ.addItem("Firma", "firma")
        self.nazwa = QLineEdit(self.kontrahent.get("nazwa", ""))
        self.nip = QLineEdit(self.kontrahent.get("nip", ""))
        self.regon = QLineEdit(self.kontrahent.get("regon", ""))
        self.adres = QLineEdit(self.kontrahent.get("adres", ""))
        self.kod = QLineEdit(self.kontrahent.get("kod", ""))
        self.miasto = QLineEdit(self.kontrahent.get("miasto", ""))
        self.kraj = QLineEdit(self.kontrahent.get("kraj", "Polska"))
        self.email = QLineEdit(self.kontrahent.get("email", ""))
        self.telefon = QLineEdit(self.kontrahent.get("telefon", ""))
        self.typ.setCurrentIndex(max(0, self.typ.findData(self.kontrahent.get("typ", "osoba_fizyczna"))))
        for label, widget in (("Typ:", self.typ), ("Nazwa / imi\u0119 i nazwisko:", self.nazwa), ("NIP (opcjonalnie):", self.nip),
                              ("REGON:", self.regon), ("Adres:", self.adres), ("Kod pocztowy:", self.kod),
                              ("Miasto:", self.miasto), ("Kraj:", self.kraj), ("E-mail:", self.email), ("Telefon:", self.telefon)):
            layout.addRow(label, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Zapisz")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self):
        return {"typ": self.typ.currentData(), "nazwa": self.nazwa.text(), "nip": self.nip.text(),
                "regon": self.regon.text(), "adres": self.adres.text(), "kod": self.kod.text(),
                "miasto": self.miasto.text(), "kraj": self.kraj.text(), "email": self.email.text(),
                "telefon": self.telefon.text()}


class KontrahenciDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kontrahenci")
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        self.table = _table(["ID", "Typ", "Nazwa", "NIP", "Miasto"])
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for label, handler in (("Dodaj", self._add), ("Edytuj", self._edit), ("Usu\u0144", self._delete)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        close = QPushButton("Zamknij")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        self.table.setRowCount(0)
        for entity in list_kontrahenci():
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [entity["id"], "Osoba fizyczna" if entity["typ"] == "osoba_fizyczna" else "Firma",
                      entity["nazwa"], entity.get("nip"), entity.get("miasto")]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem("" if value is None else str(value)))

    def _selected_id(self):
        row = self.table.currentRow()
        return int(self.table.item(row, 0).text()) if row >= 0 else None

    def _add(self):
        dialog = KontrahentDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                save_kontrahent(dialog.values())
                self.refresh()
            except ValueError as exc:
                QMessageBox.warning(self, "Kontrahent", str(exc))

    def _edit(self):
        selected = self._selected_id()
        if selected is None:
            QMessageBox.information(self, "Kontrahent", "Wybierz kontrahenta.")
            return
        dialog = KontrahentDialog(get_kontrahent(selected), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                save_kontrahent(dialog.values(), selected)
                self.refresh()
            except ValueError as exc:
                QMessageBox.warning(self, "Kontrahent", str(exc))

    def _delete(self):
        selected = self._selected_id()
        if selected is None:
            QMessageBox.information(self, "Kontrahent", "Wybierz kontrahenta.")
            return
        if QMessageBox.question(self, "Usuwanie", "Usun\u0144 wybranego kontrahenta?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            delete_kontrahent(selected)
            self.refresh()


class ManualInvoiceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wystaw faktur\u0119")
        self.resize(900, 650)
        self.buyer = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        buyer_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Wpisz nazw\u0119 lub NIP firmy / kontrahenta")
        buyer_row.addWidget(self.search, 1)
        self.btn_new = QPushButton("Nowy kontrahent")
        buyer_row.addWidget(self.btn_new)
        form.addRow("Nabywca:", buyer_row)
        self.results = QListWidget()
        self.results.setMaximumHeight(120)
        form.addRow("Wyniki:", self.results)
        self.selected_label = QLabel("Nie wybrano nabywcy")
        form.addRow("Wybrano:", self.selected_label)
        self.date_issue, self.date_sale, self.date_due = self._date_widget(), self._date_widget(), self._date_widget(7)
        form.addRow("Data wystawienia:", self.date_issue)
        form.addRow("Data sprzeda\u017Cy:", self.date_sale)
        form.addRow("Termin p\u0142atno\u015Bci:", self.date_due)
        layout.addLayout(form)
        self.items = QTableWidget(0, 5)
        self.items.setHorizontalHeaderLabels(["Nazwa", "Ilo\u015B\u0107", "Jednostka", "Cena netto", "VAT %"])
        self.items.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.items)
        item_buttons = QHBoxLayout()
        add = QPushButton("Dodaj pozycj\u0119")
        remove = QPushButton("Usu\u0144 pozycj\u0119")
        add.clicked.connect(self._add_item)
        remove.clicked.connect(self._remove_item)
        item_buttons.addWidget(add)
        item_buttons.addWidget(remove)
        item_buttons.addStretch()
        layout.addLayout(item_buttons)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Zapisz")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._search_buyers)
        self.results.itemClicked.connect(self._select_buyer)
        self.btn_new.clicked.connect(self._new_contractor)
        self._add_item()

    @staticmethod
    def _date_widget(days_from_today=0):
        widget = QDateEdit()
        widget.setCalendarPopup(True)
        widget.setDisplayFormat("yyyy-MM-dd")
        widget.setDate(QDate.currentDate().addDays(days_from_today))
        return widget

    def _search_buyers(self, text):
        self.buyer = None
        self.selected_label.setText("Nie wybrano nabywcy")
        self.results.clear()
        term = (text or "").strip()
        if not term:
            return
        conn = get_conn()
        try:
            like = f"%{term}%"
            companies = conn.execute("SELECT id, nazwa, nip FROM firmy WHERE nazwa ILIKE %s OR nip ILIKE %s ORDER BY lower(nazwa) LIMIT 30", (like, like)).fetchall()
        finally:
            conn.close()
        for entity in companies:
            item = QListWidgetItem(f"Firma: {entity[1]}" + (f" (NIP: {entity[2]})" if entity[2] else ""))
            item.setData(Qt.ItemDataRole.UserRole, {"typ": "firma", "id": entity[0]})
            self.results.addItem(item)
        for entity in search_kontrahenci(term):
            kind = "Osoba fizyczna" if entity["typ"] == "osoba_fizyczna" else "Firma"
            item = QListWidgetItem(f"Kontrahent: {entity['nazwa']} ({kind})" + (f" (NIP: {entity['nip']})" if entity.get("nip") else ""))
            item.setData(Qt.ItemDataRole.UserRole, {"typ": "kontrahent", "id": entity["id"]})
            self.results.addItem(item)

    def _select_buyer(self, item):
        self.buyer = item.data(Qt.ItemDataRole.UserRole)
        self.selected_label.setText(item.text())

    def _new_contractor(self):
        dialog = KontrahentDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                save_kontrahent(dialog.values())
                self.search.setText(dialog.nazwa.text())
            except ValueError as exc:
                QMessageBox.warning(self, "Kontrahent", str(exc))

    def _add_item(self):
        row = self.items.rowCount()
        self.items.insertRow(row)
        for column, value in enumerate(("", "1", "szt.", "0.00", "23")):
            self.items.setItem(row, column, QTableWidgetItem(value))

    def _remove_item(self):
        row = self.items.currentRow()
        if row >= 0:
            self.items.removeRow(row)

    def _positions(self):
        positions = []
        for row in range(self.items.rowCount()):
            values = [self.items.item(row, col).text().strip() if self.items.item(row, col) else "" for col in range(5)]
            if not any(values):
                continue
            positions.append(normalize_position({"nazwa": values[0], "ilosc": to_float(values[1], 0),
                                                 "jednostka": values[2] or "szt.", "cena_netto": to_float(values[3], 0),
                                                 "vat": to_float(values[4], 0)}))
        return positions

    def _save(self):
        if not self.buyer:
            QMessageBox.warning(self, "Faktura", "Wybierz nabywc\u0119 z listy wynik\u00f3w.")
            return
        try:
            positions = self._positions()
            if not positions or any(not position["nazwa"] for position in positions):
                raise ValueError("Dodaj co najmniej jedn\u0105 pozycj\u0119 i uzupe\u0142nij jej nazw\u0119.")
            create_manual_invoice(self.buyer["typ"], self.buyer["id"], positions,
                                  self.date_issue.date().toString("yyyy-MM-dd"),
                                  self.date_sale.date().toString("yyyy-MM-dd"),
                                  self.date_due.date().toString("yyyy-MM-dd"))
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Faktura", str(exc))
            return
        self.accept()
