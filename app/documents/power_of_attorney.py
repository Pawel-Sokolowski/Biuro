"""Build unsigned, schema-valid CRD data for print preparation.

Paper signatures and authentication fields are never fabricated. In particular,
the old UPL XSD makes an authentication date mandatory inside its signature
block, so that block is omitted from unsigned XML; paper signers are printed
separately from the reviewed representative data.
"""
from datetime import date, datetime
import re

from lxml import etree as E

from app.documents.crd_forms import VERSIONS, validate_xml

NS = dict(adr='http://crd.gov.pl/xml/schematy/adres/2009/11/09/',
          oso='http://crd.gov.pl/xml/schematy/osoba/2009/11/16/',
          inst='http://crd.gov.pl/xml/schematy/instytucja/2009/11/16/',
          meta='http://crd.gov.pl/xml/schematy/meta/2009/11/16/',
          str='http://crd.gov.pl/xml/schematy/struktura/2009/11/16/')
OFFICE_EMAIL = 'office@example.test'


def formatted_address(data):
    street = ' '.join(str(data.get(key) or '').strip() for key in ('street', 'building')).strip()
    if data.get('apartment'):
        street += '/' + str(data['apartment']).strip()
    locality = ' '.join(str(data.get(key) or '').strip() for key in ('postcode', 'city')).strip()
    return ', '.join(value for value in (street, locality) if value)


def split_street(line):
    line = str(line or '').strip()
    match = re.fullmatch(r'(.*?)\s+(\d+[\w-]*)(?:/(\w+))?', line)
    return dict(street=match[1].strip(), building=match[2], apartment=match[3] or '') if match else dict(street=line, building='', apartment='')


def office_address():
    from app.db.dao_app_settings import get_setting
    country = get_setting('faktury_sprzedawca_kraj') or 'PL'
    if country.casefold() in ('polska', 'poland'):
        country = 'PL'
    elif len(country) == 2:
        country = country.upper()
    result = split_street(get_setting('faktury_sprzedawca_adres'))
    result.update(postcode=get_setting('faktury_sprzedawca_kod') or '',
                  city=get_setting('faktury_sprzedawca_miasto') or '', country=country,
                  region=get_setting('faktury_sprzedawca_wojewodztwo') or '',
                  county=get_setting('faktury_sprzedawca_powiat') or '',
                  municipality=get_setting('faktury_sprzedawca_gmina') or '',
                  email=OFFICE_EMAIL)
    return result


def resolve_employee(data):
    """Names come from the selected employee; correspondence is always the office's."""
    from app.db.dao_employees import get_employee_info
    selected = data.get('attorney') or {}
    employee = get_employee_info(selected.get('employee_id')) if selected.get('employee_id') else None
    if not employee or not employee.get('aktywny'):
        raise ValueError('Wybierz aktywnego pracownika jako pełnomocnika.')
    office = office_address()
    if not office['street'] or not office['postcode'] or not office['city']:
        raise ValueError('Uzupełnij firmowy adres, kod i miejscowość w ustawieniach danych sprzedawcy.')
    attorney = {key: selected.get(key) or '' for key in ('pesel', 'nip', 'birth_date', 'identity_number', 'identity_country', 'identity_type')}
    attorney['identity_type'] = attorney.get('identity_type') or '1'
    attorney.update(office, employee_id=employee['id'], imie_pierwsze=employee['imie'], nazwisko=employee['nazwisko'])
    # A private employee e-mail/address is never copied into correspondence fields.
    return attorney


def _date(value):
    if isinstance(value, date):
        return value.isoformat()
    value = str(value or '').strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError('Uzupełnij poprawną datę pełnomocnictwa.')


def pesel_birth_date(pesel):
    if not re.fullmatch(r'[0-9]{11}', str(pesel or '')):
        return ''
    year, month, day = int(pesel[:2]), int(pesel[2:4]), int(pesel[4:6])
    for lower, century in ((80, 1800), (60, 2200), (40, 2100), (20, 2000), (0, 1900)):
        if lower < month <= lower + 12:
            try:
                return date(century + year, month - lower, day).isoformat()
            except ValueError:
                return ''
    return ''


