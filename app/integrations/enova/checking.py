"""Independent checking snapshots; never update workflow stages or invoice counters."""
from datetime import date, datetime
from pathlib import Path
import json
from zoneinfo import ZoneInfo
from app.integrations.enova.document_counts import fetch_counts, normalize_nip, validate_rows, whole_count, requested_months
from app.integrations.enova.jpk import STATUSES
from app.utils.months import previous_month, month_bounds

DEPARTMENTS = ('accounting', 'payroll')


def history_months(month, count=12):
    month_bounds(month)
    if type(count) is not int or not 0 <= count <= 36:
        raise ValueError('Nieprawidłowy zakres historii sprawdzania.')
    result = []
    for _ in range(count):
        month = previous_month(month)
        result.append(month)
    return sorted(result)


def _identity(row, source, months):
    month = str(row['miesiac'])
    if normalize_nip(row['nip']) != normalize_nip(source['nip']) or month not in months:
        raise ValueError('Nieprawidłowy NIP lub okres w danych sprawdzania.')
    return month


def _timestamp(value):
    if value in (None, ''):
        return None
    if isinstance(value, date):
        return value.isoformat()
    if len(str(value)) == 10:
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            raise ValueError('Nieprawidłowa data wysyłki lub UPO.') from None
    try:
        return datetime.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValueError('Nieprawidłowa data wysyłki lub UPO.') from None


def validate_declarations(rows, source, months):
    result = {month: [] for month in months}
    seen = set()
    for row in rows:
        month = _identity(row, source, months)
        department = row['department']
        form = str(row['form'] or '').strip().upper()
        if department not in DEPARTMENTS or not form or len(form) > 64:
            raise ValueError('Nieprawidłowy dział lub rodzaj deklaracji.')
        identity = (month, department, form)
        if identity in seen:
            raise ValueError('Zapytanie deklaracji musi wybrać jeden aktualny stan formularza (uwzględniając korekty).')
        seen.add(identity)
        status, upo = row['status'], str(row['upo'] or '').strip()
        if status not in STATUSES or (status == 'accepted' and not upo):
            raise ValueError('Nieprawidłowy status deklaracji lub brak UPO dla przyjętej deklaracji.')
        result[month].append({'department': department, 'form': form, 'status': status,
                              'sent_at': _timestamp(row['sent_at']), 'upo': upo,
                              'upo_at': _timestamp(row['upo_at'])})
    return result


def validate_payroll(rows, source, months):
    result = {month: {} for month in months}
    seen_months, seen_types = set(), set()
    for row in rows:
        month = _identity(row, source, months)
        kind = str(row['rodzaj'] or '').strip()
        number = whole_count(row['liczba'])
        seen_months.add(month)
        if not kind:
            if number:
                raise ValueError('Brak rodzaju dokumentu kadrowego.')
            continue  # explicit zero-month marker; NULL kind with count zero
        if (month, kind) in seen_types:
            raise ValueError('Powtórzony rodzaj dokumentu kadrowego w miesiącu.')
        seen_types.add((month, kind))
        result[month][kind] = number
    if seen_months != set(months):
        raise ValueError('Brak miesiąca w danych kadrowych nie oznacza zera.')
    return result


def sync_checking(conn, config, source, company_id, day, *, apply):
    requested_months(day, config.get('months_back', 3))
    month = day.strftime('%Y-%m')
    # The configured month window plus the preceding full year for zero detection.
    months = history_months(month, 12 + config.get('months_back', 3) - 1) + [month]
    key = f'enova_checking_checked_{company_id}'
    signature = {'version': 1, 'day': day.isoformat(), 'server': config.get('server'),
                 'database': source['database'], 'months': months,
                 'payroll_query': source.get('payroll_query'), 'declarations_query': source.get('declarations_query')}
    checked = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (key,)).fetchone()
    if apply and checked and json.loads(checked[0]) == signature:
        return {'mode': 'skipped'}
    report_source = {**source, 'accounting_type': 'KH', 'count_query': str(Path(__file__).resolve().parents[3] / 'docs/enova/queries/checking.sql')}
    counts = validate_rows(fetch_counts(config, report_source, months), report_source, months)
    if any(count.by_type is None for count in counts):
        raise ValueError('Brak podziału na rodzaje dokumentów w odczycie sprawdzania.')
    payroll = None
    if source.get('payroll_query'):
        query = {**source, 'count_query': source['payroll_query']}
        payroll = validate_payroll(fetch_counts(config, query, months, result_kind='payroll'), source, months)
    declarations = None
    if source.get('declarations_query'):
        query = {**source, 'count_query': source['declarations_query']}
        declarations = validate_declarations(fetch_counts(config, query, months, result_kind='declarations'), source, months)
    if apply:
        stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
        for count in counts:
            value = {'documents': {'accounting': count.by_type,
                                   'payroll': payroll[count.month] if payroll is not None else None},
                     'declarations': declarations[count.month] if declarations is not None else None,
                     'imported_at': stamp, 'database': source['database']}
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                         'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                         (f'enova_checking_{company_id}_{count.month}', json.dumps(value, ensure_ascii=False)))
        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                     'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc', (key, json.dumps(signature)))
    return {'mode': 'applied' if apply else 'preview', 'months': len(months),
            'payroll_configured': payroll is not None, 'declarations_configured': declarations is not None}


def run_checking_import(config, *, apply=False, today=None):
    from app.db.dao_conn import get_conn
    from app.integrations.enova.document_counts import find_company
    if apply and not config.get('enabled', False):
        raise ValueError('Import Enova wyłączony.')
    day = today or datetime.now(ZoneInfo('Europe/Warsaw')).date()
    results = []
    with get_conn() as conn:
        if apply and not conn.execute('SELECT pg_try_advisory_xact_lock(748210)').fetchone()[0]:
            raise ValueError('Import sprawdzania jest już uruchomiony.')
        for source in config['sources']:
            try:
                with conn.transaction():
                    company = find_company(conn, normalize_nip(source['nip']))
                    result = sync_checking(conn, config, source, company['id'], day, apply=apply)
                    results.append({'database': source['database'], 'kind': 'checking', 'ok': True, **result})
            except Exception as exc:
                error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                results.append({'database': source['database'], 'kind': 'checking', 'ok': False, 'error': error})
                if apply:
                    conn.execute("INSERT INTO import_history(import_type,source_path,status,details) "
                                 "VALUES ('enova_checking',%s,'failed',%s)",
                                 (source['database'], json.dumps({'error': error})))
    return results
