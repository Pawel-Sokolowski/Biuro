"""Map a visible Płatnik register to scoped, exact evidence about a set."""
from datetime import date, datetime
import re
from app.utils.nip import normalize_nip

FORMS = {'ZUS DRA', 'ZUS RCA'}
STATUSES = {'sent': 'Zestaw wysłany', 'accepted': 'Zestaw potwierdzony',
            'rejected': 'Zestaw odrzucony'}
ERRORS = {
    'DesktopUnavailable': 'Pulpit konta agenta jest niedostępny.',
    'AppUnavailable': 'Płatnik nie jest dostępny na pulpicie agenta.',
    'ScreenUnavailable': 'Nie odnaleziono skonfigurowanego widoku Płatnika.',
    'ProfileMismatch': 'Układ lub wartości w rejestrze wymagają sprawdzenia profilu agenta.',
    'DuplicateEvidence': 'Kilka wpisów pasuje do tej samej deklaracji.',
    'ScanLimit': 'Rejestr jest za duży. Zawęź widok w Płatniku.',
    'Timeout': 'Upłynął limit czasu odczytu Płatnika.',
    'ReadFailed': 'Odczyt Płatnika nie powiódł się.',
    'LeaseExpired': 'Agent przerwał zadanie przed zapisaniem wyniku.',
}
MAX_ROWS = 10000
REQUIRED_COLUMNS = {'nip', 'period', 'form', 'report', 'status', 'set_id', 'receipt'}
OPTIONAL_COLUMNS = {'sent_at'}
REFERENCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./ -]{0,127}')


class ReadError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def label(raw):
    return ' '.join(str(raw or '').casefold().split())


def month(raw):
    value = str(raw or '').strip()
    if re.fullmatch(r'\d{2}/\d{4}', value, re.ASCII):
        value = value[3:] + '-' + value[:2]
    if re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value, re.ASCII):
        date.fromisoformat(value + '-01')
        return value
    raise ValueError('Nieprawidłowy miesiąc w rejestrze.')


def report_number(raw):
    value = str(raw or '').strip()
    if re.fullmatch(r'[0-9]{1,2}', value) and int(value):
        return f'{int(value):02d}'
    raise ValueError('Nieprawidłowy identyfikator deklaracji.')


def sent_timestamp(raw):
    value = str(raw or '').strip()
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M'):
        try:
            return datetime.strptime(value, fmt).isoformat(timespec='seconds')
        except ValueError:
            pass
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', value, re.ASCII):
        return datetime.fromisoformat(value).isoformat(timespec='seconds')
    raise ValueError('Nieprawidłowa data wysyłki.')


def clean_finding(item):
    form = item['form']
    status = item['status']
    receipt = item.get('receipt') or ''
    sent_at = sent_timestamp(item.get('sent_at'))
    set_id = item['set_id']
    if (form not in FORMS or status not in STATUSES or not REFERENCE.fullmatch(set_id)
            or (receipt and not REFERENCE.fullmatch(receipt))
            or (status == 'accepted' and not receipt) or (status == 'sent' and not sent_at)):
        raise ValueError('Nieprawidłowe potwierdzenie zestawu.')
    return {'form': form, 'report': report_number(item['report']), 'status': status,
            'set_id': set_id, 'receipt': receipt, 'sent_at': sent_at}


def collect(rows, nip, period, status_labels):
    selected = {}
    for index, row in enumerate(rows, start=1):
        if index > MAX_ROWS:
            raise ReadError('ScanLimit')
        try:
            row_nip = normalize_nip(row['nip'])
        except ValueError:
            continue
        if row_nip != nip:
            continue
        form = ' '.join(row['form'].upper().split())
        if form in ('DRA', 'RCA'):
            form = 'ZUS ' + form
        if form not in FORMS:
            continue
        try:
            if month(row['period']) != period:
                continue
            report = report_number(row['report'])
            status = status_labels.get(label(row['status']))
            # A row with an unmapped state still counts when looking for competing sets.
            finding = None if status is None else clean_finding(
                {'form': form, 'report': report, 'status': status, 'set_id': row['set_id'].strip(),
                 'receipt': row['receipt'].strip(), 'sent_at': row.get('sent_at')}
            )
        except ValueError:
            raise ReadError('ProfileMismatch') from None
        key = (form, report)
        if key in selected and (finding is None or selected[key] != finding):
            raise ReadError('DuplicateEvidence')
        selected[key] = finding
    return [finding for finding in selected.values() if finding is not None]


def display_check(check, report):
    """Describe a matched set separately from a declaration's own filing status."""
    state = check.get('state')
    if state == 'queued':
        return 'Oczekuje na agenta', ''
    if state == 'running':
        return 'Agent sprawdza', ''
    if state == 'failed':
        return 'Odczyt nieudany', ERRORS.get(check.get('error_code'), '')
    source = report.get('source')
    revisions = {report_number(value) for value in report.get('reports', [])}
    if state != 'completed':
        return '—', ''
    matches = [item for item in check.get('findings', [])
               if item['form'] == source and item['report'] in revisions]
    if not matches:
        return 'Brak zgodnego wpisu', ''
    labels = sorted({STATUSES[item['status']] for item in matches})
    evidence = []
    for item in matches:
        detail = f"{item['form']} {item['report']} · zestaw {item['set_id']}"
        if item['receipt']:
            detail += ' · potwierdzenie ' + item['receipt']
        if item['sent_at']:
            detail += ' · wysłano ' + item['sent_at'].replace('T', ' ')
        evidence.append(detail)
    return ', '.join(labels) if len(matches) == len(revisions) else 'Dane dla części korekt', '\n'.join(evidence)
