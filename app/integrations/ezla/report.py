"""Read original PUE/eZUS report XML without repairing or importing it."""
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import re
from lxml import etree

MAX_BYTES = 10 * 1024 * 1024
MAX_DOCUMENTS = 20000
STATUSES = {'wystawione': 'Wystawione', 'anulowane': 'Anulowane', 'skorygowane': 'Skorygowane',
            'wsteczne - do wyjaśnienia': 'Wsteczne - do wyjaśnienia',
            'wsteczne - uzasadnione': 'Wsteczne - uzasadnione',
            'wsteczne - nie uzasadnione': 'Wsteczne - nie uzasadnione',
            'wsteczne - nieuzasadnione': 'Wsteczne - nie uzasadnione'}


@dataclass
class Certificate:
    employee_name: str
    employee_id: str = field(repr=False)
    identifier_type: str
    series: str
    number: str
    status: str
    issued_at: str
    start: str
    end: str
    issues: list[str] = field(default_factory=list)

    @property
    def masked_identifier(self):
        if not self.employee_id:
            return '—'
        if len(self.employee_id) <= 4:
            return '*' * len(self.employee_id)
        return '*' * (len(self.employee_id) - 4) + self.employee_id[-4:]

    @property
    def review(self):
        notes = list(self.issues)
        if self.status == 'Anulowane':
            notes.append('Anulowane — wymaga obsługi anulowania w Enovie')
        elif self.status == 'Skorygowane':
            notes.append('Skorygowane — sprawdź dokument powiązany w Enovie')
        elif self.status.startswith('Wsteczne'):
            notes.append('Zwolnienie wsteczne — wymaga sprawdzenia w Enovie')
        return '; '.join(notes) or 'Podgląd — nie zaimportowano'


@dataclass
class Report:
    nip: str
    payer_name: str
    generated_at: str
    certificates: list[Certificate]
    issues: list[str] = field(default_factory=list)


def _node(parent, tag, required=True):
    values = parent.findall(tag)
    if len(values) > 1 or (required and not values):
        raise ValueError('Nieprawidłowa struktura raportu e-ZLA: ' + tag)
    return values[0] if values else None


def _text(parent, tag, required=True):
    node = _node(parent, tag, required)
    if node is None:
        return ''
    if len(node):
        raise ValueError('Nieprawidłowe pole raportu e-ZLA: ' + tag)
    value = (node.text or '').strip()
    if len(value) > 256 or (required and not value):
        raise ValueError('Puste lub nieprawidłowe pole raportu e-ZLA: ' + tag)
    return value


def _date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Nieprawidłowa data raportu e-ZLA.')
    return date.fromisoformat(value).isoformat()


