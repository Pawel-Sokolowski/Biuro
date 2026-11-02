from __future__ import annotations

from PyQt6.QtCore import Qt, QDate, QThreadPool
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QAbstractItemView,
)

from app.db.dao_conn import get_conn
from app.companies.company_lookup import lookup_company_by_nip
from app.companies.company_logic import CEIDGWorker
from app.utils.payment_terms import default_due_date
from app.db.dao_kontrahenci import (
    delete_kontrahent, get_kontrahent, list_kontrahenci, save_kontrahent, search_kontrahenci,
)
from app.db.dao_invoices import create_manual_invoice
from app.invoices.positions import normalize_position
from app.invoices.service_picker import choose_service
from app.invoices.vat_summary import VatSummary
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
        self.btn_lookup = QPushButton("Pobierz dane po NIP (GUS / CEIDG)")
        self.btn_lookup.setToolTip("Wpisz NIP firmy. Pobrane dane możesz sprawdzić i zmienić przed zapisaniem.")
        self.btn_lookup.clicked.connect(self._lookup_company)
        layout.addRow("Dane rejestrowe:", self.btn_lookup)
        self._lookup_worker = None
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Zapisz")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        self.buttons = buttons
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _lookup_company(self):
        if self._lookup_worker is not None:
            return
        nip = "".join(ch for ch in self.nip.text() if ch.isdigit())
        if len(nip) != 10:
            QMessageBox.warning(self, "Dane kontrahenta", "Wpisz NIP firmy składający się z 10 cyfr.")
            return
        self._lookup_nip = nip
        self._lookup_worker = CEIDGWorker(nip, self, fetch=lookup_company_by_nip)
        self._lookup_worker.signals.result.connect(self._lookup_result)
        self._lookup_worker.signals.error.connect(self._lookup_error)
        self._lookup_worker.signals.finished.connect(self._lookup_finished)
        self._set_lookup_busy(True)
        QThreadPool.globalInstance().start(self._lookup_worker)

    def _set_lookup_busy(self, busy):
        self.btn_lookup.setEnabled(not busy)
        self.btn_lookup.setText("Pobieranie…" if busy else "Pobierz dane po NIP (GUS / CEIDG)")
        for widget in (self.typ, self.nazwa, self.nip, self.regon, self.adres, self.kod,
                       self.miasto, self.kraj, self.email, self.telefon):
            widget.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not busy)

    def _lookup_result(self, payload):
        _, data = payload
        if not data or data.get("error"):
            QMessageBox.warning(self, "Dane kontrahenta", (data or {}).get("message") or "Nie znaleziono firmy.")
            return
        returned_nip = "".join(ch for ch in str(data.get("nip") or self._lookup_nip) if ch.isdigit())
        if returned_nip != self._lookup_nip:
            QMessageBox.warning(self, "Dane kontrahenta", "Rejestr zwrócił inny NIP. Dane nie zostały wczytane.")
            return
        address = data.get("adresDzialalnosci") or data.get("adres_dzialalnosci") or {}
        street = data.get("dz_ulica") or address.get("ulica") or data.get("ulica") or ""
        building = data.get("dz_budynek") or address.get("budynek") or ""
        apartment = data.get("dz_lokal") or address.get("lokal") or ""
        number = str(building) + (f"/{apartment}" if apartment else "")
        street_address = " ".join(str(part) for part in (street, number) if part)
        values = ((self.nazwa, data.get("nazwa")), (self.nip, returned_nip), (self.regon, data.get("regon")),
                  (self.adres, street_address or data.get("adres")),
                  (self.kod, data.get("dz_kod") or address.get("kod") or address.get("kodPocztowy") or data.get("kod")),
                  (self.miasto, data.get("dz_miasto") or address.get("miasto") or data.get("miasto")),
                  (self.kraj, data.get("dz_kraj") or address.get("kraj") or data.get("kraj")),
                  (self.email, data.get("email") or data.get("kontaktEmail")),
                  (self.telefon, data.get("telefon") or data.get("kontaktTelefon")))
        for widget, value in values:
            if value:
                widget.setText(str(value))
        self.typ.setCurrentIndex(self.typ.findData("firma"))
        if data.get("_registry_warning"):
            QMessageBox.warning(self, "Dane kontrahenta", "Pobrano dostępne dane. " + str(data["_registry_warning"]))

    def _lookup_error(self, error):
        QMessageBox.warning(self, "Dane kontrahenta", f"Nie udało się pobrać danych: {error}")

    def _lookup_finished(self):
        self._lookup_worker = None
        self._set_lookup_busy(False)

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
        self.date_issue, self.date_sale, self.date_due = self._date_widget(), self._date_widget(), self._date_widget()
        # Wartość wyjściowa wynika z ustawień globalnych, nie jest na sztywno +7 dni.
        self._suggested_due_date = None
        self._on_issue_date_changed(self.date_issue.date())
        self.date_issue.dateChanged.connect(self._on_issue_date_changed)
        form.addRow("Data wystawienia:", self.date_issue)
        form.addRow("Data sprzeda\u017Cy:", self.date_sale)
        form.addRow("Termin p\u0142atno\u015Bci:", self.date_due)
        self.podstawa_zwolnienia = QLineEdit()
        self.podstawa_zwolnienia.setPlaceholderText("Np. art. 113 ust. 1 ustawy o VAT — wymagane dla zw")
        form.addRow("Podstawa zwolnienia z VAT:", self.podstawa_zwolnienia)
        layout.addLayout(form)
        self.items = QTableWidget(0, 5)
        self.items.setHorizontalHeaderLabels(["Nazwa", "Ilo\u015B\u0107", "Jednostka", "Cena netto", "VAT %"])
        self.items.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.items)
        self.vat_summary = VatSummary()
        layout.addWidget(self.vat_summary)
        self.items.itemChanged.connect(self._update_vat_summary)
        item_buttons = QHBoxLayout()
        add = QPushButton("Dodaj pozycj\u0119")
        remove = QPushButton("Usu\u0144 pozycj\u0119")
        add.clicked.connect(self._add_item)
        remove.clicked.connect(self._remove_item)
        item_buttons.addWidget(add)
        item_buttons.addWidget(remove)
        self.btn_catalogue = QPushButton("Dodaj z katalogu usług")
        self.btn_catalogue.clicked.connect(self._add_catalogue_service)
        item_buttons.addWidget(self.btn_catalogue)
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
        widget.setDisplayFormat("dd-MM-yyyy")
        widget.setDate(QDate.currentDate().addDays(days_from_today))
        return widget

    def _on_issue_date_changed(self, issue_qdate):
        # Jeżeli użytkownik ręcznie wybrał termin, nie nadpisujemy go
        # podczas zmiany daty wystawienia.
        due = default_due_date(issue_qdate.toPyDate())
        next_due = QDate(due.year, due.month, due.day)
        if self._suggested_due_date is None or self.date_due.date() == self._suggested_due_date:
            self.date_due.setDate(next_due)
        self._suggested_due_date = next_due

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

    def _add_catalogue_service(self):
        position = choose_service(self)
        if position is None:
            return
        # Reuse the untouched placeholder instead of leaving an invalid blank line.
        row = next((r for r in range(self.items.rowCount())
                    if [self.items.item(r, c).text() if self.items.item(r, c) else "" for c in range(5)]
                    == ["", "1", "szt.", "0.00", "23"]), None)
        if row is None:
            row = self.items.rowCount()
            self._add_item()
        for column, value in enumerate((position["nazwa"], f'{position["ilosc"]:g}', position["jednostka"],
                                        f'{position["cena_netto"]:.2f}', position.get("vat_code") or f'{position["vat"]:g}')):
            self.items.setItem(row, column, QTableWidgetItem(value))
        self.items.item(row, 0).setData(Qt.ItemDataRole.UserRole, position)
        self._update_vat_summary()
        self.items.setCurrentCell(row, 0)

    def _update_vat_summary(self, *_args):
        self.vat_summary.set_positions(self._positions())

    def _remove_item(self):
        row = self.items.currentRow()
        if row >= 0:
            self.items.removeRow(row)
            self._update_vat_summary()

    def _positions(self):
        positions = []
        for row in range(self.items.rowCount()):
            values = [self.items.item(row, col).text().strip() if self.items.item(row, col) else "" for col in range(5)]
            if not any(values):
                continue
            positions.append(normalize_position({**(self.items.item(row, 0).data(Qt.ItemDataRole.UserRole) or {}), "nazwa": values[0], "ilosc": to_float(values[1], 0),
                                                 "jednostka": values[2] or "szt.", "cena_netto": to_float(values[3], 0),
                                                 "vat": values[4] or 0, "vat_code": values[4].strip().lower().replace("%", "").strip()}))
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
                                  self.date_due.date().toString("yyyy-MM-dd"),
                                  podstawa_zwolnienia=self.podstawa_zwolnienia.text().strip())
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Faktura", str(exc))
            return
        self.accept()
