"""Potwierdzanie przelewów ING i płatności gotówkowych w GUI faktur."""
from __future__ import annotations

import logging
import traceback
from decimal import Decimal, DecimalException

from PyQt6.QtCore import QDate, QSettings, Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QCompleter
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.db.dao_payments import (
    bank_import_summary, PaymentError, confirm_bank_payment, import_mt940_file, list_company_choices, list_invoice_choices,
    list_unsettled_transfers, record_manual_payment, record_cash_payment, suggested_invoice_ids, delete_bank_transaction,
    invoice_choices_for_transaction, match_invoice_contractor,
    invoice_contractor_key,
    bank_transfer_block_reason, apply_invoice_correction,
)
from app.utils.view_state import ItemViewState

logger = logging.getLogger(__name__)


def _contractor_data(invoice: dict) -> str:
    kind, value = invoice_contractor_key(invoice)
    return f"{kind}:{value}"


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


class InvoiceChoiceCombo(QComboBox):
    def showPopup(self):
        if self.property('deferred_choices'):
            self.load_all()
        super().showPopup()


class BankPaymentReview(QDialog):
    """Bez automatycznych księgowań — użytkownik zatwierdza każdą alokację."""
    COL_ID = 0
    COL_DATE = 1
    COL_AMOUNT = 2
    COL_BANK_CONTRACTOR = 3
    COL_TITLE = 4
    COL_INVOICE = 5
    COL_SETTLEMENT = 6
    COL_CONTRACTOR = 7
    COL_FILTER = 8
    COL_MODE = 9

    def __init__(self, actor_id: int, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.invoices = []
        self.companies = []
        self.transactions = []
        self._bank_limit = 100
        self.setWindowTitle("Uzgadnianie przelewów ING MT940 z fakturami")
        self.resize(1600, 1000)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Wybierz przelew, sprawdź proponowaną fakturę i kwotę, "
            "a następnie zatwierdź rozliczenie. Żaden przelew nie jest przypisywany automatycznie."
        ))
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "ID", "Data", "Wpływ / pozostało (PLN)", "Nadawca z wyciągu", "Tytuł przelewu",
            "Przypisz do faktury", "Kwota rozliczenia (PLN)", "Wybrany kontrahent", "Filtr faktur", "Sposób uzgodnienia",
        ])
        self.table.setColumnHidden(self.COL_ID, True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(self.COL_BANK_CONTRACTOR, 240)
        self.table.setColumnWidth(self.COL_TITLE, 330)
        self.table.setColumnWidth(self.COL_INVOICE, 330)
        self.table.setColumnWidth(self.COL_CONTRACTOR, 250)
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
        self.btn_correction = QPushButton('Rozlicz korektą…')
        self.btn_correction.clicked.connect(self._offset_correction)
        buttons.addWidget(self.btn_correction)
        self.btn_delete = QPushButton("Usuń przelew")
        self.btn_delete.clicked.connect(self._delete_selected)
        buttons.addWidget(self.btn_delete)
        refresh = QPushButton("Odśwież")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        layout.addLayout(buttons)
        self.all_imports = QCheckBox("Pokaż wszystkie zaimportowane operacje, także rozliczone i wypływy")
        self.all_imports.setChecked(False)
        self.all_imports.toggled.connect(self.refresh)
        layout.addWidget(self.all_imports)
        self.more_imports = QPushButton("Wczytaj kolejne 100 operacji")
        self.more_imports.clicked.connect(self._load_more_imports)
        layout.addWidget(self.more_imports)
        self.data_status = QLabel()
        self.data_status.setWordWrap(True)
        layout.addWidget(self.data_status)
        self.refresh()

    def _load_more_imports(self):
        self._bank_limit += 100
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
            error = QMessageBox(self)
            error.setIcon(QMessageBox.Icon.Warning)
            error.setWindowTitle("Wyciąg MT940")
            error.setText("Nie udało się zaimportować wyciągu.")
            error.setInformativeText("Rozwiń szczegóły i skopiuj treść błędu, aby ustalić przyczynę.")
            error.setDetailedText(traceback.format_exc())
            error.exec()
            return
        QMessageBox.information(
            self, "Wyciąg MT940",
            f"Transakcje w pliku: {report['all']}\n"
            f"Nowe: {report['new']}\n"
            f"Już importowane: {report['duplicates']}\n"
            f"Pominięte przelewy własne: {report.get('own', 0)}\n\n"
            "Operacje wymagają potwierdzenia na liście przed rozliczeniem.",
        )
        self.refresh()

    def refresh(self):
        try:
            invoices = list_invoice_choices(self.actor_id)
            transactions = list_unsettled_transfers(self.actor_id, include_all=self.all_imports.isChecked(), limit=self._bank_limit)
            summary = bank_import_summary(self.actor_id)
            try:
                self.companies = list_company_choices(self.actor_id)
            except Exception:
                logger.exception("Nie udało się wczytać listy firm do rozliczeń")
                self.companies = []
        except (PaymentError, PermissionError) as exc:
            QMessageBox.warning(self, "Rozliczenie", str(exc))
            return
        except Exception:
            logger.exception("Błąd odświeżania rozliczeń bankowych")
            QMessageBox.warning(self, "Rozliczenie", "Nie udało się odczytać rozliczeń. Szczegóły w logach.")
            return
        from app.config import get_database
        self.data_status.setText(
            f"Wspólna baza: {get_database()} · schemat: {summary['schema']}. "
            f"Zaimportowane operacje: {summary['total']}; na liście: {len(transactions)}. "
            "Wpływy PLN rozliczaj z należnościami, wypływy ze zwrotami na korektach ujemnych. "
            "Ręczną wpłatę można powiązać bez jej powielania.")
        self.more_imports.setVisible(len(transactions) >= self._bank_limit)
        saved_choices = self._snapshot_choices()
        state = ItemViewState(self.table, lambda index: index.data()) if self.table.rowCount() else None
        self.invoices, self.transactions = invoices, transactions
        old_model = getattr(self, '_contractor_model', None)
        self._contractor_model = QStandardItemModel(self)
        first = QStandardItem('Wszyscy kontrahenci')
        self._contractor_model.appendRow(first)
        seen = set()
        for invoice in sorted(invoices, key=lambda item: str(item.get('buyer') or '').casefold()):
            key = invoice_contractor_key(invoice)
            if key not in seen:
                seen.add(key)
                item = QStandardItem(invoice.get('buyer') or '(bez nazwy)')
                item.setData(_contractor_data(invoice), Qt.ItemDataRole.UserRole)
                self._contractor_model.appendRow(item)
        for company in self.companies:
            key = (company.get('kind', 'firma'), company['id'])
            if key not in seen:
                seen.add(key)
                item = QStandardItem(company['buyer'])
                item.setData(f'{key[0]}:{key[1]}', Qt.ItemDataRole.UserRole)
                self._contractor_model.appendRow(item)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0)
        for entry in self.transactions:
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            values = (
                entry["id"], entry["data_operacji"],
                f"{money_text(entry['kwota'])} / {money_text(entry['remaining'])}",
                entry.get("kontrahent") or "(brak nazwy nadawcy)",
                entry.get("tytul") or entry.get("referencja") or "",
            )
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(idx, col, cell)
            match = match_invoice_contractor(entry, self.invoices)
            contractor = QComboBox(self.table)
            contractor.setEditable(True)
            contractor.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            contractor.setToolTip((match.get("reason") or "") + " Wybór można zmienić ręcznie.")
            contractor.setModel(self._contractor_model)
            if match.get("confident"):
                kind, value = match["company_key"]
                contractor.setCurrentIndex(max(0, contractor.findData(f"{kind}:{value}")))
            else:
                contractor.setCurrentText(entry.get("kontrahent") or "Wszyscy kontrahenci")
            completer = contractor.completer()
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self.table.setCellWidget(idx, self.COL_CONTRACTOR, contractor)
            choices = InvoiceChoiceCombo(self.table)
            choices.load_all = lambda row=idx: self._populate_invoice_choices(row, full=True)
            choices.setEditable(True)
            choices.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            choices.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            choices.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            choices.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            choices.lineEdit().textEdited.connect(lambda _text, row=idx: self._load_full_choices(row))
            # Sugestia (★) jest podstawiana, ale nie rozliczana bez zatwierdzenia.
            self.table.setCellWidget(idx, self.COL_INVOICE, choices)
            amount = QLineEdit(money_text(entry["remaining"]), self.table)
            self.table.setCellWidget(idx, self.COL_SETTLEMENT, amount)
            override = QCheckBox("Wszystkie faktury", self.table)
            override.setChecked(not match.get("confident"))
            override.setEnabled(True)
            override.setToolTip(match.get("reason") or "Nie rozpoznano klienta — wybierz fakturę ręcznie.")
            self.table.setCellWidget(idx, self.COL_FILTER, override)
            mode = QComboBox(self.table)
            self.table.setCellWidget(idx, self.COL_MODE, mode)
            self._populate_invoice_choices(idx)
            self._restore_choices(idx, saved_choices.get(entry["id"]))
            self._preselect_suggestion(idx)
            override.toggled.connect(lambda _checked, row=idx: self._override_filter_changed(row))
            contractor.currentIndexChanged.connect(lambda _index, row=idx: self._contractor_changed(row))
            contractor.lineEdit().editingFinished.connect(lambda row=idx: self._contractor_text_entered(row))
            choices.currentIndexChanged.connect(lambda _index, row=idx: self._invoice_choice_changed(row))
            mode.currentIndexChanged.connect(lambda _index, row=idx: self._mode_changed(row))
            amount.textEdited.connect(lambda _text, row=idx: self.table.setCurrentCell(row, self.COL_SETTLEMENT))
            reason = bank_transfer_block_reason(entry)
            if reason:
                choices.blockSignals(True)
                choices.clear()
                choices.addItem(reason, None)
                choices.blockSignals(False)
                for widget in (choices, amount, contractor, override, mode):
                    widget.setEnabled(False)
                self.table.item(idx, self.COL_AMOUNT).setText(
                    f"{money_text(entry['kwota'])} {entry.get('waluta', 'PLN')} / pozostało {money_text(entry['remaining'])}")
        self.table.setUpdatesEnabled(True)
        if state is not None:
            state.restore()
        elif self.transactions:
            self.table.selectRow(0)
        if old_model is not None:
            old_model.deleteLater()

    def _snapshot_choices(self):
        """Zapamiętuje wybory użytkownika, by odświeżenie po jednym rozliczeniu ich nie kasowało."""
        saved = {}
        for row, entry in enumerate(self.transactions):
            invoice = self.table.cellWidget(row, self.COL_INVOICE)
            contractor = self.table.cellWidget(row, self.COL_CONTRACTOR)
            amount = self.table.cellWidget(row, self.COL_SETTLEMENT)
            override = self.table.cellWidget(row, self.COL_FILTER)
            if None in (invoice, contractor, amount, override):
                continue
            partly_settled = Decimal(str(entry["kwota"])) != Decimal(str(entry["remaining"]))
            if invoice.currentData() is None and not partly_settled:
                continue
            if (invoice.currentData() == invoice.property("auto_selected") and not partly_settled
                    and amount.text() == invoice.property("auto_amount")
                    and self.table.cellWidget(row, self.COL_MODE).currentData() is None):
                continue
            saved[entry["id"]] = {
                "invoice": invoice.currentData(), "contractor": contractor.currentData(),
                "amount": amount.text(), "override": override.isChecked(),
                'mode': self.table.cellWidget(row, self.COL_MODE).currentData(),
            }
        return saved

    def _restore_choices(self, row, saved):
        if not saved:
            return
        invoice_id = saved["invoice"]
        contractor = self.table.cellWidget(row, self.COL_CONTRACTOR)
        index = contractor.findData(saved["contractor"])
        if index >= 0:
            contractor.setCurrentIndex(index)
        self.table.cellWidget(row, self.COL_FILTER).setChecked(saved["override"])
        self._populate_invoice_choices(row)
        if invoice_id is None or not any(item["id"] == invoice_id for item in self.invoices):
            return
        combo = self.table.cellWidget(row, self.COL_INVOICE)
        index = combo.findData(invoice_id)
        if index < 0:
            return
        combo.setCurrentIndex(index)
        self._populate_modes(row)
        mode = self.table.cellWidget(row, self.COL_MODE)
        mode.setCurrentIndex(max(0, mode.findData(saved.get('mode'))))
        invoice = next(item for item in self.invoices if item["id"] == invoice_id)
        remaining = Decimal(str(self.transactions[row]["remaining"]))
        try:
            wanted = Decimal(saved["amount"].replace(",", "."))
        except DecimalException:
            wanted = remaining
        limit = min(remaining, self._mode_limit(row, invoice))
        self.table.cellWidget(row, self.COL_SETTLEMENT).setText(money_text(min(wanted, limit) if wanted > 0 else limit))

    def _populate_invoice_choices(self, row, *, full=False):
        entry = self.transactions[row]
        combo = self.table.cellWidget(row, self.COL_INVOICE)
        selected = combo.currentData()
        contractor_key = self.table.cellWidget(row, self.COL_CONTRACTOR).currentData()
        if self.table.cellWidget(row, self.COL_FILTER).isChecked():
            invoices = self.invoices
        elif contractor_key is not None:
            invoices = [item for item in self.invoices if _contractor_data(item) == contractor_key]
        else:
            invoices = invoice_choices_for_transaction(entry, self.invoices)
        refund = Decimal(str(entry['kwota'])) < 0
        invoices = [item for item in invoices if (item.get('rodzaj') == 'korekta'
                    and Decimal(str(item.get('kwota_brutto', 0))) < 0) == refund]
        suggestions = suggested_invoice_ids(entry, invoices)
        by_id = {invoice["id"]: invoice for invoice in invoices}
        ordering = suggestions + [invoice["id"] for invoice in invoices if invoice["id"] not in suggestions]
        deferred = not full and len(ordering) > 50
        if deferred:
            ordering = list(dict.fromkeys(suggestions + ([selected] if selected in by_id else [])))
        combo.setProperty('deferred_choices', deferred)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(f'— Rozwiń / wyszukaj fakturę ({len(invoices)}) —' if deferred else '— Wybierz fakturę (wymagane potwierdzenie) —', None)
        for invoice_id in ordering:
            invoice = by_id[invoice_id]
            prefix = "★ " if invoice_id in suggestions else ""
            combo.addItem(
                f"{prefix}{invoice['numer'] or '(bez numeru)'} — {invoice['buyer']} — saldo {money_text(invoice['balance'])} zł"
                + ('; dostępna wpłata ręczna' if invoice.get('manual_payments') else ''),
                invoice_id,
            )
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.blockSignals(False)
        self._populate_modes(row)

    def _populate_modes(self, row):
        mode = self.table.cellWidget(row, self.COL_MODE)
        invoice_id = self.table.cellWidget(row, self.COL_INVOICE).currentData()
        old = mode.currentData() if mode.property('invoice_id') == invoice_id else None
        invoice = next((item for item in self.invoices if item['id'] == invoice_id), None)
        mode.blockSignals(True)
        mode.clear()
        if invoice is None or Decimal(str(invoice['balance'])) > 0:
            mode.addItem('Nowa wpłata / zwrot', None)
        for payment in invoice.get('manual_payments', []) if invoice else []:
            mode.addItem(f'Powiąż ręczną: {payment["data_zaplaty"]} — {money_text(payment["kwota"])} zł', payment['id'])
        mode.setProperty('invoice_id', invoice_id)
        mode.setCurrentIndex(max(0, mode.findData(old)))
        mode.blockSignals(False)

    def _load_full_choices(self, row):
        combo = self.table.cellWidget(row, self.COL_INVOICE)
        if combo.property('deferred_choices'):
            text = combo.currentText()
            self._populate_invoice_choices(row, full=True)
            combo.setEditText(text)

    def _mode_limit(self, row, invoice):
        manual_id = self.table.cellWidget(row, self.COL_MODE).currentData()
        if manual_id is not None:
            payment = next((item for item in invoice.get('manual_payments', []) if item['id'] == manual_id), None)
            return Decimal(str(payment['kwota'])) if payment else Decimal('0')
        return max(Decimal(str(invoice['balance'])), Decimal('0'))

    def _mode_changed(self, row):
        self.table.setCurrentCell(row, self.COL_MODE)
        invoice_id = self.table.cellWidget(row, self.COL_INVOICE).currentData()
        invoice = next((item for item in self.invoices if item['id'] == invoice_id), None)
        if invoice is not None:
            self.table.cellWidget(row, self.COL_SETTLEMENT).setText(money_text(
                min(Decimal(str(self.transactions[row]['remaining'])), self._mode_limit(row, invoice))))

    def _override_filter_changed(self, row):
        self._populate_invoice_choices(row)
        self.table.setCurrentCell(row, self.COL_INVOICE)

    def _contractor_text_entered(self, row):
        combo = self.table.cellWidget(row, self.COL_CONTRACTOR)
        text = combo.currentText().strip()
        if not text:
            return
        index = combo.findText(text, Qt.MatchFlag.MatchFixedString)
        if index < 0:
            index = combo.findText(text, Qt.MatchFlag.MatchContains)
        if index >= 0 and index != combo.currentIndex():
            combo.setCurrentIndex(index)

    def _contractor_changed(self, row):
        override = self.table.cellWidget(row, self.COL_FILTER)
        if self.table.cellWidget(row, self.COL_CONTRACTOR).currentData() is not None:
            override.setChecked(False)
        self._populate_invoice_choices(row)
        self.table.setCurrentCell(row, self.COL_CONTRACTOR)

    def _invoice_choice_changed(self, row):
        self.table.setCurrentCell(row, self.COL_INVOICE)
        self.table.cellWidget(row, self.COL_INVOICE).setProperty("auto_selected", None)
        invoice_id = self.table.cellWidget(row, self.COL_INVOICE).currentData()
        invoice = next((item for item in self.invoices if item["id"] == invoice_id), None)
        if invoice is not None:
            self._populate_modes(row)
            amount = min(Decimal(str(self.transactions[row]["remaining"])), self._mode_limit(row, invoice))
            self.table.cellWidget(row, self.COL_SETTLEMENT).setText(money_text(amount))

    def _preselect_suggestion(self, row):
        """Podstawia fakturę oznaczoną gwiazdką; rozliczenie nadal wymaga zatwierdzenia."""
        combo = self.table.cellWidget(row, self.COL_INVOICE)
        if combo.currentData() is not None or combo.count() < 2 or not combo.itemText(1).startswith("★"):
            return
        invoice = next((item for item in self.invoices if item["id"] == combo.itemData(1)), None)
        if invoice is None:
            return
        combo.setCurrentIndex(1)
        combo.setProperty("auto_selected", combo.currentData())
        self._populate_modes(row)
        amount = min(Decimal(str(self.transactions[row]["remaining"])), self._mode_limit(row, invoice))
        self.table.cellWidget(row, self.COL_SETTLEMENT).setText(money_text(amount))
        combo.setProperty("auto_amount", money_text(amount))

    def _confirm(self):
        selected = self.table.currentRow()
        if selected < 0 or selected >= len(self.transactions):
            QMessageBox.warning(self, "Rozliczenie", "Wybierz przelew.")
            return
        transfer = self.transactions[selected]
        combo = self.table.cellWidget(selected, self.COL_INVOICE)
        invoice_id = combo.currentData()
        if invoice_id is None:
            QMessageBox.warning(self, "Rozliczenie", "Wybierz konkretną fakturę.")
            return
        invoice = next((i for i in self.invoices if i["id"] == invoice_id), None)
        if invoice is None:
            QMessageBox.warning(self, "Rozliczenie", "Faktura nie jest już dostępna. Odśwież listę.")
            return
        try:
            amount = _payment_amount(self.table.cellWidget(selected, self.COL_SETTLEMENT).text().strip())
            reason = bank_transfer_block_reason(transfer)
            if reason:
                raise PaymentError(reason)
            if amount > min(Decimal(str(transfer["remaining"])), self._mode_limit(selected, invoice)):
                raise PaymentError("Kwota przekracza pozostałe saldo faktury lub przelewu.")
        except PaymentError as exc:
            QMessageBox.warning(self, "Rozliczenie", str(exc))
            return
        manual_id = self.table.cellWidget(selected, self.COL_MODE).currentData()
        legacy = 'kierunek' in transfer and transfer['kierunek'] in (None, '')
        msg = (
            f"Czy zatwierdzić przypisanie przelewu?\n\n"
            f"Data: {transfer['data_operacji']}\n"
            f"Tytuł: {transfer.get('tytul') or ''}\n"
            f"Kontrahent: {transfer.get('kontrahent') or ''}\n"
            f"Faktura: {invoice['numer']} — {invoice['buyer']}\n"
            f"Kwota przypisania: {amount} PLN"
        )
        if manual_id is not None:
            msg += '\nPowiązanie istniejącej ręcznej wpłaty — suma zapłat faktury nie wzrośnie.'
        if legacy:
            msg += '\nStarszy import bez kierunku: potwierdzasz, że to zwykły wpływ / zwrot zgodny ze znakiem kwoty.'
        if QMessageBox.question(
            self, "Potwierdzenie rozliczenia", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._save_operation(transfer, invoice_id, amount, manual_id, legacy)
        except (PaymentError, PermissionError) as exc:
            QMessageBox.warning(self, "Rozliczenie", str(exc))
            return
        except Exception:
            logger.exception("Błąd zapisu rozliczenia bankowego")
            QMessageBox.warning(self, "Rozliczenie", "Nie udało się zapisać rozliczenia. Szczegóły w logach.")
            return
        QMessageBox.information(self, "Rozliczenie", "Uzgodniono operację z fakturą.")
        self.refresh()

    def _save_operation(self, transfer, invoice_id, amount, manual_id, legacy):
        options = {}
        if manual_id is not None:
            options['manual_payment_id'] = manual_id
        if legacy:
            options['allow_legacy_credit'] = True
        confirm_bank_payment(int(transfer['id']), int(invoice_id), amount, self.actor_id, **options)

    def _confirm_all(self):
        operations = []
        by_invoice = {invoice["id"]: invoice for invoice in self.invoices}
        totals = {}
        try:
            for row, transfer in enumerate(self.transactions):
                invoice_id = self.table.cellWidget(row, self.COL_INVOICE).currentData()
                if invoice_id is None:
                    continue
                invoice = by_invoice.get(invoice_id)
                if invoice is None:
                    raise PaymentError("Faktura nie jest już dostępna. Odśwież listę.")
                amount = _payment_amount(self.table.cellWidget(row, self.COL_SETTLEMENT).text().strip())
                if bank_transfer_block_reason(transfer):
                    raise PaymentError(bank_transfer_block_reason(transfer))
                if amount > Decimal(str(transfer["remaining"])):
                    raise PaymentError(f"Kwota przekracza saldo przelewu #{transfer['id']}.")
                manual_id = self.table.cellWidget(row, self.COL_MODE).currentData()
                key = invoice_id, manual_id
                totals[key] = totals.get(key, Decimal("0")) + amount
                if totals[key] > self._mode_limit(row, invoice):
                    raise PaymentError(f"Suma wybranych wpłat przekracza saldo faktury {invoice['numer']}.")
                operations.append((transfer, invoice, amount, manual_id, 'kierunek' in transfer and transfer['kierunek'] in (None, '')))
        except PaymentError as exc:
            QMessageBox.warning(self, "Rozliczenie", str(exc))
            return
        if not operations:
            QMessageBox.warning(self, "Rozliczenie", "Najpierw wybierz faktury dla przelewów do rozliczenia.")
            return
        details = "\n".join(
            f"Przelew #{transfer['id']} ({transfer['data_operacji']}) → {invoice['numer']} — {invoice['buyer']}: {amount:.2f} PLN"
            + (' (powiązanie ręcznej wpłaty)' if manual_id is not None else '')
            + (' (starszy import — potwierdź kierunek zgodny ze znakiem kwoty)' if legacy else '')
            for transfer, invoice, amount, manual_id, legacy in operations
        )
        if QMessageBox.question(
            self, "Potwierdź rozliczenia", f"Zapisać {len(operations)} rozliczeń?\n\n{details}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        errors, saved = [], 0
        for transfer, invoice, amount, manual_id, legacy in operations:
            try:
                self._save_operation(transfer, invoice['id'], amount, manual_id, legacy)
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

    def _offset_correction(self):
        credits = [item for item in self.invoices if item.get('rodzaj') == 'korekta'
                   and Decimal(str(item.get('kwota_brutto', 0))) < 0 and Decimal(str(item['balance'])) > 0]
        if not credits:
            QMessageBox.information(self, 'Rozlicz korektą', 'Brak wystawionych korekt ujemnych z niewykorzystanym saldem.')
            return
        form = QDialog(self)
        form.setWindowTitle('Pomniejszenie należności korektą — bez przelewu')
        layout = QFormLayout(form)
        credit_choice, debit_choice = QComboBox(form), QComboBox(form)
        for item in credits:
            credit_choice.addItem(f'{item["numer"]} — {item["buyer"]} — dostępne {money_text(item["balance"])} zł', item['id'])
        amount = QLineEdit(form)
        paid_on = QDateEdit(QDate.currentDate(), form)
        paid_on.setCalendarPopup(True)
        paid_on.setDisplayFormat('dd-MM-yyyy')
        def family(item):
            return item.get('root_id') or item.get('korygowana_faktura_id') or item['id']
        def limit_amount():
            credit = next(item for item in credits if item['id'] == credit_choice.currentData())
            debit = next((item for item in self.invoices if item['id'] == debit_choice.currentData()), None)
            amount.setText(money_text(min(Decimal(str(credit['balance'])), Decimal(str(debit['balance']))) if debit else '0.00'))
        def populate():
            credit = next(item for item in credits if item['id'] == credit_choice.currentData())
            debit_choice.blockSignals(True)
            debit_choice.clear()
            for item in self.invoices:
                if (Decimal(str(item.get('kwota_brutto', item['balance']))) > 0 and Decimal(str(item['balance'])) > 0
                        and family(item) == family(credit) and invoice_contractor_key(item) == invoice_contractor_key(credit)):
                    debit_choice.addItem(f'{item["numer"]} — pozostało {money_text(item["balance"])} zł', item['id'])
            debit_choice.blockSignals(False)
            limit_amount()
        credit_choice.currentIndexChanged.connect(populate)
        debit_choice.currentIndexChanged.connect(limit_amount)
        for label, widget in (('Korekta ujemna:', credit_choice), ('Należność do pomniejszenia:', debit_choice),
                              ('Kwota pomniejszenia:', amount), ('Data rozliczenia:', paid_on)):
            layout.addRow(label, widget)
        layout.addRow(QLabel('Rozliczenie nie tworzy wpłaty i nie zużywa kwoty przelewu MT940.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(form.accept)
        buttons.rejected.connect(form.reject)
        layout.addRow(buttons)
        populate()
        if form.exec() != QDialog.DialogCode.Accepted:
            return
        credit_id, debit_id = credit_choice.currentData(), debit_choice.currentData()
        if debit_id is None:
            QMessageBox.information(self, 'Rozlicz korektą', 'Brak należności w tej rodzinie faktur. Pozostałe saldo korekty można rozliczyć zwrotem.')
            return
        try:
            value = _payment_amount(amount.text())
            credit, debit = (next(item for item in self.invoices if item['id'] == key) for key in (credit_id, debit_id))
            if QMessageBox.question(self, 'Rozlicz korektą', f'Pomniejszyć należność {debit["numer"]} o {value:.2f} zł '
                                    f'korektą {credit["numer"]}? Bez przelewu i bez nowej wpłaty.',
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            apply_invoice_correction(credit_id, debit_id, value, paid_on.date().toString('yyyy-MM-dd'), self.actor_id)
        except (PaymentError, PermissionError) as exc:
            QMessageBox.warning(self, 'Rozlicz korektą', str(exc))
            return
        self.refresh()

    def _delete_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.warning(self, "Usuwanie", "Nie wybrano przelewów.")
            return
        transfer_ids = [int(self.transactions[row]['id']) for row in rows]
        if QMessageBox.question(
            self, "Usuń przelewy", f"Usunąć zaznaczone operacje MT940 ({len(transfer_ids)})?\n"
            "Powiązane wpłaty pozostaną na fakturach jako ręczne. Usuwany jest wyłącznie zapis importu MT940.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        errors, deleted = [], 0
        for transfer_id in transfer_ids:
            try:
                delete_bank_transaction(transfer_id, self.actor_id, preserve_payments=True)
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
    return record_payment_in_dialog(invoice, actor_id, parent, cash_only=True)


def record_payment_in_dialog(invoice: dict, actor_id: int, parent=None, *, cash_only=False) -> bool:
    from app.db.dao_payments import get_invoice_payment_summary
    try:
        summary = get_invoice_payment_summary(int(invoice["id"]), actor_id)
    except (PaymentError, PermissionError) as exc:
        QMessageBox.warning(parent, "Zapłacono", str(exc))
        return False
    except Exception:
        logger.exception("Błąd odczytu salda faktury")
        QMessageBox.warning(parent, "Zapłacono", "Nie udało się odczytać salda faktury. Szczegóły w logach.")
        return False
    if summary["balance"] <= 0:
        QMessageBox.information(parent, "Zapłacono", "Ta faktura jest już w całości rozliczona.")
        return False
    refund = invoice.get('rodzaj') == 'korekta' and summary['total'] < 0
    form = QDialog(parent)
    form.setWindowTitle('Zwrócono — ręczne oznaczenie zwrotu' if refund else "Zapłacono — ręczne oznaczenie wpłaty")
    layout = QFormLayout(form)
    date_input = QDateEdit(QDate.currentDate(), form)
    date_input.setCalendarPopup(True)
    date_input.setDisplayFormat("dd-MM-yyyy")
    amount_edit = QLineEdit(form)
    amount_edit.setText(money_text(summary["balance"]))
    layout.addRow("Faktura:", QLabel(str(invoice.get("numer") or invoice["id"]), form))
    layout.addRow("Data zapłaty:", date_input)
    method = QComboBox(form)
    method.addItem("Przelew / wpłata bez importu MT940", "bank")
    method.addItem("Gotówka", "gotowka")
    method.setCurrentIndex(1 if cash_only else 0)
    method.setEnabled(not cash_only)
    layout.addRow("Sposób zapłaty:", method)
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
        QMessageBox.warning(parent, "Zapłacono", str(exc))
        return False
    if QMessageBox.question(
        parent, "Potwierdź zapłatę",
        f"Zarejestrować {'zwrot' if refund else 'wpłatę'} {amount} zł z dnia {date_input.date().toString('dd-MM-yyyy')} "
        f"na fakturę {invoice.get('numer') or invoice['id']}?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    ) != QMessageBox.StandardButton.Yes:
        return False
    try:
        save_payment = record_cash_payment if method.currentData() == "gotowka" else record_manual_payment
        save_payment(
            int(invoice["id"]),
            date_input.date().toString("yyyy-MM-dd"), amount, actor_id,
        )
    except (PaymentError, PermissionError) as exc:
        QMessageBox.warning(parent, "Zapłacono", str(exc))
        return False
    except Exception:
        logger.exception("Błąd zapisu płatności gotówkowej")
        QMessageBox.warning(parent, "Zapłacono", "Nie udało się zapisać płatności. Szczegóły w logach.")
        return False
    return True
