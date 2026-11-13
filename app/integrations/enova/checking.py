"""Independent checking snapshots; never update workflow stages or invoice counters."""
from datetime import date, datetime
from pathlib import Path
import json
from zoneinfo import ZoneInfo
from app.integrations.enova.document_counts import fetch_counts, normalize_nip, validate_rows, whole_count, requested_months
from app.integrations.enova.jpk import STATUSES
from app.utils.months import previous_month, month_bounds
from app.services.worker_activity import record

DEPARTMENTS = ('accounting', 'payroll')


class CheckingBusy(ValueError):
    pass


def checking_source(source, company):
    from app.companies.cennik_keys import company_accounting_type
    kind = company_accounting_type(company) or source.get('accounting_type')
    # KPiR entries live in Dziennik, not the general DokEwidencja pivot.
    query = 'kpir.sql' if kind=='KPIR' else 'checking.sql'
    return {**source,'accounting_type':'KPIR' if kind=='KPIR' else 'KH',
            'count_query':str(Path(__file__).resolve().parents[3] / 'docs/enova/queries' / query)}


def import_document_year(config, year, *, actor_id, progress=None):
    """Explicit counts-only backfill, independent of billing stages and schedule.

    Only the accounting section of checking snapshots is refreshed. In particular,
    this operation never writes monthly counters, provenance or daily job markers.
    """
    from app.db.dao_conn import get_conn
    from app.security.authorization import is_administrator
    from app.integrations.enova.document_counts import find_company
    if not is_administrator(actor_id):
        raise PermissionError('Tylko administrator może uruchamiać import Enovy.')
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError('Wybierz rok od 2000 do 2100.')
    sources = config.get('sources') or []
    if not sources:
        raise ValueError('Najpierw przypisz bazy Enovy do firm i zapisz połączenie.')
    months = [f'{year}-{month:02d}' for month in range(1, 13)]
    results = []
    with get_conn() as conn:
        if not conn.execute('SELECT pg_try_advisory_xact_lock(748210)').fetchone()[0]:
            raise CheckingBusy('Import sprawdzania jest już uruchomiony. Poczekaj na jego zakończenie.')
        for index, source in enumerate(sources):
            database = source['database']
            if progress:
                progress(index, len(sources), database)
            try:
                with conn.transaction():
                    company = find_company(conn, normalize_nip(source['nip']))
                    report_source = checking_source(source,company)
                    counts = validate_rows(fetch_counts(config, report_source, months), report_source, months)
                    if any(count.by_type is None for count in counts):
                        raise ValueError('Brak podziału na rodzaje dokumentów w odczycie sprawdzania.')
                    stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
                    for count in counts:
                        key = f'enova_checking_{company["id"]}_{count.month}'
                        old = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s FOR UPDATE', (key,)).fetchone()
                        snapshot = json.loads(old[0]) if old else {}
                        snapshot.setdefault('documents', {})['accounting'] = count.by_type
                        snapshot.update(database=database, accounting_imported_at=stamp)
                        snapshot.setdefault('imported_at', stamp)
                        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                                     'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                                     (key, json.dumps(snapshot, ensure_ascii=False)))
                    details = {'year': year, 'company_id': company['id'], 'months': months,
                               'counts': {count.month: count.documents for count in counts}}
                    conn.execute("INSERT INTO import_history(import_type,source_path,status,updated_count,completed_at,details) "
                                 "VALUES ('enova_document_year',%s,'completed',12,to_char(now(),'YYYY-MM-DD HH24:MI:SS'),%s)",
                                 (database, json.dumps(details)))
                    results.append({'database': database, 'company': company['nazwa'], 'ok': True, **details})
            except Exception as exc:
                from app.integrations.enova.odbc_support import describe_sql_error
                # Reuse redaction rather than leaking connection strings from an ODBC error.
                diagnostic = describe_sql_error(exc, secrets=(config.get('password_enc'),))
                error = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else diagnostic['hint']
                results.append({'database': database, 'ok': False, 'error': error})
                conn.execute("INSERT INTO import_history(import_type,source_path,status,details) "
                             "VALUES ('enova_document_year',%s,'failed',%s)",
                             (database, json.dumps({'year': year, 'error': error})))
            finally:
                if progress:
                    progress(index + 1, len(sources), database)
    return results


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
        employee_id = str(row.get('employee_id') or '').strip()
        employee_name = str(row.get('employee_name') or '').strip()
        if bool(employee_id) != bool(employee_name):
            raise ValueError('Deklaracja pracownika wymaga identyfikatora i nazwy pracownika.')
        source_system = str(row.get('source_system') or 'enova').strip().casefold().replace('ł', 'l')
        if source_system not in ('enova', 'platnik'):
            raise ValueError('Nieznane źródło deklaracji.')
        identity = (month, department, form, employee_id)
        if identity in seen:
            raise ValueError('Zapytanie deklaracji musi wybrać jeden aktualny stan formularza (uwzględniając korekty).')
        seen.add(identity)
        status, upo = row['status'], str(row['upo'] or '').strip()
        if status not in STATUSES or (status == 'accepted' and not upo):
            raise ValueError('Nieprawidłowy status deklaracji lub brak UPO dla przyjętej deklaracji.')
        result[month].append({'department': department, 'form': form, 'status': status,
                              'employee_id': employee_id, 'employee_name': employee_name, 'source_system': source_system,
                              'sent_at': _timestamp(row['sent_at']), 'upo': upo,
                              'upo_at': _timestamp(row['upo_at'])})
        if row.get('raw_status'):
            result[month][-1]['raw_status'] = str(row['raw_status'])[:256]
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


