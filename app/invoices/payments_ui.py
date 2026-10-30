"""Potwierdzanie przelewów ING i płatności gotówkowych w GUI faktur."""
from __future__ import annotations

import logging
from decimal import Decimal, DecimalException

from PyQt6.QtCore import QDate, QSettings, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.db.dao_payments import (
    PaymentError, confirm_bank_payment, import_mt940_file, list_invoice_choices,
    list_unsettled_transfers, record_cash_payment, suggested_invoice_ids, delete_bank_transaction,
    invoice_choices_for_transaction, match_invoice_contractor,
)

logger = logging.getLogger(__name__)


def money_text(value) -> str:
    return f"{Decimal(str(value)):.2f}"


def _payment_amount(text: str) -> Decimal:
    try:
        amount = Decimal(text.replace(" ", "").replace(",", "."))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise PaymentError("Podaj dodatnią kwotę z najwyżej dwoma miejscami po przecinku.")
        return amount
    except DecimalException as exc:
        raise PaymentError("Nieprawidłowa kwota płatności.") from exc


class BankPaymentReview(QDialog):
    """Bez automatycznych księgowań — użytkownik zatwierdza każdą alokację."""
    def __init__(self, actor_id: int, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.invoices = []
        self.transactions = []
        self.setWindowTitle("Uzgadnianie przelewów ING MT940 z fakturami")
        self.resize(1400, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Wybierz przelew, sprawdź proponowaną fakturę i kwotę, "
            "a następnie zatwierdź rozliczenie. Żaden przelew nie jest przypisywany automatycznie."
        ))
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "ID", "Data", "Wpływ / pozostało (PLN)", "Tytuł przelewu",
            "Przypisz do faktury", "Kwota rozliczenia (PLN)", "Rozpoznany kontrahent", "Filtr faktur",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(3, 330)
        self.table.setColumnWidth(4, 330)
        self.table.setColumnWidth(6, 230)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.btn_import = QPushButton("Importuj ING MT940")
        self.btn_import.clicked.connect(self._import_file)
        buttons.addWidget(self.btn_import)
        buttons.addWidget(QLabel("Kodowanie:"))
        self.encoding = QComboBox(self)
        for label, encoding in (("Auto (UTF-8 / Windows-1250)", ""), ("UTF-8", "utf-8-sig"),
                                ("Windows-1250", "cp1250"), ("ISO-8859-2", "iso8859_2"), ("CP852", "cp852")):
            self.encoding.addItem(label, encoding)
        self.settings = QSettings("Biuro", "OfficeManager")
        self.encoding.setCurrentIndex(max(0, self.encoding.findData(str(self.settings.value("mt940/encoding", "") or ""))))
        self.encoding.currentIndexChanged.connect(self._remember_encoding)
        self.encoding.setToolTip("Jeśli polskie znaki są błędne, wybierz kodowanie zgodne z eksportem ING.")
        buttons.addWidget(self.encoding)
        self.btn_confirm = QPushButton("Potwierdź rozliczenie wybranego przelewu")
        self.btn_confirm.clicked.connect(self._confirm)
        buttons.addWidget(self.btn_confirm)
        self.btn_confirm_all = QPushButton("Rozlicz wybrane dopasowania")
        self.btn_confirm_all.clicked.connect(self._confirm_all)
        buttons.addWidget(self.btn_confirm_all)
        self.btn_delete = QPushButton("Usuń przelew")
        self.btn_delete.clicked.connect(self._delete_selected)
        buttons.addWidget(self.btn_delete)
        refresh = QPushButton("Odśwież")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        layout.addLayout(buttons)
        self.refresh()

    def _remember_encoding(self):
        self.settings.setValue("mt940/encoding", self.encoding.currentData())
        self.settings.sync()

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import wyciągu ING MT940", "",
            "Wyciągi MT940 (*.sta *.mt940 *.txt *.940);;Wszystkie pliki (*)",
        )
        if not path:
            return
        try:
            report = import_mt940_file(path, self.actor_id, encoding=self.encoding.currentData() or None)
        except (PaymentError, OSError, PermissionError) as exc:
            QMessageBox.warning(self, "Wyciąg MT940", str(exc))
            return
        except Exception:
            logger.exception("Błąd importu wyciągu MT940")
            QMessageBox.warning(self, "Wyciąg MT940", "Nie udało się zaimportować wyciągu. Szczegóły w logach.")
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
        try:
            invoices = list_invoice_choices(self.actor_id)
            transactions = list_unsettled_transfers(self.actor_id)
        except (PaymentError, PermissionError) as exc:
            QMessageBox.warning(self, "Rozliczenie", str(exc))
            return
        except Exception:
            logger.exception("Błąd odświeżania rozliczeń bankowych")
            QMessageBox.warning(self, "Rozliczenie", "Nie udało się odczytać rozliczeń. Szczegóły w logach.")
            return
        self.invoices, self.transactions = invoices, transactions
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
            match = match_invoice_contractor(entry, self.invoices)
            contractor_item = QTableWidgetItem(match.get("resolved_contractor") or entry.get("kontrahent") or "Nie rozpoznano jednoznacznie")
            contractor_item.setFlags(contractor_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            contractor_item.setToolTip(match.get("reason") or "")
            self.table.setItem(idx, 6, contractor_item)
            choices = QComboBox(self.table)
            # Sugestia jest tylko wizualna; nie wybieramy faktury automatycznie.
            self.table.setCellWidget(idx, 4, choices)
            amount = QLineEdit(money_text(entry["remaining"]), self.table)
            self.table.setCellWidget(idx, 5, amount)
            override = QCheckBox("Wszystkie faktury", self.table)
            override.setChecked(not match.get("confident"))
            override.setEnabled(bool(match.get("confident")))
            override.setToolTip(match.get("reason") or "Nie rozpoznano klienta — wybierz fakturę ręcznie.")
            self.table.setCellWidget(idx, 7, override)
            self._populate_invoice_choices(idx)
            override.toggled.connect(lambda _checked, row=idx: self._override_filter_changed(row))
            choices.currentIndexChanged.connect(lambda _index, row=idx: self._invoice_choice_changed(row))
            amount.textEdited.connect(lambda _text, row=idx: self.table.setCurrentCell(row, 5))
        if self.transactions:
            self.table.selectRow(0)

    def _populate_invoice_choices(self, row):
        entry = self.transactions[row]
        combo = self.table.cellWidget(row, 4)
        selected = combo.currentData()
        invoices = (self.invoices if self.table.cellWidget(row, 7).isChecked()
                    else invoice_choices_for_transaction(entry, self.invoices))
        suggestions = suggested_invoice_ids(entry, invoices)
        by_id = {invoice["id"]: invoice for invoice in invoices}
        ordering = suggestions + [invoice["id"] for invoice in invoices if invoice["id"] not in suggestions]
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("— Wybierz fakturę (wymagane potwierdzenie) —", None)
        for invoice_id in ordering:
            invoice = by_id[invoice_id]
            prefix = "★ " if invoice_id in suggestions else ""
            combo.addItem(
                f"{prefix}{invoice['numer'] or '(bez numeru)'} — {invoice['buyer']} — saldo {money_text(invoice['balance'])} zł",
                invoice_id,
            )
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.blockSignals(False)

    def _override_filter_changed(self, row):
        self._populate_invoice_choices(row)
        self.table.setCurrentCell(row, 4)

    def _invoice_choice_changed(self, row):
        self.table.setCurrentCell(row, 4)
        invoice_id = self.table.cellWidget(row, 4).currentData()
        invoice = next((item for item in self.invoices if item["id"] == invoice_id), None)
        if invoice is not None:
            amount = min(Decimal(str(self.transactions[row]["remaining"])), Decimal(str(invoice["balance"])))
            self.table.cellWidget(row, 5).setText(money_text(amount))

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
        invoice = next((i for i in self.invoices if i["id"] == invoice_id), None)
        if invoice is None:
            QMessageBox.warning(self, "Rozliczenie", "Faktura nie jest już dostępna. Odśwież listę.")
            return
        try:
            amount = _payment_amount(self.table.cellWidget(selected, 5).text().strip())
            if amount > min(Decimal(str(transfer["remaining"])), Decimal(str(invoice["balance"]))):
                raise PaymentError("Kwota przekracza pozostałe saldo faktury lub przelewu.")
        except PaymentError as exc:
            QMessageBox.warning(self, "Rozliczenie", str(exc))
            return
        msg = (
            f"Czy zatwierdzić przypisanie przelewu?\n\n"
            f"Data: {transfer['data_operacji']}\n"
            f"Tytuł: {transfer.get('tytul') or ''}\n"
            f"Kontrahent: {transfer.get('kontrahent') or ''}\n"
            f"Faktura: {invoice['numer']} — {invoice['buyer']}\n"
            f"Kwota przypisania: {amount} PLN"
        )
        if QMessageBox.question(
            self, "Potwierdzenie rozliczenia", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            confirm_bank_payment(int(transfer["id"]), int(invoice_id), amount, self.actor_id)
        except (PaymentError, PermissionError) as exc:
            QMessageBox.warning(self, "Rozliczenie", str(exc))
            return
        except Exception:
            logger.exception("Błąd zapisu rozliczenia bankowego")
            QMessageBox.warning(self, "Rozliczenie", "Nie udało się zapisać rozliczenia. Szczegóły w logach.")
            return
        QMessageBox.information(self, "Rozliczenie", "Płatność przypisana do faktury.")
        self.refresh()

    def _confirm_all(self):
        operations = []
        by_invoice = {invoice["id"]: invoice for invoice in self.invoices}
        totals = {}
        try:
            for row, transfer in enumerate(self.transactions):
                invoice_id = self.table.cellWidget(row, 4).currentData()
                if invoice_id is None:
                    continue
                invoice = by_invoice.get(invoice_id)
                if invoice is None:
                    raise PaymentError("Faktura nie jest już dostępna. Odśwież listę.")
                amount = _payment_amount(self.table.cellWidget(row, 5).text().strip())
                if amount > Decimal(str(transfer["remaining"])):
                    raise PaymentError(f"Kwota przekracza saldo przelewu #{transfer['id']}.")
                totals[invoice_id] = totals.get(invoice_id, Decimal("0")) + amount
                if totals[invoice_id] > Decimal(str(invoice["balance"])):
                    raise PaymentError(f"Suma wybranych wpłat przekracza saldo faktury {invoice['numer']}.")
                operations.append((transfer, invoice, amount))
        except PaymentError as exc:
            QMessageBox.warning(self, "Rozliczenie", str(exc))
            return
        if not operations:
            QMessageBox.warning(self, "Rozliczenie", "Najpierw wybierz faktury dla przelewów do rozliczenia.")
            return
        details = "\n".join(
            f"Przelew #{transfer['id']} ({transfer['data_operacji']}) → {invoice['numer']} — {invoice['buyer']}: {amount:.2f} PLN"
            for transfer, invoice, amount in operations
        )
        if QMessageBox.question(
            self, "Potwierdź rozliczenia", f"Zapisać {len(operations)} rozliczeń?\n\n{details}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        errors, saved = [], 0
        for transfer, invoice, amount in operations:
            try:
                confirm_bank_payment(int(transfer["id"]), int(invoice["id"]), amount, self.actor_id)
            except (PaymentError, PermissionError) as exc:
                errors.append(f"Przelew #{transfer['id']}: {exc}")
            except Exception:
                logger.exception("Błąd zbiorczego rozliczenia przelewu %s", transfer["id"])
                errors.append(f"Przelew #{transfer['id']}: błąd zapisu, szczegóły w logach.")
            else:
                saved += 1
        message = f"Zapisano rozliczeń: {saved} z {len(operations)}."
        if errors:
            QMessageBox.warning(self, "Rozliczenie", message + "\n\n" + "\n".join(errors[:20]))
        else:
            QMessageBox.information(self, "Rozliczenie", message)
        self.refresh()

    def _delete_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.warning(self, "Usuwanie", "Nie wybrano przelewów.")
            return
        if QMessageBox.question(
            self, "Usuń przelewy", f"Usunąć zaznaczone przelewy ({len(rows)})?\nRozliczone operacje nie zostaną usunięte.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        errors, deleted = [], 0
        for row in rows:
            transfer_id = int(self.transactions[row]["id"])
            try:
                delete_bank_transaction(transfer_id, self.actor_id)
            except (PaymentError, PermissionError) as exc:
                errors.append(f"Przelew #{transfer_id}: {exc}")
            except Exception:
                logger.exception("Błąd usunięcia przelewu %s", transfer_id)
                errors.append(f"Przelew #{transfer_id}: błąd usuwania, szczegóły w logach.")
            else:
                deleted += 1
        message = f"Usunięto przelewów: {deleted} z {len(rows)}."
        if errors:
            QMessageBox.warning(self, "Usuwanie", message + "\n\n" + "\n".join(errors[:20]))
        else:
            QMessageBox.information(self, "Usuwanie", message)
        self.refresh()


def record_cash_in_dialog(invoice: dict, actor_id: int, parent=None) -> bool:
    from app.db.dao_payments import get_invoice_payment_summary
    try:
        summary = get_invoice_payment_summary(int(invoice["id"]), actor_id)
    except (PaymentError, PermissionError) as exc:
        QMessageBox.warning(parent, "Gotówka", str(exc))
        return False
    except Exception:
        logger.exception("Błąd odczytu salda faktury")
        QMessageBox.warning(parent, "Gotówka", "Nie udało się odczytać salda faktury. Szczegóły w logach.")
        return False
    if summary["balance"] <= 0:
        QMessageBox.information(parent, "Gotówka", "Ta faktura jest już w całości rozliczona.")
        return False
    form = QDialog(parent)
    form.setWindowTitle("Zapłata gotówką")
    layout = QFormLayout(form)
    date_input = QDateEdit(QDate.currentDate(), form)
    date_input.setCalendarPopup(True)
    date_input.setDisplayFormat("dd/MM/yyyy")
    amount_edit = QLineEdit(form)
    amount_edit.setText(money_text(summary["balance"]))
    layout.addRow("Faktura:", QLabel(str(invoice.get("numer") or invoice["id"]), form))
    layout.addRow("Data otrzymania gotówki:", date_input)
    layout.addRow("Pozostało do zapłaty:", QLabel(f"{money_text(summary['balance'])} PLN", form))
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
    try:
        amount = _payment_amount(amount_edit.text().strip())
        if amount > summary["balance"]:
            raise PaymentError("Kwota przekracza pozostałe saldo faktury.")
    except PaymentError as exc:
        QMessageBox.warning(parent, "Gotówka", str(exc))
        return False
    if QMessageBox.question(
        parent, "Potwierdź płatność gotówkową",
        f"Zarejestrować wpłatę {amount} zł z dnia {date_input.date().toString('dd/MM/yyyy')} "
        f"na fakturę {invoice.get('numer') or invoice['id']}?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
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
    except Exception:
        logger.exception("Błąd zapisu płatności gotówkowej")
        QMessageBox.warning(parent, "Gotówka", "Nie udało się zapisać płatności. Szczegóły w logach.")
        return False
    return True
