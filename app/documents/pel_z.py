"""Fill the supplied flat PEL-Z using its labels and vector field borders."""
import hashlib
from pathlib import Path

from pypdf import PdfReader

from app.documents.power_of_attorney import _date, pesel_birth_date, reviewed_data

TEMPLATE = Path(__file__).with_name('templates') / 'pel-z.pdf'
TEMPLATE_SHA256 = '88e9f579d47454a85649da172da7252a2eb3b59728e2ab8cb3e2b56c0032aaca'


def _geometry(page):
    labels, rows, point = [], {}, None
    def text(value, cm, tm, font, size):
        if value.strip():
            labels.append((value.strip(), float(tm[5])))
    def operator(op, args, cm, tm):
        nonlocal point
        if op == b'm':
            point = tuple(map(float, args))
        elif op == b'l' and point:
            x0, y0 = point
            x1, y1 = map(float, args)
            point = x1, y1
            a, b, c, d, e, f = cm
            xx0, yy0 = a * x0 + c * y0 + e, b * x0 + d * y0 + f
            xx1, yy1 = a * x1 + c * y1 + e, b * x1 + d * y1 + f
            if abs(xx0 - xx1) < 0.1 and 16.8 < abs(yy0 - yy1) < 18:
                rows.setdefault(round(min(yy0, yy1), 1), set()).add(round(xx0, 2))
    page.extract_text(visitor_text=text, visitor_operand_before=operator)
    return labels, rows


def overlay_commands(data):
    data = reviewed_data(data, 'PEL-Z')
    if hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() != TEMPLATE_SHA256:
        raise ValueError('PEL-Z ma inną wersję niż sprawdzony szablon.')
    reader = PdfReader(TEMPLATE)
    pages = [dict(size=(float(p.mediabox.width), float(p.mediabox.height)), text=[]) for p in reader.pages]
    geometry = [_geometry(p) for p in reader.pages]
    def field(page, label, value, occurrence=0, part=None, digits=False):
        if not value:
            return
        labels, rows = geometry[page]
        matches = [y for name, y in labels if name == label]
        if len(matches) <= occurrence:
            raise ValueError('Nie znaleziono pola PEL-Z: ' + label)
        y = matches[occurrence]
        # The two-line identity label's baseline sits fractionally below the
        # adjacent border; tolerate PDF coordinate rounding, not a different row.
        matching = [(base, sorted(xs)) for base, xs in rows.items() if base - 0.75 <= y <= base + 18.1]
        if len(matching) != 1:
            raise ValueError('Niejednoznaczna geometria pola PEL-Z: ' + label)
        base, xs = matching[0]
        if digits:
            text = str(value)
            cells = [(left, right) for left, right in zip(xs, xs[1:]) if 17 < right - left < 19]
            if len(text) > len(cells) or not text.isascii() or not text.isdigit():
                raise ValueError('Niepoprawna liczba cyfr pola PEL-Z: ' + label)
            for char, (left, right) in zip(text, cells):
                pages[page]['text'].append((left + 4.5, base + 4, char, right - left - 5))
        else:
            if part is not None:
                if len(xs) != 4:
                    raise ValueError('Nieprawidłowa geometria podzielonego pola PEL-Z.')
                left, right = xs[part * 2:part * 2 + 2]
            else:
                left, right = xs[0], xs[-1]
            pages[page]['text'].append((left + 4, base + 4, str(value).upper(), right - left - 8))
    attorney = data['attorney']
    if not data['nip']:
        raise ValueError('PEL-Z wymaga NIP podmiotu.')
    field(0, 'PESEL', attorney.get('pesel'), digits=True)
    if not attorney.get('pesel'):
        field(0, 'potwierdzającego tożsamość', attorney.get('identity_number'), occurrence=0)
    field(0, 'NIP', data['nip'], digits=True)
    filled_on = _date(data['agreement_date'])
    year, month, day = filled_on.split('-')
    field(0, 'Data wypełnienia', day + month + year, digits=True)
    # Fixed checkbox locations belong to this checksummed PEL-Z version.
    pages[0]['text'].extend([(118, 718, 'X', 13), (207, 381, 'X', 13)])
    representatives = [r for r in data.get('representatives', ()) if r.get('imie_pierwsze') or r.get('nazwisko')]
    if not representatives:
        raise ValueError('Wybierz przedstawicieli do PEL-Z.')
    if len(representatives) > 4:
        raise ValueError('Dostarczony PEL-Z mieści czterech przedstawicieli.')
    for index, person in enumerate(representatives):
        page, occurrence = ((0, 0), (1, 0), (1, 1), (2, 0))[index]
        pesel = str(person.get('pesel') or '')
        if pesel and (len(pesel) != 11 or not pesel.isascii() or not pesel.isdigit()):
            raise ValueError('PESEL przedstawiciela PEL-Z musi mieć 11 cyfr albo pozostać pusty.')
        field(page, f'Przedstawiciel {index + 1} PESEL', pesel, digits=True)
        birthday = person.get('birth_date') or pesel_birth_date(pesel)
        if birthday:
            year, month, day = _date(birthday).split('-')
            field(page, 'Data urodzenia', day + month + year, occurrence, digits=True)
        if not pesel:
            field(page, 'potwierdzającego tożsamość', person.get('identity_number'), occurrence + (1 if page == 0 else 0))
        for label, key in (('Imię', 'imie_pierwsze'), ('Nazwisko', 'nazwisko'), ('Ulica', 'street'), ('Numer telefonu', 'nr_telefonu')):
            field(page, label, person.get(key), occurrence)
        field(page, 'Numer domu Numer lokalu', person.get('building'), occurrence, part=0)
        field(page, 'Numer domu Numer lokalu', person.get('apartment'), occurrence, part=1)
        field(page, 'Kod pocztowy Miejscowość', person.get('postcode'), occurrence, part=0)
        field(page, 'Kod pocztowy Miejscowość', person.get('city'), occurrence, part=1)
        country = str(person.get('country') or '')
        if country.casefold() not in ('', 'pl', 'polska', 'poland'):
            field(page, 'Nazwa państwa', country, occurrence)
    return pages


def write_pdf(data, destination, renderer):
    from app.documents.upl_paper import merge_overlay
    overlay = Path(destination).with_suffix('.overlay.pdf')
    try:
        renderer('overlay', overlay_commands(data), overlay)
        merge_overlay(TEMPLATE, overlay, destination)
    finally:
        overlay.unlink(missing_ok=True)
