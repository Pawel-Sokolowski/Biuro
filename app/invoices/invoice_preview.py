"""Lokalny podgląd faktury i zapis do PDF bez zależności zewnętrznych."""

from __future__ import annotations

import html
import base64
import io
import logging
import re
from decimal import Decimal, InvalidOperation

from PyQt6.QtCore import QMarginsF
from PyQt6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument
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
    payment_method = invoice.get("sposob_zaplaty") or "Przelew"
    account = invoice.get("konto_bankowe") or get_setting("konto_bankowe_biura") or ""
    ksef_date = str(invoice.get("ksef_data") or "")[:10]
    return f"""
    <html><head><style>
      body {{ font-family: 'Arial'; font-size: 9.5pt; color: #111; margin: 10px; }}
      table {{ border-collapse: collapse; }}
      td {{ vertical-align: top; }}
      .doc-title {{ font-size: 12pt; font-weight: bold; text-align: right; }}
      .doc-number {{ font-size: 15pt; font-weight: bold; text-align: right; }}
      .ksef-head {{ color: #d71920; font-size: 10pt; font-weight: bold; text-align: right; }}
      .label {{ color: #444; font-size: 8pt; }}
      .party-name {{ font-size: 10pt; font-weight: bold; }}
      .items th {{ background: #eeeeee; padding: 6px 4px; }}
      .items td {{ padding: 7px 4px; }}
      .num {{ text-align: right; white-space: nowrap; }}
      .grand {{ font-size: 15pt; font-weight: bold; }}
      .ksef-number {{ font-family: monospace; font-size: 8pt; color: #333; }}
    </style></head><body>
      <table width="100%" border="0" cellspacing="0" cellpadding="8"><tr>
        <td width="45%">{logo_html}</td>
        <td width="55%" align="right"><div class="doc-title">{document_title} nr</div>
        <div class="doc-number">{_escape(document_number)}</div><br>
        <b>Nr KSeF:</b><br><span class="ksef-head">{_escape(invoice.get('ksef_numer') or '—')}</span><br>
        <span class="label">Data przyjęcia do KSeF:</span><br><b>{_escape(ksef_date or '—')}</b><br><br>
        <span class="label">Data wystawienia:</span> <b>{_escape(invoice.get('data_wystawienia'))}</b><br>
        <span class="label">Data sprzedaży:</span> <b>{_escape(invoice.get('data_sprzedazy'))}</b></td>
      </tr></table>
      <br>
      <table width="100%" border="1" cellspacing="0" cellpadding="8">
        <tr bgcolor="#eeeeee"><th width="50%" align="left">Sprzedawca</th><th width="50%" align="left">Nabywca</th></tr>
        <tr><td><span class="party-name">{_escape(seller)}</span><br>
        {_escape(seller_address)}<br>NIP: <b>{_escape(seller_nip)}</b><br>
        </td><td><span class="party-name">{_escape(invoice.get('nabywca_pelna') or invoice.get('firma_nazwa'))}</span><br>
        {_escape(buyer_address)}<br>NIP: <b>{_escape(invoice.get('nabywca_nip'))}</b><br>
        REGON: {_escape(invoice.get('nabywca_regon'))}<br>Kraj: {_escape(invoice.get('nabywca_kraj'))}<br>
        E-mail: {_escape(invoice.get('nabywca_email'))}</td>
      </tr></table>
      <br><br>
      <table class="items" width="100%" border="1" cellspacing="0" cellpadding="4"><thead><tr>
      <th width="5%">Lp.</th><th width="35%">Nazwa towaru lub usługi</th><th width="8%">Ilość</th><th width="7%">J.m.</th>
      <th>Cena netto</th><th>VAT %</th><th>Netto</th><th>Brutto</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table>
      <br>
      <table width="100%" border="0" cellspacing="0" cellpadding="5"><tr>
        <td width="55%"></td><td width="25%" align="right"><b>Razem netto</b></td>
        <td width="20%" align="right">{_money(invoice.get('kwota_netto'))} PLN</td></tr>
        <tr><td></td><td align="right" class="grand">Razem do zapłaty</td>
        <td align="right" class="grand">{_money(invoice.get('kwota_brutto'))} PLN</td></tr>
      </table>
      {f'<div><b>Faktura korygowana:</b> {_escape(invoice.get("korygowana_faktura_numer"))}<br><b>Przyczyna:</b> {_escape(invoice.get("przyczyna_korekty"))}</div>' if invoice.get('rodzaj') == 'korekta' else ''}
      <br>
      <table width="100%" border="0" cellspacing="0" cellpadding="6"><tr>
        <td width="70%"><table border="0" cellspacing="0" cellpadding="3">
          <tr><td><b>Sposób zapłaty</b></td><td>{_escape(payment_method)}</td></tr>
          <tr><td><b>Termin zapłaty</b></td><td>{_escape(invoice.get('termin_platnosci'))}</td></tr>
          <tr><td><b>Numer rachunku</b></td><td>{_escape(account)}</td></tr>
          <tr><td><b>Numer KSeF</b></td><td><span class="ksef-number">{_escape(invoice.get('ksef_numer') or '—')}</span></td></tr>
        </table></td>
        <td width="30%" align="center">{qr_html}<span class="label">Zeskanuj kod, aby zweryfikować fakturę w KSeF</span></td>
      </tr></table>
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
        self.resize(1150, 840)
        layout = QVBoxLayout(self)
        self.preview = QTextBrowser(self)
        self.html = invoice_html(self.invoice, get_invoice_positions(int(self.invoice["id"])))
        self.preview.setHtml(self.html)
        self.preview.document().setDocumentMargin(8)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        save = QPushButton("Zapisz PDF…", self)
        save.clicked.connect(self._save_pdf)
        buttons.addButton(save, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "preview"):
            # Podgląd wykorzystuje całą szerokość okna. Wysokość wynika z
            # proporcji i kolejnych stron A4, a eksport ma osobny układ strony.
            self.preview.document().setTextWidth(max(560, self.preview.viewport().width() - 20))

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
            document = QTextDocument()
            document.setDocumentMargin(0)
            document.setHtml(self.html)
            # Ustal tylko szerokość składu. Nadanie dokumentowi pełnej wysokości
            # A4 powoduje w QTextDocument sztuczne rozciąganie pionowych przerw.
            document.setTextWidth(float(writer.pageLayout().paintRectPoints().width()))
            document.print(writer)
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "PDF", f"Nie udało się zapisać pliku PDF: {exc}")
            return
        QMessageBox.information(self, "PDF", f"Zapisano:\n{path}")