def parse_report(data: bytes) -> Report:
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Raport e-ZLA jest pusty lub przekracza 10 MB.')
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                             recover=False, huge_tree=False, remove_comments=True, remove_pis=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:
        raise ValueError('Niepoprawny XML. Wybierz oryginalny raport pobrany z ZUS, nie tekst skopiowany z PDF.') from None
    if root.tag != 'raportEzla' or root.getroottree().docinfo.doctype:
        raise ValueError('Nieobsługiwany format raportu e-ZLA.')
    # Structural text (page numbers, ellipses) must never silently become data.
    for element in root.iter():
        if not isinstance(element.tag, str) or (len(element) and (element.text or '').strip()) or (element.tail or '').strip():
            raise ValueError('Raport zawiera dodatkowy tekst lub encje. Użyj oryginalnego pliku XML z ZUS.')
    header = _node(root, 'daneRaportu')
    payer = _node(header, 'platnik')
    nip = re.sub(r'[\s-]', '', _text(payer, 'nip'))
    if not re.fullmatch(r'[0-9]{10}', nip):
        raise ValueError('Nieprawidłowy NIP płatnika w raporcie e-ZLA.')
    generated = _date(_text(header, 'dataWygenerowania'))
    rows = root.findall('dokumentyEzla')
    declared = _text(header, 'liczbaDokumentowEzla')
    if not re.fullmatch(r'[0-9]{1,6}', declared) or int(declared) != len(rows) or len(rows) > MAX_DOCUMENTS:
        raise ValueError('Liczba dokumentów e-ZLA nie zgadza się z zawartością raportu lub przekracza limit.')
    correction_rows = root.findall('dokumentyEzlaK')
    declared_k = _text(header, 'liczbaDokumentowEzlaK', False)
    correction_count = int(declared_k) if re.fullmatch(r'[0-9]{1,6}', declared_k) else 0
    if (_node(header, 'liczbaDokumentowEzlaK', False) is not None and not re.fullmatch(r'[0-9]{1,6}', declared_k)):
        raise ValueError('Nieprawidłowa liczba dokumentów e-ZLA/K.')
    if correction_count > MAX_DOCUMENTS or len(rows) + len(correction_rows) > MAX_DOCUMENTS:
        raise ValueError('Liczba dokumentów e-ZLA/K przekracza limit.')
    if correction_rows and correction_count != len(correction_rows):
        raise ValueError('Liczba dokumentów e-ZLA/K nie zgadza się z zawartością raportu.')
    report_issues = []
    if correction_count and not correction_rows:
        report_issues.append(f'ZUS wykazał {correction_count} dokumentów e-ZLA/K bez szczegółów. '
                             'W raporcie cyklicznym PUE/eZUS włącz dołączanie ZLA/K w formie XML.')
    certificates, identities = [], {}
    for row in rows:
        person = _node(row, 'daneUbezpieczonego')
        identifier = _node(person, 'identyfikator')
        document = _node(row, 'daneDokumentu')
        period = _node(document, 'okresZwolnienia')
        kind, value = _text(identifier, 'typ'), _text(identifier, 'wartosc', False)
        issues = []
        if kind.upper() != 'PESEL':
            issues.append('Identyfikator inny niż PESEL — wymaga osobnego dopasowania')
        elif not re.fullmatch(r'[0-9]{11}', value):
            issues.append('Brak lub nieprawidłowy PESEL — nie dopasowuj po nazwisku')
        status = _text(document, 'status')
        status = STATUSES.get(re.sub(r'\s+', ' ', status.casefold().replace('–', '-').replace('—', '-')), status)
        if status not in STATUSES.values():
            issues.append('Nieznany status — wymaga wyjaśnienia')
        cancelled = _text(document, 'czyAnulowane', False).casefold()
        if cancelled not in ('', 'tak', 'nie', 'true', 'false', '1', '0'):
            issues.append('Nieznane oznaczenie anulowania')
        if cancelled in ('tak', 'true', '1') and status != 'Anulowane':
            issues.append('Oznaczenie anulowania sprzeczne ze statusem')
        start, end = _text(period, 'dataOd'), _text(period, 'dataDo')
        issued = _text(document, 'dataWystawienia')
        try:
            start, end, issued = _date(start), _date(end), _date(issued)
            if start > end:
                issues.append('Koniec zwolnienia wcześniejszy niż początek')
        except ValueError:
            issues.append('Nieprawidłowa data dokumentu lub zwolnienia')
        certificate = Certificate(
            employee_name=(_text(person, 'imie') + ' ' + _text(person, 'nazwisko')),
            employee_id=value, identifier_type=kind, series=_text(document, 'seria'),
            number=_text(document, 'numer'), status=status, issued_at=issued, start=start, end=end, issues=issues)
        identity = (kind.upper(), value, certificate.series, certificate.number)
        if identity in identities:
            # Do not guess whether two versions are duplicates, a correction or an annulment.
            certificate.issues.append('Powtórzony identyfikator dokumentu w raporcie')
            previous = identities[identity]
            if 'Powtórzony identyfikator dokumentu w raporcie' not in previous.issues:
                previous.issues.append('Powtórzony identyfikator dokumentu w raporcie')
        identities[identity] = certificate
        certificates.append(certificate)
    for row in correction_rows:
        person = _node(row, 'daneUbezpieczonego')
        identifier = _node(person, 'identyfikator')
        document = _node(row, 'daneDokumentu')
        kind, value = _text(identifier, 'typ'), _text(identifier, 'wartosc', False)
        issues = ['e-ZLA/K: skrócenie istniejącego zwolnienia — dopasuj dokument pierwotny; nie jest nową nieobecnością']
        if kind.upper() != 'PESEL' or not re.fullmatch(r'[0-9]{11}', value):
            issues.append('Brak poprawnego PESEL — wymaga osobnego dopasowania')
        end = _text(document, 'dataZwolnieniaDo')
        try:
            end = _date(end)
        except ValueError:
            issues.append('Nieprawidłowa data skrócenia zwolnienia')
        item = Certificate(employee_name=_text(person, 'imie') + ' ' + _text(person, 'nazwisko'),
            employee_id=value, identifier_type=kind, series=_text(document, 'seria'), number=_text(document, 'numer'),
            status='Skrócenie e-ZLA/K', issued_at='', start='', end=end, issues=issues)
        identity = ('K', kind.upper(), value, item.series, item.number)
        if identity in identities:
            item.issues.append('Powtórzony dokument e-ZLA/K w raporcie')
            identities[identity].issues.append('Powtórzony dokument e-ZLA/K w raporcie')
        identities[identity] = item
        certificates.append(item)
    return Report(nip=nip, payer_name=_text(payer, 'nazwaSkrocona', False), generated_at=generated,
                  certificates=certificates, issues=report_issues)


def read_report(path) -> Report:
    with Path(path).open('rb') as stream:
        return parse_report(stream.read(MAX_BYTES + 1))
