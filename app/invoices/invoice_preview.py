"""Lokalny podgląd faktury i zapis do PDF bez zależności zewnętrznych."""

from __future__ import annotations

import html
import base64
import io
import logging
import os
import re
import tempfile
from pathlib import Path
from decimal import Decimal, InvalidOperation

from PyQt6.QtCore import QMarginsF, QSizeF, QTimer
from PyQt6.QtGui import QFont, QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout,
)

from app.db.dao_app_settings import get_setting, get_setting_first
from app.db.dao_companies import get_company
from app.db.dao_invoices import get_invoice_positions
from app.db.dao_kontrahenci import get_kontrahent
from app.invoices.ksef import ksef_url
from app.invoices.vat_summary import summary_html

logger = logging.getLogger(__name__)

INVOICE_FONT_PT = 12.0


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
    from app.invoices.invoice_identity import with_seller_identity
    invoice = with_seller_identity(invoice)
    frozen = invoice.get('_seller_frozen')
    seller = invoice.get('sprzedawca_nazwa', '') if frozen else invoice.get("sprzedawca_nazwa") or get_setting("faktury_sprzedawca_nazwa") or invoice.get("wystawca") or get_setting_first(
        ("wystawca_faktur", "wystawca", "faktury_wystawca"), "Biuro rachunkowe",
    )
    seller_nip = invoice.get('sprzedawca_nip', '') if frozen else invoice.get("sprzedawca_nip") or get_setting("faktury_sprzedawca_nip") or invoice.get("ksef_nip_wysylki") or ""
    seller_address = invoice.get('sprzedawca_adres', '') if frozen else invoice.get("sprzedawca_adres") or " ".join(filter(None, (
        get_setting("faktury_sprzedawca_adres"), get_setting("faktury_sprzedawca_kod"),
        get_setting("faktury_sprzedawca_miasto"),
    )))
    configured_nip = re.sub(r"\D", "", get_setting("faktury_sprzedawca_nip") or "")
    matching_seller = bool(configured_nip and configured_nip == re.sub(r"\D", "", seller_nip))
    def seller_detail(field):
        value = invoice.get("sprzedawca_" + field)
        if value is None and not frozen and (not invoice.get("_official") or matching_seller):
            value = get_setting("faktury_sprzedawca_" + field)
        return value or ""
    seller_regon, seller_country, seller_email = (seller_detail(field) for field in ("regon", "kraj", "email"))
    logo = get_setting("faktury_logo_data") or ""
    logo_html = f'<img src="{_escape(logo)}" width="180"><br>' if logo.startswith("data:image/") else ""
    qr = invoice.get("_qr_data_url") or _qr_data_url(invoice)
    verification_url = ksef_url(invoice.get("ksef_numer"), invoice=invoice) if invoice.get("ksef_numer") else ""
    verification_html = ""
    if verification_url.startswith("https://"):
        visible_url = "\u200b".join(verification_url[index:index + 20] for index in range(0, len(verification_url), 20))
        verification_html = (f'<br><a href="{_escape(verification_url)}">Zweryfikuj fakturę w KSeF</a>'
                             f'<br><span class="label"><a href="{_escape(verification_url)}">{_escape(visible_url)}</a></span>')
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
    document_title = {"korekta": "FAKTURA KORYGUJĄCA", "zaliczkowa": "FAKTURA ZALICZKOWA",
                      "proforma": "FAKTURA PROFORMA"}.get(invoice.get("rodzaj"), "FAKTURA VAT")
    document_number = invoice.get("numer") or "Dokument KSeF"
    payment_method = invoice.get("sposob_zaplaty") or "Przelew"
    account = invoice.get("konto_bankowe") or ("" if invoice.get("_official") else get_setting("konto_bankowe_biura")) or ""
    ksef_date = str(invoice.get("ksef_data") or "")[:10]
    number_part = str(invoice.get("ksef_numer") or "").split("-")
    if len(number_part) >= 3 and re.fullmatch(r"\d{8}", number_part[1]):
        # Data z numeru KSeF to faktyczna data nadania; ksef_data w starszych importach bywa datą pobrania.
        ksef_date = f"{number_part[1][:4]}-{number_part[1][4:6]}-{number_part[1][6:]}"
    return f"""
    <html><head><style>
      body {{ background-color: white; font-family: 'Arial'; font-size: {INVOICE_FONT_PT:g}pt; color: #111; margin: 10px; }}
      table {{ border-collapse: collapse; }}
      td {{ vertical-align: top; }}
      .doc-title {{ font-size: 13pt; font-weight: bold; text-align: right; }}
      .doc-number {{ font-size: 16pt; font-weight: bold; text-align: right; }}
      .ksef-head {{ color: #d71920; font-size: 12pt; font-weight: bold; text-align: right; }}
      .label {{ color: #444; font-size: 12pt; }}
      .party-name {{ font-size: 12pt; font-weight: bold; }}
      .items th {{ background: #eeeeee; padding: 6px 4px; }}
      .items td {{ padding: 7px 4px; }}
      .num {{ text-align: right; white-space: nowrap; }}
      .grand {{ font-size: 14pt; font-weight: bold; }}
      .ksef-number {{ font-family: monospace; font-size: 12pt; color: #333; }}
    </style></head><body>
    {('<h1>ANULOWANA</h1><p>' + _escape(invoice.get('cancelled_reason')) + '</p>') if invoice.get('cancelled_at') else ''}
      <table width="100%" border="0" cellspacing="0" cellpadding="8"><tr>
        <td width="45%">{logo_html}</td>
        <td width="55%" align="right"><div class="doc-title">{document_title} nr</div>
        <div class="doc-number">{_escape(document_number)}</div><br>
        <b>Nr KSeF:</b><br><span class="ksef-head">{_escape(invoice.get('ksef_numer') or '—')}</span><br>
        <span class="label">Data przyjęcia do KSeF:</span><br><b>{_escape(ksef_date or '—')}</b><br>
        <span class="label">Data wystawienia:</span> <b>{_escape(invoice.get('data_wystawienia'))}</b><br>
        <span class="label">Data sprzedaży:</span> <b>{_escape(invoice.get('data_sprzedazy'))}</b></td>
      </tr></table>
      <br>
      <table width="100%" border="1" cellspacing="0" cellpadding="8">
        <tr bgcolor="#eeeeee"><th width="50%" align="left">Sprzedawca</th><th width="50%" align="left">Nabywca</th></tr>
        <tr><td><span class="party-name">{_escape(seller)}</span><br>
        {_escape(seller_address)}<br>NIP: <b>{_escape(seller_nip)}</b><br>
        REGON: {_escape(seller_regon)}<br>Kraj: {_escape(seller_country)}<br>
        E-mail: {_escape(seller_email)}</td><td><span class="party-name">{_escape(invoice.get('nabywca_pelna') or invoice.get('firma_nazwa'))}</span><br>
        {_escape(buyer_address)}<br>NIP: <b>{_escape(invoice.get('nabywca_nip'))}</b><br>
        REGON: {_escape(invoice.get('nabywca_regon'))}<br>Kraj: {_escape(invoice.get('nabywca_kraj'))}<br>
        E-mail: {_escape(invoice.get('nabywca_email'))}</td>
      </tr></table>
      <br>
      <table class="items" width="100%" border="1" cellspacing="0" cellpadding="4"><thead><tr>
      <th width="5%">Lp.</th><th width="27%">Nazwa towaru lub usługi</th><th width="8%">Ilość</th><th width="6%">J.m.</th>
      <th width="14%">Cena netto</th><th width="8%">VAT&nbsp;%</th><th width="16%">Netto</th><th width="16%">Brutto</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table>
      <br>{summary_html(positions, paper=True, accepted_amounts=bool(invoice.get("_official")))}<br>
      <table width="100%" border="0" cellspacing="0" cellpadding="5"><tr>
        <td width="43%"></td><td width="32%" align="right"><b>Razem netto</b></td>
        <td width="25%" align="right">{_money(invoice.get('kwota_netto'))} PLN</td></tr>
        <tr><td></td><td align="right" class="grand">Razem do zapłaty</td>
        <td align="right" class="grand">{_money(invoice.get('kwota_brutto'))}&nbsp;PLN</td></tr>
      </table>
      {f'<div><b>Faktura korygowana:</b> {_escape(invoice.get("korygowana_faktura_numer"))}<br><b>Przyczyna:</b> {_escape(invoice.get("przyczyna_korekty"))}</div>' if invoice.get('rodzaj') == 'korekta' else ''}
      <br>
      <table width="100%" border="0" cellspacing="0" cellpadding="6"><tr>
        <td width="60%"><table border="0" cellspacing="0" cellpadding="3">
          <tr><td><b>Sposób zapłaty</b></td><td>{_escape(payment_method)}</td></tr>
          <tr><td><b>Termin zapłaty</b></td><td>{_escape(invoice.get('termin_platnosci'))}</td></tr>
          <tr><td><b>Numer rachunku</b></td><td style="white-space: nowrap">{_escape(account)}</td></tr>
        </table></td>
        <td width="40%" align="center">{qr_html}<span class="label">Zeskanuj kod, aby zweryfikować fakturę w KSeF</span>{verification_html}</td>
      </tr></table>
    </body></html>
    """


