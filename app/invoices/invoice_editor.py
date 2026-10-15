"""Edytor pozycji faktury (roboczej, korekty) oraz podgląd wystawionej faktury."""

from __future__ import annotations

from typing import Any, Dict, List

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from app.db.dao_invoices import get_invoice_positions, update_invoice_draft
from app.invoices.positions import normalize_position, totals
from app.utils.parsing import to_float

COL_NAZWA, COL_ILOSC, COL_JEDN, COL_CENA, COL_VAT, COL_NETTO, COL_BRUTTO = range(7)
EDITABLE_COLUMNS = (COL_NAZWA, COL_ILOSC, COL_JEDN, COL_CENA, COL_VAT)
NUMERIC_COLUMNS = (COL_ILOSC, COL_CENA, COL_VAT, COL_NETTO, COL_BRUTTO)


def _date_edit(value: str) -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd")
    parsed = QDate.fromString((value or "")[:10], "yyyy-MM-dd")
    edit.setDate(parsed if parsed.isValid() else QDate.currentDate())
    return edit


class InvoiceEditorDialog(QDialog):
    def __init__(self, invoice: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.invoice = invoice
        self.read_only = bool(invoice.get("eksportowana"))
        self.is_correction = invoice.get("rodzaj") == "korekta"
        kind = "Korekta" if self.is_correction else "Faktura"
        self.setWindowTitle(f"{kind} #{invoice['id']} - {invoice.get('miesiac', '')}")
        self.resize(900, 560)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.numer = QLineEdit(invoice.get("numer") or "")
        self.numer.setPlaceholderText("np. FV/10/2026/001")
        self.data_wyst = _date_edit(invoice.get("data_wystawienia") or "")
        self.data_sprz = _date_edit(invoice.get("data_sprzedazy") or "")
        self.termin = _date_edit(invoice.get("termin_platnosci") or "")
        form.addRow("Numer:", self.numer)
        form.addRow("Data wystawienia:", self.data_wyst)
        form.addRow("Data sprzedaży:", self.data_sprz)
        form.addRow("Termin płatności:", self.termin)
        self.przyczyna = QLineEdit(invoice.get("przyczyna_korekty") or "")
        if self.is_correction:
            form.addRow("Przyczyna korekty:", self.przyczyna)
        layout.addLayout(form)

        if self.is_correction:
            hint = QLabel(
                "Pozycje korekty to różnice względem faktury pierwotnej (ujemna ilość = zwrot/obniżenie). "
                "Domyślnie korekta zeruje całą fakturę - zmień ilości lub ceny, aby skorygować częściowo."
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Nazwa", "Ilość", "Jednostka", "Cena netto", "VAT %", "Wartość netto", "Wartość brutto"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_NAZWA, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table)

        row_buttons = QHBoxLayout()
        self.btn_add = QPushButton("Dodaj pozycję")
        self.btn_add.clicked.connect(lambda: self._add_row({"nazwa": "", "ilosc": 1, "jednostka": "szt.", "cena_netto": 0, "vat": 23}))
        self.btn_remove = QPushButton("Usuń pozycję")
        self.btn_remove.clicked.connect(self._remove_row)
        row_buttons.addWidget(self.btn_add)
        row_buttons.addWidget(self.btn_remove)
        row_buttons.addStretch()
        self.lbl_total = QLabel()
        row_buttons.addWidget(self.lbl_total)
        layout.addLayout(row_buttons)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
            if not self.read_only else QDialogButtonBox.StandardButton.Close
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for position in get_invoice_positions(invoice["id"]):
            self._add_row(position)
        self._recalculate_total()
        self.table.itemChanged.connect(self._on_item_changed)

        if self.read_only:
            for widget in (self.numer, self.data_wyst, self.data_sprz, self.termin, self.przyczyna):
                widget.setEnabled(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.btn_add.setEnabled(False)
            self.btn_remove.setEnabled(False)

    def _add_row(self, position: Dict[str, Any]):
        item = normalize_position(position)
        self.table.blockSignals(True)
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = {
            COL_NAZWA: item["nazwa"], COL_ILOSC: f"{item['ilosc']:g}", COL_JEDN: item.get("jednostka", ""),
            COL_CENA: f"{item['cena_netto']:.2f}", COL_VAT: f"{item['vat']:g}",
            COL_NETTO: f"{item['netto']:.2f}", COL_BRUTTO: f"{item['brutto']:.2f}",
        }
        for column, text in values.items():
            cell = QTableWidgetItem(str(text))
            if column in NUMERIC_COLUMNS:
                cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if column not in EDITABLE_COLUMNS:
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, column, cell)
        # Pola pozycji, których edytor nie pokazuje (kategoria, generated...), są zachowywane
        self.table.item(row, COL_NAZWA).setData(Qt.ItemDataRole.UserRole, dict(position))
        self.table.blockSignals(False)

    def _remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._recalculate_total()

    def _row_position(self, row: int) -> Dict[str, Any]:
        def text(column):
            cell = self.table.item(row, column)
            return cell.text().strip() if cell else ""

        base = dict(self.table.item(row, COL_NAZWA).data(Qt.ItemDataRole.UserRole) or {})
        base.pop("netto", None)
        base.pop("brutto", None)
        base.update({
            "nazwa": text(COL_NAZWA), "ilosc": to_float(text(COL_ILOSC), 1.0),
            "jednostka": text(COL_JEDN), "cena_netto": to_float(text(COL_CENA)),
            "vat": to_float(text(COL_VAT), 23.0),
        })
        return normalize_position(base)

    def positions(self) -> List[Dict[str, Any]]:
        return [self._row_position(row) for row in range(self.table.rowCount())]

    def _on_item_changed(self, cell: QTableWidgetItem):
        if cell.column() not in EDITABLE_COLUMNS:
            return
        row = cell.row()
        position = self._row_position(row)
        self.table.blockSignals(True)
        self.table.item(row, COL_NETTO).setText(f"{position['netto']:.2f}")
        self.table.item(row, COL_BRUTTO).setText(f"{position['brutto']:.2f}")
        self.table.blockSignals(False)
        self._recalculate_total()

    def _recalculate_total(self):
        netto, brutto = totals(self.positions())
        self.lbl_total.setText(f"Razem netto: {netto:.2f} zł    brutto: {brutto:.2f} zł")

    def _save(self):
        positions = self.positions()
        if not positions:
            QMessageBox.warning(self, "Faktura", "Dodaj co najmniej jedną pozycję.")
            return
        if any(not p["nazwa"] for p in positions):
            QMessageBox.warning(self, "Faktura", "Każda pozycja musi mieć nazwę.")
            return
        try:
            update_invoice_draft(
                self.invoice["id"],
                positions,
                numer=self.numer.text(),
                data_wystawienia=self.data_wyst.date().toString("yyyy-MM-dd"),
                data_sprzedazy=self.data_sprz.date().toString("yyyy-MM-dd"),
                termin_platnosci=self.termin.date().toString("yyyy-MM-dd"),
                przyczyna_korekty=self.przyczyna.text().strip(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Faktura", str(exc))
            return
        self.accept()