def sync_checking(conn, config, source, company_id, day, *, apply, months=None, force=False):
    requested_months(day, config.get('months_back', 3))
    month = day.strftime('%Y-%m')
    # Retain historical snapshots for period navigation; the UI compares only the previous month.
    months = months if months is not None else history_months(month, 12 + config.get('months_back', 3) - 1) + [month]
    if not months or months != sorted(set(months)) or len(months) > 36:
        raise ValueError('Nieprawidłowy zakres sprawdzania.')
    for period in months:
        month_bounds(period)
    key = f'enova_checking_checked_{company_id}'
    signature = {'version': 3, 'day': day.isoformat(), 'server': config.get('server'),
                 'database': source['database'], 'months': months,
                 'payroll_query': source.get('payroll_query'), 'declarations_query': source.get('declarations_query'), 'hr_events_query': source.get('hr_events_query')}
    checked = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (key,)).fetchone()
    if apply and not force and checked and json.loads(checked[0]) == signature:
        return {'mode': 'skipped'}
    from app.integrations.enova.document_counts import find_company
    company = find_company(conn,normalize_nip(source['nip']))
    report_source = checking_source(source,company)
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
    events = None
    if source.get('hr_events_query'):
        from app.integrations.enova.hr_events import fetch_events
        events = fetch_events(config, source, day)
    if force and source.get('jpk_query'):
        from app.integrations.enova.jpk import import_jpk
        import_jpk(conn, config, source, company_id, months, day, apply=apply, force=True)
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
        hr_snapshot = {'events': events, 'imported_at': stamp, 'as_of': day.isoformat(), 'database': source['database']}
        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                     'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                     (f'enova_hr_{company_id}', json.dumps(hr_snapshot, ensure_ascii=False)))
        if not force:
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                         'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc', (key, json.dumps(signature)))
    return {'mode': 'applied' if apply else 'preview', 'months': len(months),
            'payroll_configured': payroll is not None, 'declarations_configured': declarations is not None, 'hr_configured': events is not None}


def run_checking_import(config, *, apply=False, today=None, months=None, force=False):
    from app.db.dao_conn import get_conn
    from app.integrations.enova.document_counts import find_company
    if apply and not config.get('enabled', False):
        raise ValueError('Import Enova wyłączony.')
    day = today or datetime.now(ZoneInfo('Europe/Warsaw')).date()
    results = []
    with get_conn() as conn:
        if apply and not conn.execute('SELECT pg_try_advisory_xact_lock(748210)').fetchone()[0]:
            raise CheckingBusy('Import sprawdzania jest już uruchomiony.')
        for source in config['sources']:
            company = None
            try:
                with conn.transaction():
                    company = find_company(conn, normalize_nip(source['nip']))
                    if apply:
                        record('enova', 'reading', company_id=company['id'])
                    result = sync_checking(conn, config, source, company['id'], day, apply=apply, months=months, force=force)
                    results.append({'database': source['database'], 'company_id':company['id'], 'kind': 'checking', 'ok': True, **result})
                if apply:
                    record('enova', 'read', company_id=company['id'])
            except Exception as exc:
                if apply:
                    record('enova', 'failed', company_id=company['id'] if company else None)
                error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                results.append({'database': source['database'], 'kind': 'checking', 'ok': False, 'error': error})
                if apply:
                    conn.execute("INSERT INTO import_history(import_type,source_path,status,details) "
                                 "VALUES ('enova_checking',%s,'failed',%s)",
                                 (source['database'], json.dumps({'error': error})))
    return results
