"""Generate and validate domestic PLN FA(3) invoices from stored invoice data."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from lxml import etree as E
from app.invoices.ksef_client import KsefError
from app.invoices.positions import normalize_position
from app.invoices.vat_summary import vat_summary, vat_code

NS = 'http://crd.gov.pl/wzor/2025/06/25/13775/'


def _element(parent, name, text=None, **attrs):
    node = E.SubElement(parent, '{'+NS+'}'+name, **attrs)
    if text is not None:
        node.text = str(text)
    return node


def _required(value, label):
    text = str(value or '').strip()
    if not text:
        raise KsefError('Uzupełnij '+label+' przed wysyłką do KSeF.')
    return text


def _date(value, label):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise KsefError('Nieprawidłowa data: '+label) from exc


def _amount(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise KsefError('Nieprawidłowa kwota faktury.') from exc


@lru_cache(maxsize=1)
def _schema():
    parser = E.XMLParser(resolve_entities=False, no_network=True)
    return E.XMLSchema(E.parse(str(Path(__file__).parent/'schemas'/'fa3'/'fa3.xsd'), parser))


def validate_fa3(xml):
    try:
        root = E.fromstring(xml, E.XMLParser(resolve_entities=False, no_network=True))
        _schema().assertValid(root)
    except (E.XMLSyntaxError, E.DocumentInvalid, E.XMLSchemaParseError, OSError) as exc:
        raise KsefError('Dokument FA(3) nie przeszedł walidacji: '+str(exc)) from exc


def build_fa3_xml(invoice, positions, seller, *, corrected=None):
    """Supported domestic rates: 23/22, 8/7, 5, 0 KR and exemption with basis.

    No tax procedure is inferred from a company name or an amount. Special
    foreign/reverse-charge/margin cases need explicit data and are rejected.
    """
    if (invoice.get('waluta') or 'PLN') != 'PLN':
        raise KsefError('Automatyczna wysyłka obsługuje obecnie faktury w PLN.')
    if invoice.get('rodzaj', 'faktura') not in ('faktura', 'korekta'):
        raise KsefError('Nieobsługiwany rodzaj dokumentu do automatycznej wysyłki.')
    for field in ('procedura_marzy', 'odwrotne_obciazenie', 'samofakturowanie'):
        if invoice.get(field):
            raise KsefError('Dokument wymaga dodatkowych danych procedury podatkowej: '+field)
    seller_nip = ''.join(c for c in str(seller.get('nip') or '') if c.isdigit())
    buyer_nip = ''.join(c for c in str(invoice.get('nabywca_nip') or '') if c.isdigit())
    if len(seller_nip) != 10 or (invoice.get('nabywca_nip') and len(buyer_nip) != 10):
        raise KsefError('NIP sprzedawcy i NIP krajowego nabywcy muszą mieć po 10 cyfr.')
    country = str(invoice.get('nabywca_kraj') or 'PL').strip().casefold()
    if country not in ('pl', 'polska', 'poland'):
        raise KsefError('Automatyczna wysyłka wymaga obsługiwanych danych krajowego nabywcy.')
    if not positions:
        raise KsefError('Faktura nie zawiera pozycji.')
    normalized = []
    for source in positions:
        item = normalize_position(source, preserve_amounts=True)
        for field in ('ilosc', 'cena_netto', 'netto', 'brutto'):
            if not Decimal(str(item[field])).is_finite():
                raise KsefError('Pozycja zawiera nieprawidłową liczbę.')
        if item['ilosc'] == 0:
            raise KsefError('Pozycja faktury ma zerową ilość.')
        normalized.append(item)
    groups = vat_summary(normalized, accepted_amounts=True)
    for group in groups:
        if group['code'] not in ('23', '22', '8', '7', '5', '0', '0 kr', 'zw'):
            raise KsefError('Stawka lub procedura VAT wymaga dodatkowej obsługi: '+group['code'])
    net = sum((g['net'] for g in groups), Decimal('0'))
    gross = sum((g['gross'] for g in groups), Decimal('0'))
    if net != _amount(invoice.get('kwota_netto')) or gross != _amount(invoice.get('kwota_brutto')):
        raise KsefError('Sumy pozycji nie zgadzają się z kwotami faktury. Popraw dokument przed wysyłką.')
    is_correction = invoice.get('rodzaj') == 'korekta'
    if not is_correction and (net < 0 or gross < 0):
        raise KsefError('Ujemną fakturę należy wystawić jako korektę.')
    root = E.Element('{'+NS+'}Faktura', nsmap={None: NS})
    header = _element(root, 'Naglowek')
    _element(header, 'KodFormularza', 'FA', kodSystemowy='FA (3)', wersjaSchemy='1-0E')
    _element(header, 'WariantFormularza', '3')
    _element(header, 'DataWytworzeniaFa', datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'))
    _element(header, 'SystemInfo', 'Biuro')
    party = _element(root, 'Podmiot1')
    identity = _element(party, 'DaneIdentyfikacyjne')
    _element(identity, 'NIP', seller_nip)
    _element(identity, 'Nazwa', _required(seller.get('name'), 'nazwę sprzedawcy'))
    address = _element(party, 'Adres')
    _element(address, 'KodKraju', 'PL')
    _element(address, 'AdresL1', _required(seller.get('address'), 'adres sprzedawcy'))
    party = _element(root, 'Podmiot2')
    identity = _element(party, 'DaneIdentyfikacyjne')
    _element(identity, 'NIP' if buyer_nip else 'BrakID', buyer_nip or '1')
    _element(identity, 'Nazwa', _required(invoice.get('nabywca_pelna'), 'nazwę nabywcy'))
    address = _element(party, 'Adres')
    _element(address, 'KodKraju', 'PL')
    _element(address, 'AdresL1', _required(invoice.get('nabywca_adres'), 'adres nabywcy'))
    city = ' '.join(filter(None, (invoice.get('nabywca_kod'), invoice.get('nabywca_miasto'))))
    if city:
        _element(address, 'AdresL2', city)
    _element(party, 'JST', '2')
    _element(party, 'GV', '2')
    fa = _element(root, 'Fa')
    _element(fa, 'KodWaluty', 'PLN')
    _element(fa, 'P_1', _date(invoice.get('data_wystawienia'), 'wystawienia'))
    _element(fa, 'P_2', _required(invoice.get('numer'), 'numer faktury'))
    if invoice.get('data_sprzedazy'):
        _element(fa, 'P_6', _date(invoice['data_sprzedazy'], 'sprzedaży'))
    for rates, net_field, tax_field in ((('23','22'), 'P_13_1', 'P_14_1'), (('8','7'), 'P_13_2', 'P_14_2'),
                                       (('5',), 'P_13_3', 'P_14_3'), (('0','0 kr'), 'P_13_6_1', None), (('zw',), 'P_13_7', None)):
        selected = [g for g in groups if g['code'] in rates]
        if selected:
            _element(fa, net_field, f"{sum((g['net'] for g in selected), Decimal('0')):.2f}")
            if tax_field:
                _element(fa, tax_field, f"{sum((g['vat'] for g in selected), Decimal('0')):.2f}")
    _element(fa, 'P_15', f'{gross:.2f}')
    notes = _element(fa, 'Adnotacje')
    for name in ('P_16', 'P_17', 'P_18', 'P_18A'):
        _element(notes, name, '1' if (name == 'P_18A' and invoice.get('mechanizm_podzielonej_platnosci')) or (name == 'P_16' and invoice.get('metoda_kasowa')) else '2')
    exemption = _element(notes, 'Zwolnienie')
    if any(g['code'] == 'zw' for g in groups):
        _element(exemption, 'P_19', '1')
        _element(exemption, 'P_19A', _required(invoice.get('podstawa_zwolnienia'), 'podstawę zwolnienia z VAT'))
    else:
        _element(exemption, 'P_19N', '1')
    _element(_element(notes, 'NoweSrodkiTransportu'), 'P_22N', '1')
    _element(notes, 'P_23', '2')
    _element(_element(notes, 'PMarzy'), 'P_PMarzyN', '1')
    _element(fa, 'RodzajFaktury', 'KOR' if is_correction else 'VAT')
    if is_correction:
        if not corrected:
            raise KsefError('Brak oryginału faktury korygowanej.')
        _element(fa, 'PrzyczynaKorekty', _required(invoice.get('przyczyna_korekty'), 'przyczynę korekty'))
        original = _element(fa, 'DaneFaKorygowanej')
        _element(original, 'DataWystFaKorygowanej', _date(corrected.get('data_wystawienia'), 'oryginału'))
        _element(original, 'NrFaKorygowanej', _required(corrected.get('numer'), 'numer oryginału'))
        if corrected.get('ksef_numer'):
            _element(original, 'NrKSeF', '1')
            _element(original, 'NrKSeFFaKorygowanej', corrected['ksef_numer'])
        else:
            _element(original, 'NrKSeFN', '1')
    for number, item in enumerate(normalized, 1):
        row = _element(fa, 'FaWiersz')
        _element(row, 'NrWierszaFa', number)
        _element(row, 'P_7', _required(item.get('nazwa'), 'nazwę pozycji'))
        _element(row, 'P_8A', item.get('jednostka') or 'szt.')
        _element(row, 'P_8B', format(Decimal(str(item['ilosc'])), 'f'))
        _element(row, 'P_9A', f"{_amount(item['cena_netto']):.2f}")
        _element(row, 'P_11', f"{_amount(item['netto']):.2f}")
        _element(row, 'P_11Vat', f"{_amount(item['brutto'])-_amount(item['netto']):.2f}")
        code = vat_code(item)
        _element(row, 'P_12', '0 KR' if code in ('0', '0 kr') else code)
    payment = _element(fa, 'Platnosc')
    if invoice.get('termin_platnosci'):
        _element(_element(payment, 'TerminPlatnosci'), 'Termin', _date(invoice['termin_platnosci'], 'termin płatności'))
    method = str(invoice.get('sposob_zaplaty') or 'przelew').casefold()
    if method not in ('przelew','gotówka','gotowka','karta'):
        raise KsefError('Nieobsługiwany sposób zapłaty: '+method)
    _element(payment, 'FormaPlatnosci', {'przelew':'6','gotówka':'1','gotowka':'1','karta':'2'}[method])
    if seller.get('account'):
        _element(_element(payment, 'RachunekBankowy'), 'NrRB', ''.join(str(seller['account']).split()))
    xml = E.tostring(root, encoding='UTF-8', xml_declaration=True)
    validate_fa3(xml)
    return xml