def write_invoice_pdf(path, rendered_html, *, title="Faktura"):
    """Validate a complete PDF before replacing an existing destination."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.pdf', delete=False) as handle:
            temporary = Path(handle.name)
        _write_invoice_pdf(temporary, rendered_html, title=title)
        try:
            if not temporary.read_bytes().startswith(b'%PDF') or not PdfReader(temporary).pages:
                raise OSError('Nie udało się wygenerować pliku PDF.')
        except (PdfReadError, ValueError, IndexError) as exc:
            raise OSError('Nie udało się wygenerować poprawnego pliku PDF.') from exc
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_invoice_pdf(path, rendered_html, *, title="Faktura"):
    """Qt's reentrant document/PDF classes also support background delivery jobs."""
    writer = QPdfWriter(str(path))
    writer.setResolution(600)
    writer.setTitle(title)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                    QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter))
    document = QTextDocument()
    # Lay out HTML in stable logical units, independently of the screen DPI.
    # The metric device is never painted: PDF text remains vector text.
    metrics = QImage(1, 1, QImage.Format.Format_ARGB32)
    metrics.setDotsPerMeterX(round(96 / 0.0254))
    metrics.setDotsPerMeterY(round(96 / 0.0254))
    document.documentLayout().setPaintDevice(metrics)
    font = QFont("Arial")
    font.setPointSizeF(INVOICE_FONT_PT)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    document.setDefaultFont(font)
    document.setUseDesignMetrics(True)
    document.setDocumentMargin(0)
    document.setHtml(rendered_html)
    document.setPageSize(QSizeF(writer.width() * metrics.logicalDpiX() / writer.resolution(),
                               writer.height() * metrics.logicalDpiY() / writer.resolution()))
    document.print(writer)
    del writer


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
        self.preview.setOpenExternalLinks(True)
        # Invoice paper stays white independently of the application theme.
        self.preview.setStyleSheet("QTextBrowser { background: white; color: #111; }")
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
        self._ksef_refresh_timer = QTimer(self)
        self._ksef_refresh_timer.setInterval(5000)
        self._ksef_refresh_timer.timeout.connect(self._refresh_ksef_identity)
        if self.invoice.get("ksef_status") in ("wysylanie", "oczekuje", "do_sprawdzenia"):
            self._ksef_refresh_timer.start()

    def _refresh_ksef_identity(self):
        from app.db.dao_invoices import get_invoice_by_id
        try:
            latest = get_invoice_by_id(int(self.invoice["id"]))
            if latest and latest.get("ksef_numer") and latest.get("ksef_numer") != self.invoice.get("ksef_numer"):
                for key in ("ksef_numer", "ksef_data", "ksef_nip_wysylki", "ksef_hash_faktury", "ksef_status"):
                    self.invoice[key] = latest.get(key)
                self.html = invoice_html(self.invoice, get_invoice_positions(int(self.invoice["id"])))
                self.preview.setHtml(self.html)
                self._ksef_refresh_timer.stop()
            elif latest and latest.get("ksef_status") == "odrzucona":
                self._ksef_refresh_timer.stop()
        except Exception:
            logger.exception("Nie udało się odświeżyć numeru KSeF w podglądzie")

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
            write_invoice_pdf(path, self.html, title=f"Faktura {self.invoice.get('numer') or self.invoice['id']}")
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "PDF", f"Nie udało się zapisać pliku PDF: {exc}")
            return
        QMessageBox.information(self, "PDF", f"Zapisano:\n{path}")

