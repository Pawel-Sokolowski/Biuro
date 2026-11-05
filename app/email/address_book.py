"""Contact normalization and Thunderbird CSV parsing, without database writes."""
import csv
import io
from email.headerregistry import Address
from email.utils import getaddresses
from pathlib import Path
import re
import unicodedata

CONTACT_FIELDS = (
    ('first_name', 'Imię'), ('last_name', 'Nazwisko'), ('display_name', 'Wyświetlana nazwa'),
    ('nickname', 'Pseudonim'), ('email', 'Podstawowy e-mail'), ('additional_email', 'Dodatkowy e-mail'),
    ('aim', 'Komunikator AIM'), ('phone_work', 'Telefonu służbowy'), ('phone_home', 'Telefon domowy'),
    ('fax', 'Numer faksu'), ('pager', 'Pager'), ('phone_mobile', 'Numer telefonu komórkowego'),
    ('home_address', 'Adres domowy'), ('home_address2', 'Adres domowy 2'), ('home_city', 'Miasto (dom)'),
    ('home_region', 'Województwo (dom)'), ('home_postal', 'Kod pocztowy (dom)'), ('home_country', 'Państwo (dom)'),
    ('work_address', 'Adres służbowy'), ('work_address2', 'Adres służbowy 2'), ('work_city', 'Miasto (praca)'),
    ('work_region', 'Województwo (praca)'), ('work_postal', 'Kod pocztowy (praca)'), ('work_country', 'Państwo (praca)'),
    ('job_title', 'Stanowisko'), ('department', 'Dział'), ('company', 'Firma/Organizacja'),
    ('website1', 'Strona WWW 1'), ('website2', 'Strona WWW 2'), ('birth_year', 'Urodziny – Rok'),
    ('birth_month', 'Urodziny – Miesiąc'), ('birth_day', 'Urodziny – Dzień'),
    ('custom1', 'Informacja 1'), ('custom2', 'Informacja 2'), ('custom3', 'Informacja 3'),
    ('custom4', 'Informacja 4'), ('notes', 'Notatki'),
)
CORE_FIELDS = ('email', 'first_name', 'last_name', 'display_name', 'nickname', 'additional_email', 'company')
ENGLISH_HEADERS = ('First Name', 'Last Name', 'Display Name', 'Nickname', 'Primary Email', 'Secondary Email',
                   'Screen Name', 'Work Phone', 'Home Phone', 'Fax Number', 'Pager Number', 'Mobile Number',
                   'Home Address', 'Home Address 2', 'Home City', 'Home State', 'Home ZipCode', 'Home Country',
                   'Work Address', 'Work Address 2', 'Work City', 'Work State', 'Work ZipCode', 'Work Country',
                   'Job Title', 'Department', 'Organization', 'Web Page 1', 'Web Page 2',
                   'Birth Year', 'Birth Month', 'Birth Day', 'Custom 1', 'Custom 2', 'Custom 3', 'Custom 4', 'Notes')


def normalize_header(value):
    text = unicodedata.normalize('NFKC', str(value or '')).lstrip('\ufeff')
    return re.sub(r'\s+', ' ', text.replace('–', '-').replace('—', '-')).strip().casefold()


def normalize_email(value):
    raw = str(value or '').strip()
    if not raw or '\n' in raw or '\r' in raw:
        raise ValueError('Podaj poprawny adres e-mail.')
    parsed = getaddresses([raw])
    if len(parsed) != 1 or not parsed[0][1]:
        raise ValueError('W polu e-mail podaj jeden adres.')
    address = parsed[0][1]
    try:
        mailbox = Address(addr_spec=address)
        if not mailbox.username or not mailbox.domain or len(address) > 254:
            raise ValueError()
        domain = mailbox.domain.encode('idna').decode('ascii').lower()
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Podaj poprawny adres e-mail.') from exc
    return f'{mailbox.username}@{domain}'


