"""Potwierdzanie przelewów ING i płatności gotówkowych w GUI faktur."""
from __future__ import annotations

from decimal import Decimal

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.db.dao_payments import (
    PaymentError, confirm_bank_payment, import_mt940_file, list_invoice_choices,
    list_unsettled_transfers, record_cash_payment, suggested_invoice_ids,
)


def money_text(value) -> str:
    return f"{Decimal(str(value)):.2f}"


class BankPaymentReview(QDialog):
    """Bez automatycznych księgowań — użytkownik zatwierdza każdą alokację."""
    def __init__(self, actor_id: int, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.setWindowTitle("Uzgadnianie przelewów ING MT940 z fakturami")
        self.resize(1200, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Wybierz przelew, sprawdź proponowaną fakturę i kwotę, "
            "a następnie zatwierdź rozliczenie. Żaden przelew nie jest przypisywany automatycznie."
        ))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "ID", "Data", "Wpływ / pozostało (PLN)", "Tytuł przelewu",
            "Przypisz do faktury", "Kwota rozliczenia (PLN)",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(3, 330)
        self.table.setColumnWidth(4, 330)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.btn_import = QPushButton("Importuj ING MT940")
        self.btn_import.clicked.connect(self._import_file)
        buttons.addWidget(self.btn_import)
        self.btn_confirm = QPushButton("Potwierdź rozliczenie wybranego przelewu")
        self.btn_confirm.clicked.connect(self._confirm)
        buttons.addWidget(self.btn_confirm)
        refresh = QPushButton("Odśwież")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        layout.addLayout(buttons)
        self.refresh()

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import wyciągu ING MT940", "",
            "Wyciągi MT940 (*.sta *.mt940 *.txt *.940);;Wszystkie pliki (*)",
        )
        if not path:
            return
        try:
            report = import_mt940_file(path, self.actor_id)
        except (PaymentError, OSError, PermissionError) as exc:
            QMessageBox.warning(self, "Wyciąg MT940", str(exc))
            return
        QMessageBox.information(
            self, "Wyciąg MT940",
            f"Transakcje w pliku: {report['all']}\n"
            f"Nowe: {report['new']}\n"
            f"Już importowane: {report['duplicates']}\n\n"
            "Operacje wymagają potwierdzenia na liście przed rozliczeniem.",
        )
        self.refresh()

    def refresh(self):
        self.invoices = list_invoice_choices(self.actor_id)
        self.transactions = list_unsettled_transfers(self.actor_id)
        self.table.setRowCount(0)
        for entry in self.transactions:
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            values = (
                entry["id"], entry["data_operacji"],
                f"{money_text(entry['kwota'])} / {money_text(entry['remaining'])}",
                entry.get("tytul") or entry.get("referencja") or "",
            )
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(idx, col, cell)
            choices = QComboBox(self.table)
            choices.addItem("— Wybierz fakturę (wymagane potwierdzenie) —", None)
            suggestions = suggested_invoice_ids(entry, self.invoices)
            ordering = suggestions + [i["id"] for i in self.invoices if i["id"] not in suggestions]
            by_id = {i["id"]: i for i in self.invoices}
            for invoice_id in ordering:
                invoice = by_id[invoice_id]
                prefix = "★ " if invoice_id in suggestions else ""
                choices.addItem(
                    f"{prefix}{invoice['numer'] or '(bez numeru)'} — "
                    f"{invoice['buyer']} — saldo {money_text(invoice['balance'])} zł",
                    invoice_id,
                )
            # Sugestia jest tylko wizualna; nie wybieramy faktury automatycznie.
            self.table.setCellWidget(idx, 4, choices)
            from PyQt6.QtWidgets import QLineEdit
            amount = QLineEdit(money_text(entry["remaining"]), self.table)
            self.table.setCellWidget(idx, 5, amount)
        if self.transactions:
            self.table.selectRow(0)

    def _confirm(self):
        selected = self.table.currentRow()
        if selected < 0 or selected >= len(self.transactions):
            QMessageBox.warning(self, "Rozliczenie", "Wybierz przelew.")
            return
        transfer = self.transactions[selected]
        combo = self.table.cellWidget(selected, 4)
        invoice_id = combo.currentData()
        if invoice_id is None:
            QMessageBox.warning(self, "Rozliczenie", "Wybierz konkretną fakturę.")
            return
        invoice = next(i for i in self.invoices if i["id"] == invoice_id)
        amount = self.table.cellWidget(selected, 5).text().strip().replace(",", ".")
        msg = (
            f"Czy zatwierdzić przypisanie przelewu?\n\n"
            f"Data: {transfer['data_operacji']}\n"
            f"Tytuł: {transfer.get('tytul') or ''}\n"
            f"Faktura: {invoice['numer']} — {invoice['buyer']}\n"
            f"Kwota przypisania: {amount} PLN"
        )
        if QMessageBox.question(
            self, "Potwierdzenie rozliczenia", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            confirm_bank_payment(int(transfer["id"]), int(invoice_id), amount, self.actor_id)
        except (PaymentError, PermissionError) as exc:
            QMessageBox.warning(self, "Rozliczenie", str(exc))
            return
        QMessageBox.information(self, "Rozliczenie", "Płatność przypisana do faktury.")
        self.refresh()


def record_cash_in_dialog(invoice: dict, actor_id: int, parent=None) -> bool:
    form = QDialog(parent)
    form.setWindowTitle("Zapłata gotówką")
    layout = QFormLayout(form)
    date_input = QDateEdit(QDate.currentDate(), form)
    date_input.setCalendarPopup(True)
    date_input.setDisplayFormat("dd/MM/yyyy")
    amount_input = QInputDialog  # amount without a floating point round-trip
    from PyQt6.QtWidgets import QLineEdit
    amount_edit = QLineEdit(form)
    amount_edit.setText(money_text(invoice["kwota_brutto"]))
    layout.addRow("Faktura:", QLabel(str(invoice.get("numer") or invoice["id"]), form))
    layout.addRow("Data otrzymania gotówki:", date_input)
    layout.addRow("Kwota (PLN):", amount_edit)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        parent=form,
    )
    buttons.accepted.connect(form.accept)
    buttons.rejected.connect(form.reject)
    layout.addRow(buttons)
    if form.exec() != QDialog.DialogCode.Accepted:
        return False
    amount = amount_edit.text().strip().replace(",", ".")
    if QMessageBox.question(
        parent, "Potwierdź płatność gotówkową",
        f"Zarejestrować wpłatę {amount} zł z dnia {date_input.date().toString('dd/MM/yyyy')} "
        f"na fakturę {invoice.get('numer') or invoice['id']}?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    ) != QMessageBox.StandardButton.Yes:
        return False
    try:
        record_cash_payment(
            int(invoice["id"]),
            date_input.date().toString("yyyy-MM-dd"), amount, actor_id,
        )
    except (PaymentError, PermissionError) as exc:
        QMessageBox.warning(parent, "Gotówka", str(exc))
        return False
    return True
