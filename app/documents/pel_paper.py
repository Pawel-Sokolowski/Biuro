"""Placement on the exact three-page PEL supplied by the user (579/26)."""
import hashlib
from pathlib import Path

from pypdf import PdfReader

from app.documents.power_of_attorney import _date, pesel_birth_date, reviewed_data, formatted_address

TEMPLATE = Path(__file__).with_name('templates') / 'pel.pdf'
TEMPLATE_SHA256 = '72457d79e0499cfa4d0d289cde1af17635a8fca445e354441effb32f9a3e6afc'


def overlay_commands(data):
    data = reviewed_data(data, 'PEL-PAPER')
    if hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() != TEMPLATE_SHA256:
        raise ValueError('PEL ma inną wersję niż sprawdzony szablon.')
    reader = PdfReader(TEMPLATE)
    if len(reader.pages) != 3:
        raise ValueError('Wzór PEL musi mieć trzy strony.')
    pages = [dict(size=(float(p.mediabox.width), float(p.mediabox.height)), text=[]) for p in reader.pages]

    def text(page, x, y, value, width):
        if value:
            pages[page]['text'].append((x, y, str(value).upper(), width))

    def grid(page, x, y, value, count):
        value = str(value or '')
        if value and (not value.isascii() or not value.isdigit() or len(value) != count):
            raise ValueError('Niepoprawny identyfikator lub data w PEL.')
        for index, char in enumerate(value):
            text(page, x + 17.858 * index + 4.5, y, char, 12)

    def date(page, x, y, value):
        if value:
            year, month, day = _date(value).split('-')
            grid(page, x, y, day + month + year, 8)

    def check(page, x, base):
        text(page, x + 4, base + 4, 'X', 12)

    def person(page, values, baselines):
        # Baselines are inside the original vector field borders, in PDF points.
        pesel_y, birth_y, identity_y, first_y, last_y, street_y, house_y, city_y, country_y, phone_y = baselines
        grid(page, 238.96, pesel_y, values.get('pesel'), 11)
        date(page, 238.96, birth_y, values.get('birth_date') or pesel_birth_date(str(values.get('pesel') or '')))
        if not values.get('pesel'):
            text(page, 243, identity_y, values.get('identity_number'), 305)
        for y, key in ((first_y, 'imie_pierwsze'), (last_y, 'nazwisko'), (street_y, 'street'), (phone_y, 'phone')):
            text(page, 243, y, values.get(key), 305)
        text(page, 243, house_y, values.get('building'), 63)
        text(page, 404, house_y, values.get('apartment'), 63)
        text(page, 243, city_y, values.get('postcode'), 63)
        text(page, 404, city_y, values.get('city'), 144)
        if str(values.get('country') or '').casefold() not in ('', 'pl', 'polska', 'poland'):
            text(page, 243, country_y, values.get('country'), 305)

    representatives = [r for r in data.get('representatives', ()) if r.get('imie_pierwsze') or r.get('nazwisko')]
    own_company = data['principal_kind'] == 'person'
    if own_company:
        principal = dict(data, imie_pierwsze=data['principal_first_name'], nazwisko=data['principal_surname'],
                         birth_date=data.get('principal_birth_date'), identity_number=data.get('principal_identity_number'))
        check(0, 42.77, 65.4)
        grid(0, 374.22, 69.4, data['nip'], 10)
    else:
        if not representatives or not all(representatives[0].get(k) for k in ('imie_pierwsze', 'nazwisko')):
            raise ValueError('Wybierz przedstawiciela udzielającego pełnomocnictwa PEL w imieniu podmiotu.')
        if len(representatives) > 1 and not data.get('pel_z_attached'):
            raise ValueError('Przy wielu przedstawicielach zaznacz także załącznik PEL-Z.')
        principal = dict(representatives[0])
        principal['phone'] = principal.get('nr_telefonu') or principal.get('telefon')
        check(1, 42.77, 729)
        grid(1, 185.64, 715.2, data['nip'], 10)
        if data.get('pel_z_attached'):
            check(1, 524.94, 729)
        text(1, 82, 687, data['name'], 466)
        text(1, 82, 673, formatted_address(data) + (', REGON: ' + str(data['regon']) if data.get('regon') else ''), 466)
    person(0, principal, (424.5, 398.7, 364.8, 330, 304.2, 278.3, 252.4, 226.6, 200.7, 166.9))
    person(1, data['attorney'], (613.2, 586.4, 550.7, 515, 488.2, 461.8, 435.1, 408.3, 381.5, 345.8))

    scope = data['pel_scope']
    if scope == 'all':
        check(1, 42.77, 266.5)
    else:
        check(1, 42.77, 113.8)
        description = {'receive': 'Odbiór korespondencji', 'correspondence': 'Odbiór i wysyłanie korespondencji'}.get(scope, data.get('pel_case'))
        text(1, 77, 91, description, 471)
    if data.get('pel_pit'):
        check(1, 42.77, 187.1)
    ezus = data.get('pel_ezus') or ''
    if ezus not in ('', 'full', 'correspondence', 'ezla'):
        raise ValueError('Wybierz poprawny zakres eZUS.')
    if ezus:
        check(2, 42.77, 729)
        row = 443.3 if own_company else 374.7
        check(2, 42.77, row)
        if ezus == 'correspondence':
            check(2, 310.64, row)
            text(2, 47, 335, data.get('pel_ezus_requests'), 501)
        elif ezus == 'ezla':
            check(2, 435.65, row)
    # The paper explicitly prohibits validity dates for a specific activity/case.
    if scope == 'all':
        date(2, 185.38, 252.1, data['authority_from'])
        date(2, 363.96, 252.1, data['authority_until'])
    date(2, 78.24, 147.1, data['agreement_date'])
    return pages


def write_pdf(data, destination, renderer):
    from app.documents.upl_paper import merge_overlay
    overlay = Path(destination).with_suffix('.overlay.pdf')
    try:
        renderer('overlay', overlay_commands(data), overlay)
        merge_overlay(TEMPLATE, overlay, destination)
    finally:
        overlay.unlink(missing_ok=True)