def normalize_contact(values):
    result = {field: str(values.get(field) or '').strip() for field, _label in CONTACT_FIELDS}
    if not result['email']:
        result['email'], result['additional_email'] = result['additional_email'], ''
    result['email'] = normalize_email(result['email'])
    if result['additional_email']:
        result['additional_email'] = normalize_email(result['additional_email'])
        if result['additional_email'].casefold() == result['email'].casefold():
            result['additional_email'] = ''
    for field in ('first_name', 'last_name', 'display_name', 'nickname', 'company'):
        result[field] = ' '.join(result[field].split())
    if isinstance(values.get('extra_fields'), dict):
        result['extra_fields'] = dict(values['extra_fields'])
    return result


def contact_name(contact):
    return (contact.get('display_name') or ' '.join(filter(None, (contact.get('first_name'), contact.get('last_name'))))
            or contact.get('nickname') or contact.get('company') or contact['email'])


def contact_mailbox(contact, email=None):
    address = email or contact['email']
    label = contact_name(contact)
    # formataddr's MIME encoded words are intended for transport, not an editor.
    quoted = label.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{quoted}" <{address}>' if label != address else address


def recipient_spans(text):
    """Delimiter positions outside quoted names and angle-bracket addresses."""
    spans, start, quoted, escaped, depth = [], 0, False, False, 0
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quoted and char == '\\':
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted:
            if char == '<':
                depth += 1
            elif char == '>':
                depth = max(0, depth - 1)
            elif char in ',;' and depth == 0:
                spans.append((start, index))
                start = index + 1
    return [*spans, (start, len(text))]


def split_recipients(text):
    normalized = ','.join(text[start:end] for start, end in recipient_spans(text or ''))
    return [address.strip() for _name, address in getaddresses([normalized]) if address.strip()]


def read_contacts_csv(path):
    raw = Path(path).read_bytes()
    encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'cp1250', 'cp1252']
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    else:
        raise ValueError('Nie udało się odczytać kodowania CSV. Zapisz plik jako UTF-8.')
    try:
        dialect = csv.Sniffer().sniff(text[:16384], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text, newline=''), dialect, strict=True)
    try:
        headers = next(reader)
    except StopIteration:
        raise ValueError('Plik CSV jest pusty.') from None
    aliases = {normalize_header(label): field for field, label in CONTACT_FIELDS}
    aliases.update({normalize_header(label): field for (field, _), label in zip(CONTACT_FIELDS, ENGLISH_HEADERS)})
    aliases.update({normalize_header('Telefon służbowy'): 'phone_work', normalize_header('Nazwa wyświetlana'): 'display_name'})
    fields = [aliases.get(normalize_header(header)) for header in headers]
    if 'email' not in fields and 'additional_email' not in fields:
        raise ValueError('CSV musi zawierać kolumnę „Podstawowy e-mail” lub „Dodatkowy e-mail”.')
    mapped = [field for field in fields if field is not None]
    if len(mapped) != len(set(mapped)):
        raise ValueError('CSV ma powtarzające się kolumny kontaktu. Sprawdź nagłówki.')
    contacts, issues, duplicates = {}, [], 0
    try:
        for values in reader:
            if not any(value.strip() for value in values):
                continue
            if len(values) != len(headers):
                issues.append(f'Wiersz {reader.line_num}: liczba kolumn nie odpowiada nagłówkom.')
                continue
            incoming = {field: value for field, value in zip(fields, values) if field is not None}
            extra = {header: value for header, field, value in zip(headers, fields, values) if field is None and value.strip()}
            if extra:
                incoming['extra_fields'] = extra
            try:
                contact = normalize_contact(incoming)
            except ValueError as exc:
                issues.append(f'Wiersz {reader.line_num}: {exc}')
                continue
            key = contact['email'].casefold()
            if key in contacts:
                duplicates += 1
                contacts[key] = merge_missing(contacts[key], contact)
            else:
                contacts[key] = contact
    except csv.Error as exc:
        raise ValueError(f'Niepoprawny CSV przy wierszu {reader.line_num}: {exc}') from exc
    return {'contacts': list(contacts.values()), 'issues': issues, 'duplicates': duplicates, 'encoding': encoding}


def merge_missing(existing, incoming):
    result = dict(existing)
    for field, value in incoming.items():
        if field == 'extra_fields':
            result[field] = {**value, **existing.get(field, {})}
        elif value and not existing.get(field):
            result[field] = value
    return result
