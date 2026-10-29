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
    list_unsettled_transfers, record_cash_payment, suggested_invoice_ids, delete_bank_transaction
)

def money_text(value) -> str:
    return f"{Decimal(str(value)):.2f}"

class BankPaymentReview(QDialog):
    """Bez automatycznych księgowań — użytkownik zatwierdza każdą alokację."""
    def __init__(self, actor_id: int, parent=None):
        super().__init__(parent)

        self.actor_id = actor_id

        self.setWindowTitle(
            "Uzgadnianie przelewów ING MT940 z fakturami"
        )

        self.resize(1400, 700)

        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "Wybierz przelew, sprawdź proponowaną fakturę i kwotę, "
                "a następnie zatwierdź rozliczenie."
            )
        )

        self.table = QTableWidget(0, 7)

        self.table.setHorizontalHeaderLabels([
            "ID",
            "Data",
            "Wpływ / pozostało (PLN)",
            "Tytuł",
            "Kontrahent",
            "Przypisz do faktury",
            "Kwota rozliczenia (PLN)",
        ])

        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        self.table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )

        self.table.horizontalHeader().setStretchLastSection(True)

        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 250)
        self.table.setColumnWidth(4, 300)
        self.table.setColumnWidth(5, 350)

        layout.addWidget(self.table)

        buttons = QHBoxLayout()

        self.btn_import = QPushButton(
            "Importuj ING MT940"
        )
        self.btn_import.clicked.connect(
            self._import_file
        )
        buttons.addWidget(self.btn_import)

        self.btn_confirm = QPushButton(
            "Rozlicz wybrany"
        )
        self.btn_confirm.clicked.connect(
            self._confirm
        )
        buttons.addWidget(
            self.btn_confirm
        )

        self.btn_confirm_all = QPushButton(
            "Rozlicz wszystkie"
        )
        self.btn_confirm_all.clicked.connect(
            self._confirm_all
        )
        buttons.addWidget(
            self.btn_confirm_all
        )

        self.btn_delete = QPushButton(
            "Usuń przelew"
        )
        self.btn_delete.clicked.connect(
            self._delete_selected
        )
        buttons.addWidget(
            self.btn_delete
        )

        refresh = QPushButton(
            "Odśwież"
        )
        refresh.clicked.connect(
            self.refresh
        )
        buttons.addWidget(refresh)

        buttons.addStretch()

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

        self.invoices = list_invoice_choices(
            self.actor_id
        )

        self.transactions = list_unsettled_transfers(
            self.actor_id
        )

        self.table.setRowCount(0)

        for entry in self.transactions:

            row = self.table.rowCount()
            self.table.insertRow(row)

            values = (
                entry["id"],
                entry["data_operacji"],
                f"{money_text(entry['kwota'])} / {money_text(entry['remaining'])}",
                entry.get("tytul") or "",
                entry.get("kontrahent") or "",
            )

            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))

                item.setFlags(
                    item.flags()
                    & ~Qt.ItemFlag.ItemIsEditable
                )

                self.table.setItem(
                    row,
                    col,
                    item,
                )

            contractor = (
                str(
                    entry.get("kontrahent") or ""
                )
                .upper()
                .strip()
            )

            filtered = []

            if contractor:

                for inv in self.invoices:

                    buyer = str(
                        inv.get("buyer") or ""
                    ).upper()

                    if (
                            contractor in buyer
                            or buyer in contractor
                    ):
                        filtered.append(inv)

            if not filtered:
                filtered = self.invoices

            suggestions = suggested_invoice_ids(
                entry,
                filtered,
            )

            combo = QComboBox(
                self.table
            )

            combo.addItem(
                "— Wybierz fakturę —",
                None,
            )

            ids = suggestions + [
                i["id"]
                for i in filtered
                if i["id"] not in suggestions
            ]

            by_id = {
                i["id"]: i
                for i in filtered
            }

            for invoice_id in ids:
                invoice = by_id[invoice_id]

                combo.addItem(
                    (
                        f"{'★ ' if invoice_id in suggestions else ''}"
                        f"{invoice['numer'] or '(bez numeru)'} "
                        f"— {invoice['buyer']} "
                        f"— saldo {money_text(invoice['balance'])} zł"
                    ),
                    invoice_id,
                )

            if suggestions:

                idx_found = combo.findData(
                    suggestions[0]
                )

                if idx_found >= 0:
                    combo.setCurrentIndex(
                        idx_found
                    )

            self.table.setCellWidget(
                row,
                5,
                combo,
            )

            from PyQt6.QtWidgets import QLineEdit

            amount = QLineEdit(
                money_text(
                    entry["remaining"]
                )
            )

            self.table.setCellWidget(
                row,
                6,
                amount,
            )

        if self.transactions:
            self.table.selectRow(0)

    def _delete_selected(self):

        rows = sorted({
            idx.row()
            for idx in self.table.selectedIndexes()
        })

        if not rows:
            QMessageBox.warning(
                self,
                "Usuwanie",
                "Nie wybrano przelewów."
            )
            return

        if QMessageBox.question(
                self,
                "Potwierdzenie",
                f"Usunąć {len(rows)} przelewów?",
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        errors = []

        for row in rows:

            try:

                delete_bank_transaction(
                    int(self.transactions[row]["id"]),
                    self.actor_id,
                )

                deleted += 1

            except Exception as exc:

                errors.append(str(exc))

        msg = f"Usunięto {deleted} przelewów."

        if errors:
            msg += "\n\n" + "\n".join(errors[:20])

        QMessageBox.information(
            self,
            "Usuwanie",
            msg,
        )

        self.refresh()

    def _confirm(self):

        row = self.table.currentRow()

        if row < 0 or row >= len(self.transactions):
            QMessageBox.warning(
                self,
                "Rozliczenie",
                "Wybierz przelew."
            )
            return

        transfer = self.transactions[row]

        combo = self.table.cellWidget(row, 5)

        invoice_id = combo.currentData()

        if invoice_id is None:
            QMessageBox.warning(
                self,
                "Rozliczenie",
                "Wybierz fakturę."
            )
            return

        invoice = next(
            i for i in self.invoices
            if i["id"] == invoice_id
        )

        amount = (
            self.table.cellWidget(row, 6)
            .text()
            .strip()
            .replace(",", ".")
        )

        msg = (
            f"Data: {transfer['data_operacji']}\n"
            f"Tytuł: {transfer.get('tytul') or ''}\n"
            f"Kontrahent: {transfer.get('kontrahent') or ''}\n\n"
            f"Faktura: {invoice['numer']}\n"
            f"{invoice['buyer']}\n\n"
            f"Kwota: {amount} PLN"
        )

        if QMessageBox.question(
                self,
                "Potwierdzenie",
                msg,
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        try:

            confirm_bank_payment(
                int(transfer["id"]),
                int(invoice_id),
                amount,
                self.actor_id,
            )

        except (
                PaymentError,
                PermissionError,
        ) as exc:

            QMessageBox.warning(
                self,
                "Rozliczenie",
                str(exc),
            )
            return

        QMessageBox.information(
            self,
            "Rozliczenie",
            "Płatność przypisana."
        )

        self.refresh()

    def _confirm_all(self):

        operations = []

        for row in range(self.table.rowCount()):

            combo = self.table.cellWidget(row, 5)

            invoice_id = combo.currentData()

            if invoice_id is None:
                continue

            amount = (
                self.table.cellWidget(row, 6)
                .text()
                .strip()
                .replace(",", ".")
            )

            operations.append(
                (
                    row,
                    invoice_id,
                    amount,
                )
            )

        if not operations:
            QMessageBox.warning(
                self,
                "Rozliczenie",
                "Brak wybranych faktur."
            )
            return

        if QMessageBox.question(
                self,
                "Potwierdzenie",
                f"Rozliczyć {len(operations)} przelewów?",
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        errors = []

        for row, invoice_id, amount in operations:

            transfer = self.transactions[row]

            try:

                confirm_bank_payment(
                    int(transfer["id"]),
                    int(invoice_id),
                    amount,
                    self.actor_id,
                )

            except Exception as exc:

                errors.append(
                    f"{transfer['id']}: {exc}"
                )

        if errors:
            QMessageBox.warning(
                self,
                "Błędy",
                "\n".join(errors[:20]),
            )

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
