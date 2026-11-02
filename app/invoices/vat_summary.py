"""VAT totals shared by invoice editors, PDF output and FA(3) generation."""
from decimal import Decimal, ROUND_HALF_UP
import html
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel
from app.invoices.positions import normalize_position

CENT = Decimal('0.01')


def vat_code(position):
    raw = str(position.get('vat_code') or position.get('vat', position.get('stawka_vat', 23))).strip().replace('%', '').strip()
    try:
        return format(Decimal(raw.replace(',', '.')).normalize(), 'f')
    except Exception:
        return raw.lower()


def vat_summary(positions, *, accepted_amounts=False):
    groups = {}
    for source in positions:
        code = vat_code(source)
        item = source if accepted_amounts else normalize_position(source)
        net = Decimal(str(item['netto'])).quantize(CENT, rounding=ROUND_HALF_UP)
        gross = Decimal(str(item['brutto'])).quantize(CENT, rounding=ROUND_HALF_UP)
        group = groups.setdefault(code, {'code': code, 'net': Decimal('0.00'), 'vat': Decimal('0.00'), 'gross': Decimal('0.00')})
        group['net'] += net
        line_vat = item.get('vat_amount') if accepted_amounts else None
        group['vat'] += (Decimal(str(line_vat)).quantize(CENT, rounding=ROUND_HALF_UP)
                         if line_vat is not None else gross - net)
        group['gross'] += gross
    def order(item):
        try:
            return (0, -Decimal(item['code']))
        except Exception:
            return (1, item['code'])
    return sorted(groups.values(), key=order)


def summary_html(positions, *, paper=False, accepted_amounts=False):
    groups = vat_summary(positions, accepted_amounts=accepted_amounts)
    if not groups:
        return 'Podsumowanie VAT — brak pozycji'
    rows = []
    for group in groups:
        code = group['code']
        label = code + '%' if code.replace('.', '', 1).isdigit() else code
        rows.append(f"<tr><td>{html.escape(label)}</td><td align='right'>{group['net']:.2f}</td>"
                    f"<td align='right'>{group['vat']:.2f}</td><td align='right'>{group['gross']:.2f}</td></tr>")
    values = [sum((group[key] for group in groups), Decimal('0.00')) for key in ('net', 'vat', 'gross')]
    rows.append(f"<tr><td><b>Razem</b></td><td align='right'><b>{values[0]:.2f}</b></td>"
                f"<td align='right'><b>{values[1]:.2f}</b></td><td align='right'><b>{values[2]:.2f}</b></td></tr>")
    border = '1' if paper else '0'
    return (f'<b>Podsumowanie VAT</b><br><table width="100%" border="{border}" cellspacing="0" cellpadding="5">'
            '<tr><th align="left">Stawka VAT</th><th align="right">Netto</th><th align="right">VAT</th><th align="right">Brutto</th></tr>'
            + ''.join(rows) + '</table>')


class VatSummary(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.set_positions([])

    def set_positions(self, positions):
        self.setText(summary_html(positions))
