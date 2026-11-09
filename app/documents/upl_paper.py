"""Reviewed placement for the exact two-page UPL-1(6) PDF supplied by the user.

Coordinates are PDF points checked against this template's table borders and
numbered labels. Users never create fields or position text. A checksum prevents
silently applying this layout to a different PDF revision.
"""
import hashlib
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from app.documents.power_of_attorney import reviewed_data

TEMPLATE = Path(__file__).with_name('templates') / 'upl-1.pdf'
TEMPLATE_SHA256 = '22abb8e3e254343d38abcf0eb8389f0847150c831f62bb2bb9cf604edfe352db'


def overlay_commands(data):
    data = reviewed_data(data, 'UPL-PAPER')
    if hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() != TEMPLATE_SHA256:
        raise ValueError('Wzór PDF UPL ma inną wersję niż sprawdzony szablon.')
    pages = [dict(size=(float(p.mediabox.width), float(p.mediabox.height)), text=[]) for p in PdfReader(TEMPLATE).pages]
    if len(pages) != 2:
        raise ValueError('Wzór UPL musi mieć dwie strony.')
    def text(page, x, y, value, width=250):
        if value:
            value = str(value)
            if '@' not in value and not value.startswith(('/', 'http:', 'https:')):
                value = value.upper()
            pages[page]['text'].append((x, y, value, width))
    def digits(page, x, y, value, pitch=15):
        for index, char in enumerate(str(value)):
            text(page, x + index * pitch + 3, y, char, pitch - 3)
    def date_grid(x, y, value):
        if not value:
            return
        year, month, day = value.split('-')
        digits(1, x, y, day); digits(1, x + 34.68, y, month); digits(1, x + 69.36, y, year)
    digits(0, 110.04, 780.8, data['nip'] or data['pesel'])
    if data.get('copy_count', 1) > 1:
        text(0, 455, 688, f"{data.get('copy_number', 1)} / {data['copy_count']}", 108)
    text(0, 64.5, 599, 'X', 10)
    text(0, 165 if data['principal_kind'] != 'person' else 386, 488.5, 'X', 10)
    name = (data.get('principal_surname', '') + ' ' + data.get('principal_first_name', '')) if data['principal_kind'] == 'person' else data['name']
    text(0, 54, 458, name, 510)
    text(0, 77, 591, data['tax_office'], 485)
    address0 = [('country', 54, 406, 100), ('region', 162, 406, 244), ('county', 414, 406, 150),
                ('municipality', 54, 385, 122), ('street', 184, 385, 266), ('building', 457, 385, 50),
                ('apartment', 515, 385, 50), ('city', 54, 364, 215), ('postcode', 277, 364, 108),
                ('post_office', 393, 364, 170), ('phone', 55, 323, 250), ('email', 55, 300, 508)]
    address1 = [('country', 50, 760, 104), ('region', 162, 760, 224), ('county', 392, 760, 168),
                ('municipality', 50, 736, 130), ('street', 187, 736, 260), ('building', 453, 736, 50),
                ('apartment', 511, 736, 50), ('city', 50, 712, 215), ('postcode', 274, 712, 78),
                ('post_office', 360, 712, 200), ('phone', 50, 662, 250), ('email', 50, 639, 510)]
    for key, x, y, width in address0:
        value = 'POLSKA' if key == 'country' and data.get(key) == 'PL' else data.get(key)
        text(0, x, y, value, width)
    attorney = data['attorney']
    for key, x, y, width in address1:
        value = 'POLSKA' if key == 'country' and attorney.get(key) == 'PL' else attorney.get(key)
        text(1, x, y, value, width)
    digits(0, 223.44, 226.4, attorney['pesel'] or attorney['nip'])
    text(0, 55, 204, attorney['nazwisko']); text(0, 315, 204, attorney['imie_pierwsze'])
    if not (attorney['pesel'] or attorney['nip']):
        text(0, 55, 181, attorney['birth_date'], 508)
        text(0, 55, 157, attorney['identity_number'], 508)
        text(0, 55, 135, attorney.get('identity_country'), 250)
    text(0, 320, 135, attorney.get('electronic_address'), 244)
    date_grid(170.04, 593.84, data['authority_from'])
    date_grid(429.24, 593.84, data['authority_until'])
    representatives = [r for r in data.get('representatives', ()) if r.get('nazwisko') or r.get('imie_pierwsze')]
    if len(representatives) > 3:
        raise ValueError('Jeden UPL mieści trzech przedstawicieli. Przygotuj kolejny egzemplarz dla pozostałych osób.')
    for index, person in enumerate(representatives):
        surname_y, role_y = ((521, 499), (477, 455), (433, 411))[index]
        text(1, 50, surname_y, person.get('nazwisko'))
        text(1, 310, surname_y, person.get('imie_pierwsze'))
        if data['principal_kind'] != 'person':
            text(1, 50, role_y, person.get('stanowisko'))
    from app.documents.power_of_attorney import _date
    date_grid(239.76, 390.56, _date(data['agreement_date']))
    return pages


def write_pdf(data, destination, renderer):
    overlay = Path(destination).with_suffix('.overlay.pdf')
    try:
        renderer('overlay', overlay_commands(data), overlay)
        merge_overlay(TEMPLATE, overlay, destination)
    finally:
        overlay.unlink(missing_ok=True)


def merge_overlay(template, overlay, destination):
    layer, writer = PdfReader(overlay), PdfWriter(clone_from=template)
    try:
        if len(layer.pages) != len(writer.pages):
            raise RuntimeError('Nieprawidłowa liczba stron warstwy danych formularza.')
        for page, added in zip(writer.pages, layer.pages):
            page.merge_page(added)
        writer.write(destination)
    finally:
        writer.close()
