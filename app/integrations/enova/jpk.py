"""Import verified ENOVA JPK query results without inferring acceptance from sending."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from app.integrations.enova.document_counts import fetch_counts, normalize_nip

STATUSES = {'unknown': 'Nieustalony — sprawdź źródło', 'prepared': 'Przygotowany', 'processing': 'Przetwarzany', 'not_sent': 'Niewysłany', 'sent': 'Wysłany', 'accepted': 'Przyjęty (UPO)', 'rejected': 'Odrzucony', 'not_applicable': 'Nie dotyczy'}


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
        found[month] = {'status': status, 'upo': upo}
        if row.get('raw_status'):
            found[month]['raw_status'] = str(row['raw_status'])[:256]
    if set(found) != set(months):
        raise ValueError('Brak statusu JPK dla żądanego miesiąca nie oznacza niewysłanego JPK.')
    return found


def import_jpk(conn, config, source, company_id, months, day, *, apply, force=False):
    key = f'enova_jpk_checked_{company_id}'
    signature = {'day': day.isoformat(), 'server': config.get('server'), 'database': source['database'],
                 'query': source['jpk_query'], 'months': months}
    checked = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (key,)).fetchone()
    if apply and not force and checked and json.loads(checked[0]) == signature:
        return {'mode': 'skipped'}
    query_source = {**source, 'count_query': source['jpk_query']}
    statuses = validate_jpk(fetch_counts(config, query_source, months, result_kind='jpk'), source, months)
    if apply:
        for month, value in statuses.items():
            value.update(imported_at=datetime.now(ZoneInfo('Europe/Warsaw')).isoformat(), database=source['database'])
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                         'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                         (f'enova_jpk_{company_id}_{month}', json.dumps(value)))
        if not force:
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                         'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc', (key, json.dumps(signature)))
    return {'mode': 'applied' if apply else 'preview', 'statuses': statuses}
