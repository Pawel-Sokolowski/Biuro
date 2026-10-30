"""Lokalny podgląd faktury i zapis do PDF bez zależności zewnętrznych."""

from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation

from PyQt6.QtCore import QMarginsF
from PyQt6.QtGui import QPageLayout, QPageSize, QPdfWriter
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout,
)

from app.db.dao_app_settings import get_setting_first
from app.db.dao_invoices import get_invoice_positions


def _escape(value) -> str:
    return html.escape(str(value or ""))


def _money(value) -> str:
    try:
        return f"{Decimal(str(value or 0)):.2f}"
    except (InvalidOperation, ValueError):
        return "0.00"


def invoice_html(invoice: dict, positions: list[dict]) -> str:
    seller = invoice.get("wystawca") or get_setting_first(
        ("wystawca_faktur", "wystawca", "faktury_wystawca"), "Biuro rachunkowe",
    )
    buyer_address = " ".join(filter(None, (
        invoice.get("nabywca_adres"), invoice.get("nabywca_kod"), invoice.get("nabywca_miasto"),
    )))
    rows = []
    for index, item in enumerate(positions, 1):
        rows.append(
            "<tr>"
            f"<td>{index}</td><td>{_escape(item.get('nazwa') or item.get('opis'))}</td>"
            f"<td class='num'>{_escape(item.get('ilosc'))}</td><td>{_escape(item.get('jednostka') or 'szt.')}</td>"
            f"<td class='num'>{_money(item.get('cena_netto'))}</td>"
            f"<td class='num'>{_escape(item.get('vat', item.get('stawka_vat', '')))}</td>"
            f"<td class='num'>{_money(item.get('netto', item.get('wartosc_netto')))}</td>"
            f"<td class='num'>{_money(item.get('brutto', item.get('wartosc_brutto')))}</td>"
            "</tr>"
        )
    return f"""
    <html><head><style>
      body {{ font-family: 'Arial'; font-size: 10pt; color: #202020; }}
      h1 {{ font-size: 20pt; margin-bottom: 2px; }}
      .muted {{ color: #666; }} .parties {{ width: 100%; margin: 18px 0; }}
      .parties td {{ width: 50%; vertical-align: top; padding-right: 18px; }}
      table.items {{ border-collapse: collapse; width: 100%; }}
      .items th, .items td {{ border: 1px solid #aaa; padding: 5px; }}
      .items th {{ background: #e9f2f9; }} .num {{ text-align: right; white-space: nowrap; }}
      .totals {{ margin-top: 16px; text-align: right; font-size: 12pt; }}
      .ksef {{ margin-top: 22px; font-size: 8pt; color: #555; }}
    </style></head><body>
      <h1>{'Korekta' if invoice.get('rodzaj') == 'korekta' else 'Faktura'} {_escape(invoice.get('numer') or '(bez numeru)')}</h1>
      <div class="muted">Data wystawienia: {_escape(invoice.get('data_wystawienia'))}
      &nbsp;&nbsp; Data sprzedaży: {_escape(invoice.get('data_sprzedazy'))}
      &nbsp;&nbsp; Termin płatności: {_escape(invoice.get('termin_platnosci'))}</div>
      <table class="parties"><tr>
        <td><b>Sprzedawca</b><br>{_escape(seller)}</td>
        <td><b>Nabywca</b><br>{_escape(invoice.get('nabywca_pelna') or invoice.get('firma_nazwa'))}<br>
        {_escape(buyer_address)}<br>NIP: {_escape(invoice.get('nabywca_nip'))}</td>
      </tr></table>
      <table class="items"><thead><tr><th>Lp.</th><th>Nazwa</th><th>Ilość</th><th>J.m.</th>
      <th>Cena netto</th><th>VAT %</th><th>Netto</th><th>Brutto</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table>
      <div class="totals"><b>Razem netto: {_money(invoice.get('kwota_netto'))} PLN</b><br>
      <b>Razem brutto: {_money(invoice.get('kwota_brutto'))} PLN</b></div>
      <div class="ksef">Numer KSeF: {_escape(invoice.get('ksef_numer') or '—')}</div>
    </body></html>
    """


class InvoicePreviewDialog(QDialog):
    def __init__(self, invoice: dict, parent=None):
        super().__init__(parent)
        self.invoice = invoice
        self.setWindowTitle(f"Podgląd faktury {invoice.get('numer') or invoice.get('id')}")
        self.resize(1050, 760)
        layout = QVBoxLayout(self)
        self.preview = QTextBrowser(self)
        self.preview.setHtml(invoice_html(invoice, get_invoice_positions(int(invoice["id"]))))
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        save = QPushButton("Zapisz PDF…", self)
        save.clicked.connect(self._save_pdf)
        buttons.addButton(save, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _default_name(self) -> str:
        number = re.sub(r"[^A-Za-z0-9._-]+", "_", str(self.invoice.get("numer") or self.invoice["id"]))
        return f"Faktura_{number}.pdf"

    def _save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz podgląd faktury", self._default_name(), "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            writer = QPdfWriter(path)
            writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            writer.setPageLayout(QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter,
            ))
            self.preview.document().print(writer)
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "PDF", f"Nie udało się zapisać pliku PDF: {exc}")
            return
        QMessageBox.information(self, "PDF", f"Zapisano:\n{path}")