def principal_kind_for_company(company):
    """Use the recorded legal form; KRS alone does not imply legal personality."""
    form = ' '.join(str(company.get('forma_dzialalnosci') or '').strip().casefold().split())
    if form == 'jdg' or form.startswith('osoba fizyczna'):
        return 'person'
    if form in ('spółka cywilna', 'spółka jawna', 'spółka komandytowa', 'spółka partnerska',
                'spółka komandytowo-akcyjna'):
        return 'other'
    if form in ('spółka z o.o.', 'spółka z ograniczoną odpowiedzialnością', 'spółka akcyjna',
                'prosta spółka akcyjna', 'fundacja', 'stowarzyszenie'):
        return 'legal'
    return ''


def reviewed_data(data, kind):
    attorney = dict(data.get('attorney') or {})
    attorney['email'] = OFFICE_EMAIL
    attorney['electronic_address'] = ''
    for key in ('imie_pierwsze', 'nazwisko'):
        if not str(attorney.get(key) or '').strip():
            raise ValueError('Uzupełnij imię i nazwisko pełnomocnika.')
    pesel = str(attorney.get('pesel') or '').strip()
    nip = re.sub(r'[-\s]', '', str(attorney.get('nip') or ''))
    identity = str(attorney.get('identity_number') or '').strip()
    if pesel and not re.fullmatch('[0-9]{11}', pesel):
        raise ValueError('PESEL pełnomocnika musi mieć 11 cyfr.')
    if nip and not re.fullmatch('[0-9]{10}', nip):
        raise ValueError('NIP pełnomocnika musi mieć 10 cyfr.')
    if not (pesel or nip or identity):
        raise ValueError('Uzupełnij PESEL, NIP albo dokument tożsamości pełnomocnika.')
    if kind in ('PEL', 'PEL-PAPER', 'PEL-Z') and not (pesel or identity):
        raise ValueError('W PEL podaj PESEL pełnomocnika albo jego dokument tożsamości.')
    attorney.update(pesel=pesel, nip=nip, identity_number=identity)
    attorney['birth_date'] = _date(attorney['birth_date']) if attorney.get('birth_date') else pesel_birth_date(pesel)
    if kind == 'UPL' and not attorney['birth_date']:
        raise ValueError('Przesłany schemat UPL wymaga daty urodzenia pełnomocnika. Wpisz ją lub uzupełnij PESEL.')
    principal_kind = data.get('principal_kind')
    if principal_kind not in ('person', 'legal', 'other'):
        raise ValueError('Wybierz rodzaj podmiotu udzielającego pełnomocnictwa.')
    principal_nip = re.sub(r'[-\s]', '', str(data.get('nip') or ''))
    principal_pesel = str(data.get('pesel') or '').strip()
    if principal_nip and not re.fullmatch('[0-9]{10}', principal_nip):
        raise ValueError('NIP klienta musi mieć 10 cyfr.')
    if principal_pesel and not re.fullmatch('[0-9]{11}', principal_pesel):
        raise ValueError('PESEL klienta musi mieć 11 cyfr.')
    if not (principal_nip or (principal_kind == 'person' and principal_pesel)):
        raise ValueError('Uzupełnij NIP klienta albo PESEL osoby fizycznej.')
    if principal_kind == 'person' and not (data.get('principal_first_name') and data.get('principal_surname')):
        raise ValueError('Dla osoby fizycznej uzupełnij imię i nazwisko mocodawcy.')
    start = _date(data.get('authority_from') or data.get('agreement_date'))
    # Office authorizations always remain valid until revoked, including when
    # an older caller supplies a previously selected expiry date.
    end = ''
    if kind in ('PEL', 'PEL-PAPER') and data.get('pel_scope') not in ('all', 'receive', 'correspondence', 'case'):
        raise ValueError('Wybierz zakres pełnomocnictwa PEL.')
    if kind == 'PEL' and len(str(data.get('short_name') or '')) > 31:
        raise ValueError('Nazwa skrócona do PEL może mieć maksymalnie 31 znaków. Popraw ją w zakładce UPL / PEL.')
    if kind in ('PEL', 'PEL-PAPER') and data.get('pel_scope') == 'case' and not str(data.get('pel_case') or '').strip():
        raise ValueError('Opisz sprawę objętą pełnomocnictwem PEL.')
    if kind == 'UPL' and not str(data.get('tax_office') or '').strip():
        raise ValueError('Uzupełnij Urząd Skarbowy dla UPL.')
    return dict(data, attorney=attorney, nip=principal_nip, pesel=principal_pesel,
                authority_from=start, authority_until=end)


