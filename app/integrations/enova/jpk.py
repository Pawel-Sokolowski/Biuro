"""Import verified ENOVA JPK query results without inferring acceptance from sending."""
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from app.integrations.enova.document_counts import fetch_counts, normalize_nip

STATUSES = {'failed': 'Błąd przetwarzania', 'cancelled': 'Anulowany', 'unknown': 'Nieustalony — sprawdź źródło', 'prepared': 'Przygotowany', 'processing': 'Przetwarzany', 'not_sent': 'Niewysłany', 'sent': 'Wysłany', 'accepted': 'Przyjęty (UPO)', 'rejected': 'Odrzucony', 'not_applicable': 'Nie dotyczy'}


def filing_timestamp(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    # ODBC may leave a space before the offset and return seven fractional digits.
    text = re.sub(r'\s+(?=[+-]\d{2}:\d{2}$)', '', text)
    try:
        return (date.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(text)).isoformat()
    except ValueError:
        raise ValueError('Nieprawidłowa data wysyłki lub UPO.') from None


def validate_jpk(rows, source, months):
    found = {}
    for row in rows:
        month = str(row['miesiac'])
        if normalize_nip(row['nip']) != normalize_nip(source['nip']) or month not in months or month in found:
            raise ValueError('Nieprawidłowa firma lub miesiąc statusu JPK.')
        status = row['status']
        upo = str(row['upo'] or '').strip()
        if status not in STATUSES or (status == 'accepted' and not upo):
            raise ValueError('Nieprawidłowy status JPK lub brak potwierdzenia UPO.')
        found[month] = {'status': status, 'upo': upo,
                        'sent_at': filing_timestamp(row.get('sent_at')),
                        'upo_at': filing_timestamp(row.get('upo_at'))}
        if row.get('raw_status'):
            found[month]['raw_status'] = str(row['raw_status'])[:256]
        if row.get('form_variant'):
            found[month]['form_variant'] = str(row['form_variant'])[:64]
    if set(found) != set(months):
        raise ValueError('Brak statusu JPK dla żądanego miesiąca nie oznacza niewysłanego JPK.')
    return found


def import_jpk(conn, config, source, company_id, months, day, *, apply, force=False):
    key = f'enova_jpk_checked_{company_id}'
    from app.integrations.enova.query_defaults import ADAPTER_REVISION
    signature = {'adapter_revision': ADAPTER_REVISION, 'day': day.isoformat(), 'server': config.get('server'), 'database': source['database'],
                 'query': source['jpk_query'], 'months': months}
    checked = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (key,)).fetchone()
    if apply and not force and checked and json.loads(checked[0]) == signature:
        return {'mode': 'skipped'}
    query_source = {**source, 'count_query': source['jpk_query']}
    statuses = validate_jpk(fetch_counts(config, query_source, months, result_kind='jpk'), source, months)
    if apply:
        stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
        for month, value in statuses.items():
            value.update(imported_at=stamp, database=source['database'])
            save_jpk(conn, company_id, month, value)
        if not force:
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                         'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc', (key, json.dumps(signature)))
    return {'mode': 'applied' if apply else 'preview', 'statuses': statuses}


def save_jpk(conn, company_id, month, value):
    from app.companies.checking_obligations import add_imported
    if value.get('form_variant') or value['status'] not in ('unknown', 'not_applicable'):
        add_imported(conn, company_id, month, [{'form': 'JPK_VAT', **value}], 'enova-jpk')
    conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                 'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                 (f'enova_jpk_{company_id}_{month}', json.dumps(value)))
