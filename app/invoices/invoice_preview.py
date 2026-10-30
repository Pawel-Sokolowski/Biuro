"""Lokalny podgląd faktury i zapis do PDF bez zależności zewnętrznych."""

from __future__ import annotations

import html
import base64
import io
import logging
import re
from decimal import Decimal, InvalidOperation

from PyQt6.QtCore import QMarginsF
from PyQt6.QtGui import QPageLayout, QPageSize, QPdfWriter
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout,
)

from app.db.dao_app_settings import get_setting, get_setting_first
from app.db.dao_companies import get_company
from app.db.dao_invoices import get_invoice_positions
from app.db.dao_kontrahenci import get_kontrahent
from app.invoices.ksef import ksef_url

logger = logging.getLogger(__name__)


def _with_buyer_data(invoice: dict) -> dict:
    result = dict(invoice)
    buyer = None
    source = {}
    if result.get("firma_id"):
        buyer = get_company(int(result["firma_id"]))
        if buyer:
            source = {
                "nabywca_pelna": buyer.get("nazwa"),
                "nabywca_skrocona": buyer.get("nazwa_skrocona") or buyer.get("nazwa"),
                "nabywca_adres": " ".join(filter(None, (buyer.get("ulica"), buyer.get("adres_budynek"), buyer.get("adres_lokal")))),
                "nabywca_kod": buyer.get("kod_pocztowy"), "nabywca_miasto": buyer.get("miasto"),
                "nabywca_nip": buyer.get("nip"), "nabywca_regon": buyer.get("regon"),
                "nabywca_kraj": buyer.get("kraj") or "Polska", "nabywca_email": buyer.get("adres_email"),
            }
    elif result.get("kontrahent_id"):
        buyer = get_kontrahent(int(result["kontrahent_id"]))
        source = ({
            "nabywca_pelna": buyer.get("nazwa"), "nabywca_skrocona": buyer.get("nazwa"),
            "nabywca_adres": buyer.get("adres"), "nabywca_kod": buyer.get("kod"),
            "nabywca_miasto": buyer.get("miasto"), "nabywca_nip": buyer.get("nip"),
            "nabywca_regon": buyer.get("regon"), "nabywca_kraj": buyer.get("kraj") or "Polska",
            "nabywca_email": buyer.get("email"),
        } if buyer else {})
    for key, value in source.items():
        if not result.get(key) and value:
            result[key] = value
    return result


def _escape(value) -> str:
    return html.escape(str(value or ""))


def _money(value) -> str:
    try:
        return f"{Decimal(str(value or 0)):.2f}"
    except (InvalidOperation, ValueError):
        return "0.00"