def build_xml(kind, data):
    data = reviewed_data(data, kind)
    ns = VERSIONS[kind]
    root = E.Element('{' + ns + '}Dokument', nsmap=dict(wnio=ns, **NS))

    def add(parent, key, text=None, prefix=None):
        node = E.SubElement(parent, '{' + (NS[prefix] if prefix else ns) + '}' + key)
        if text is not None:
            node.text = str(text)
        return node

    def address(parent, values, pel=False):
        mappings = (('KodPocztowy', 'postcode'), ('Poczta', 'post_office'), ('Miejscowosc', 'city'),
                    ('Ulica', 'street'), ('Budynek', 'building'), ('Lokal', 'apartment'), ('Kraj', 'country'),
                    ('Wojewodztwo', 'region'), ('Powiat', 'county'), ('Gmina', 'municipality'))
        if pel:
            mappings = (('KodPocztowy', 'postcode'), ('Poczta', 'post_office'), ('GminaDzielnica', 'municipality'),
                        ('Miejscowosc', 'city'), ('Ulica', 'street'), ('NumerDomu', 'building'), ('NumerLokalu', 'apartment'),
                        ('NumerTelefonu', 'phone'), ('SymbolPanstwa', 'country'), ('AdresPocztyElektronicznej', 'email'))
        for key, field in mappings:
            if values.get(field):
                add(parent, key, values[field], None if pel else 'adr')

    def contact(parent, values):
        for key, field in (('Telefon', 'phone'), ('Email', 'email')):
            if values.get(field):
                add(parent, key, values[field], 'adr')

    attorney = data['attorney']
    if kind == 'UPL':
        add(root, 'OpisDokumentu')
        doc = add(root, 'DaneDokumentu')
        subject = add(add(doc, 'Nadawcy', prefix='str'), 'Podmiot', prefix='meta')
        if data['principal_kind'] == 'person':
            person = add(subject, 'Osoba', prefix='oso')
            add(person, 'Imie', data['principal_first_name'], 'oso')
            add(person, 'Nazwisko', data['principal_surname'], 'oso')
            entity = person
        else:
            entity = add(subject, 'Instytucja', prefix='inst')
            add(entity, 'NazwaInstytucji', data['name'], 'inst')
        address(add(entity, 'Adres', prefix='adr'), data)
        contact(add(entity, 'Kontakt', prefix='adr'), data)
        content = add(root, 'TrescDokumentu')
        content.set('format', 'text/xml'); content.set('kodowanie', 'XML')
        value = add(content, 'Wartosc')
        office = add(value, 'AdresyUrzedow')
        for key, text in (('NazwaUrzedu', data['tax_office']), ('Pobierz', ''), ('DaneUrzedu', data['tax_office'])):
            add(office, key, text)
        identifier_kind = 'NIP' if data['nip'] else 'PESEL'
        add(add(value, 'Identyfikator' + identifier_kind), identifier_kind, data['nip'] or data['pesel'])
        person = add(value, 'Pelnomocnik')
        if attorney['pesel']:
            add(add(person, 'IdentyfikatorPESEL'), 'PESEL', attorney['pesel'], 'oso')
        elif attorney['nip']:
            add(add(person, 'IdentyfikatorNIP'), 'NIP', attorney['nip'], 'oso')
        add(person, 'DataUrodzenia', attorney['birth_date'])
        address(add(person, 'Adres', prefix='adr'), attorney)
        contact(add(person, 'Kontakt', prefix='adr'), attorney)
        add(person, 'Identyfikator', '2' if attorney['pesel'] or not attorney['nip'] else '1')
        if attorney.get('electronic_address'):
            add(person, 'URIKontakt', attorney['electronic_address'], 'adr')
        add(person, 'KrajWydania', attorney.get('identity_country', ''))
        add(person, 'NumerDokumentu', attorney['identity_number'])
        add(person, 'Nazwisko', attorney['nazwisko'], 'oso'); add(person, 'Imie', attorney['imie_pierwsze'], 'oso')
        for key, text in (('TypIdentyfikatoraPodatnika', '1' if data['nip'] else '2'),
                          ('NumerEgzemplarza', str(data.get('copy_number', 1))), ('LiczbaEgzemplarzy', str(data.get('copy_count', 1)))):
            add(value, key, text)
        period = add(value, 'OkresObowiazywania')
        if data['authority_until']:
            add(add(period, 'Waznosc'), 'DataDo', data['authority_until'])
        add(period, 'Bezterminowo', '' if data['authority_until'] else '1')
        add(period, 'DataOd', data['authority_from'])
        for key, text in (('PodmiotZobowiazany', '2' if data['principal_kind'] == 'person' else '1'),
                          ('PodstawaPrawna', 'Art. 80a Ordynacji podatkowej'), ('PodmiotZagraniczny', ''),
                          ('Naczelnik4', '1'), ('Naczelnik5', '')):
            add(value, key, text)
    else:
        content = add(root, 'TrescDokumentu')
        content.set('format', 'text/xml'); content.set('kodowanie', 'XML')
        principal = add(content, 'DaneMocodawcy')
        add(add(principal, 'PodmiotUdzielajacyPelnomocnictwa'), 'Platnik', 'true')
        kind_node = {'person': 'OsobaFizyczna', 'legal': 'OsobaPrawna', 'other': 'InnyPodmiot'}[data['principal_kind']]
        add(add(principal, 'ReprezentowanyPlatnikSkladek'), kind_node, 'true')
        identity = add(content, 'DaneIdentyfikacyjneMocodawcy')
        for key, text in (('NIP', data['nip']), ('REGON', data.get('regon')), ('PESEL', data['pesel']),
                          ('NazwaSkrocona', data.get('short_name')), ('NazwaPelna', data['name']),
                          ('Nazwisko', data.get('principal_surname')), ('Imie', data.get('principal_first_name'))):
            if text:
                add(identity, key, text)
        address(add(content, 'AdresSiedzibyPlatnikaSkladek'), data, pel=True)
        scope = add(content, 'ZakresPelnomocnictwa'); rights = add(scope, 'ZakresUprawnienia')
        mode = data['pel_scope']
        if mode == 'all':
            add(rights, 'UprawnienieDoWszelkiejCzynnosci', 'true')
        elif mode in ('receive', 'correspondence'):
            add(rights, 'UprawnienieDoOkreslonejCzynnosci', 'true'); activity = add(rights, 'Czynnosc')
            add(activity, 'OdbiorKorespondencji', 'true')
            if mode == 'correspondence': add(activity, 'WyslanieKorespondencji', 'true')
        else:
            add(rights, 'UprawnienieDoOkreslonejSprawy', 'true'); case = add(rights, 'Sprawa')
            add(case, 'DokonaniePierwszejDalszychCzynnosciSprawie', 'true')
            add(case, 'OpisSprawy', data['pel_case'])
        add(scope, 'DataUstanowienia', data['authority_from'])
        period = add(scope, 'OkresObowiazywaniaPelnomocnictwa')
        add(period, 'DoDnia' if data['authority_until'] else 'DoOdwolania', 'true')
        if data['authority_until']: add(period, 'Data', data['authority_until'])
        person = add(content, 'DanePelnomocnika'); identity = add(person, 'DaneIdentyfikacyjne')
        if attorney['pesel']:
            add(identity, 'PESEL', attorney['pesel'])
        else:
            add(identity, 'RodzajDokumentu', attorney.get('identity_type', '1'))
            add(identity, 'SeriaINumerDokumentu', attorney['identity_number'])
        add(identity, 'Nazwisko', attorney['nazwisko']); add(identity, 'Imie', attorney['imie_pierwsze'])
        address(add(person, 'DaneAdresowe'), attorney, pel=True)
        declaration = add(content, 'OswiadczenieOUstanowieniuPelnomocnika')
        representatives = [r for r in data.get('representatives', ()) if r.get('nazwisko') or r.get('imie_pierwsze')]
        if len(representatives) > 3:
            raise ValueError('Dostarczony wzór CRD PEL mieści trzech przedstawicieli. Użyj aktualnego PEL z PEL-Z dla większej reprezentacji.')
        for index, representative in enumerate(representatives, 1):
            person = add(declaration, 'Mocodawca' + str(index))
            for key, field in (('PESEL', 'pesel'), ('Nazwisko', 'nazwisko'), ('Imie', 'imie_pierwsze'), ('StanowiskoFunkcja', 'stanowisko')):
                if representative.get(field): add(person, key, representative[field])
            if data.get('city'): add(person, 'Miejscowosc', data['city'])
            add(person, 'Data', _date(data.get('agreement_date')))
    xml = E.tostring(root, encoding='UTF-8', xml_declaration=True, pretty_print=True)
    validate_xml(kind, xml)
    return xml