def _qr_data_url(invoice: dict) -> str:
    if not invoice.get("ksef_numer"):
        return ""
    try:
        # Jawne API działa także wtedy, gdy pakiet nie eksportuje skrótu
        # qrcode.make na najwyższym poziomie.
        from qrcode.constants import ERROR_CORRECT_M
        from qrcode.main import QRCode
        code = QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=6, border=2)
        code.add_data(ksef_url(invoice.get("ksef_numer"), invoice=invoice))
        code.make(fit=True)
        image = code.make_image(fill_color="black", back_color="white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (ImportError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
        logger.exception("Nie udało się wygenerować kodu QR KSeF")
        return ""


def invoice_html(invoice: dict, positions: list[dict]) -> str:
    seller = get_setting("faktury_sprzedawca_nazwa") or invoice.get("wystawca") or get_setting_first(
        ("wystawca_faktur", "wystawca", "faktury_wystawca"), "Biuro rachunkowe",
    )
    seller_nip = get_setting("faktury_sprzedawca_nip") or invoice.get("ksef_nip_wysylki") or ""
    seller_address = " ".join(filter(None, (
        get_setting("faktury_sprzedawca_adres"), get_setting("faktury_sprzedawca_kod"),
        get_setting("faktury_sprzedawca_miasto"), get_setting("faktury_sprzedawca_kraj"),
    )))
    logo = get_setting("faktury_logo_data") or ""
    logo_html = f'<img src="{_escape(logo)}" width="180"><br>' if logo.startswith("data:image/") else ""
    qr = _qr_data_url(invoice)
    qr_html = (f'<img src="{_escape(qr)}" width="120" height="120"><br>' if qr else
               '<span style="color:#8b98a5">Kod QR niedostępny</span><br>')
    buyer_address = " ".join(filter(None, (
        invoice.get("nabywca_adres"), invoice.get("nabywca_kod"), invoice.get("nabywca_miasto"),
    )))
    rows = []
    for index, item in enumerate(positions, 1):
        quantity = item.get("ilosc") or 0
        unit_net = item.get("cena_netto")
        if unit_net in (None, "", 0, 0.0) and quantity:
            try:
                unit_net = Decimal(str(item.get("netto", item.get("wartosc_netto")) or 0)) / Decimal(str(quantity))
            except (InvalidOperation, ZeroDivisionError):
                unit_net = 0
        rows.append(
            "<tr>"
            f"<td>{index}</td><td>{_escape(item.get('nazwa') or item.get('opis'))}</td>"
            f"<td class='num'>{_escape(quantity)}</td><td>{_escape(item.get('jednostka') or 'szt.')}</td>"
            f"<td class='num'>{_money(unit_net)}</td>"
            f"<td class='num'>{_escape(item.get('vat', item.get('stawka_vat', '')))}</td>"
            f"<td class='num'>{_money(item.get('netto', item.get('wartosc_netto')))}</td>"
            f"<td class='num'>{_money(item.get('brutto', item.get('wartosc_brutto')))}</td>"
            "</tr>"
        )
    document_title = "FAKTURA KORYGUJĄCA" if invoice.get("rodzaj") == "korekta" else "FAKTURA VAT"
    document_number = invoice.get("numer") or "Dokument KSeF"
    return f"""
    <html><head><style>
      body {{ font-family: 'Arial'; font-size: 9.5pt; color: #25313c; margin: 18px; }}
      table {{ border-collapse: collapse; }}
      .top {{ width: 100%; background: #1769aa; color: white; }}
      .top td {{ padding: 16px; vertical-align: middle; }}
      .doc-title {{ font-size: 20pt; font-weight: bold; text-align: right; }}
      .doc-number {{ font-size: 12pt; text-align: right; }}
      .meta {{ width: 100%; margin-top: 12px; background: #edf3f8; }}
      .meta td {{ padding: 8px; text-align: center; }}
      .label {{ color: #607383; font-size: 8pt; }}
      .parties {{ width: 100%; margin-top: 14px; }}
      .parties td {{ width: 50%; vertical-align: top; padding: 12px; border: 1px solid #cbd6df; }}
      .party-title {{ color: #1769aa; font-size: 9pt; font-weight: bold; }}
      .party-name {{ font-size: 11pt; font-weight: bold; }}
      .items {{ width: 100%; margin-top: 18px; }}
      .items th {{ background: #1769aa; color: white; padding: 7px 5px; }}
      .items td {{ border-bottom: 1px solid #d7e0e7; padding: 7px 5px; }}
      .items tr:nth-child(even) {{ background: #f5f8fa; }}
      .num {{ text-align: right; white-space: nowrap; }}
      .summary {{ width: 43%; margin-top: 16px; margin-left: 57%; }}
      .summary td {{ padding: 7px; border-bottom: 1px solid #d7e0e7; }}
      .summary .grand td {{ background: #1769aa; color: white; font-size: 12pt; font-weight: bold; }}
      .footer {{ width: 100%; margin-top: 20px; border-top: 2px solid #1769aa; }}
      .footer td {{ vertical-align: top; padding-top: 10px; }}
      .ksef-number {{ font-family: monospace; font-size: 8pt; color: #455a64; }}
    </style></head><body>
      <table class="top"><tr><td>{logo_html}<b>{_escape(seller)}</b></td>
      <td><div class="doc-title">{document_title}</div><div class="doc-number">nr {_escape(document_number)}</div></td></tr></table>
      <table class="meta"><tr>
        <td><span class="label">DATA WYSTAWIENIA</span><br><b>{_escape(invoice.get('data_wystawienia'))}</b></td>
        <td><span class="label">DATA SPRZEDAŻY</span><br><b>{_escape(invoice.get('data_sprzedazy'))}</b></td>
        <td><span class="label">TERMIN PŁATNOŚCI</span><br><b>{_escape(invoice.get('termin_platnosci'))}</b></td>
      </tr></table>
      <table class="parties"><tr>
        <td><span class="party-title">SPRZEDAWCA</span><br><span class="party-name">{_escape(seller)}</span><br>
        {_escape(seller_address)}<br>NIP: <b>{_escape(seller_nip)}</b><br>
        Rachunek: {_escape(invoice.get('konto_bankowe') or get_setting('konto_bankowe_biura'))}</td>
        <td><span class="party-title">NABYWCA</span><br><span class="party-name">{_escape(invoice.get('nabywca_pelna') or invoice.get('firma_nazwa'))}</span><br>
        {_escape(buyer_address)}<br>NIP: <b>{_escape(invoice.get('nabywca_nip'))}</b><br>
        REGON: {_escape(invoice.get('nabywca_regon'))}<br>Kraj: {_escape(invoice.get('nabywca_kraj'))}<br>
        E-mail: {_escape(invoice.get('nabywca_email'))}</td>
      </tr></table>
      <table class="items"><thead><tr><th>Lp.</th><th>Nazwa</th><th>Ilość</th><th>J.m.</th>
      <th>Cena netto</th><th>VAT %</th><th>Netto</th><th>Brutto</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table>
      <table class="summary"><tr><td>Razem netto</td><td class="num">{_money(invoice.get('kwota_netto'))} PLN</td></tr>
      <tr class="grand"><td>DO ZAPŁATY</td><td class="num">{_money(invoice.get('kwota_brutto'))} PLN</td></tr></table>
      {f'<div><b>Faktura korygowana:</b> {_escape(invoice.get("korygowana_faktura_numer"))}<br><b>Przyczyna:</b> {_escape(invoice.get("przyczyna_korekty"))}</div>' if invoice.get('rodzaj') == 'korekta' else ''}
      <table class="footer"><tr><td><b>Numer KSeF</b><br><span class="ksef-number">{_escape(invoice.get('ksef_numer') or '—')}</span></td>
      <td style="text-align:right">{qr_html}</td></tr></table>
    </body></html>
    """


class InvoicePreviewDialog(QDialog):
    def __init__(self, invoice: dict, parent=None):
        super().__init__(parent)
        self.invoice = _with_buyer_data(invoice)
        if self.invoice.get("korygowana_faktura_id"):
            from app.db.dao_invoices import get_invoice_by_id
            original = get_invoice_by_id(int(self.invoice["korygowana_faktura_id"]))
            self.invoice["korygowana_faktura_numer"] = (original or {}).get("numer")
        self.setWindowTitle(f"Podgląd faktury {invoice.get('numer') or invoice.get('id')}")
        self.resize(1050, 760)
        layout = QVBoxLayout(self)
        self.preview = QTextBrowser(self)
        self.preview.setHtml(invoice_html(self.invoice, get_invoice_positions(int(self.invoice["id"]))))
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

