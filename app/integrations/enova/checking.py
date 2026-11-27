"""Independent checking snapshots; never update workflow stages or invoice counters."""
from app.remote.boundary import endpoint as office_endpoint
from datetime import date, datetime, timedelta
from pathlib import Path
import json
from zoneinfo import ZoneInfo
from app.integrations.enova.document_counts import fetch_counts, normalize_nip, resolve_source, validate_rows, whole_count, requested_months
from app.integrations.enova.counting_rules import load_rules, effective_divisor
from app.integrations.enova.jpk import STATUSES, filing_timestamp
from app.utils.months import previous_month, month_bounds
from app.services.worker_activity import record
from app.db.import_audit import log_import
from app.db.checking_storage import read_snapshot, save_snapshot
from app.integrations.enova.query_defaults import ADAPTER_REVISION

DEPARTMENTS = ('accounting', 'payroll')


class CheckingBusy(ValueError):
    pass


def checking_source(source, company):
    from app.companies.cennik_keys import company_accounting_type
    kind = company_accounting_type(company) or source.get('accounting_type')
    if not kind:
        return {**source, 'accounting_type': 'KH',
                'count_query': str(Path(__file__).resolve().parents[3] / 'docs/enova/queries/checking.sql')}
    return resolve_source({**source, 'count_query': None}, company)


def _store_accounting_breakdown(snapshot, count):
    snapshot.setdefault('documents', {})['accounting'] = count.report_breakdown
    if count.report_types is not None:
        snapshot['accounting_details'] = {'by_type': count.by_type, 'bank_transactions': count.bank_transactions,
                                          'cash_transactions': count.cash_transactions}
    else:
        snapshot.pop('accounting_details', None)


@office_endpoint('app.integrations.enova.checking.import_document_year')
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
    rules = load_rules()
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
                    counts = validate_rows(_counts(config, report_source, months, 'counts', force=True), report_source,
                                           months, effective_divisor(rules, company['id']))
                    if any(count.report_breakdown is None for count in counts):
                        raise ValueError('Brak podziału na rodzaje dokumentów w odczycie sprawdzania.')
                    stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
                    for count in counts:
                        snapshot = read_snapshot(conn, 'enova_checking', company['id'], count.month, lock=True) or {}
                        _store_accounting_breakdown(snapshot, count)
                        snapshot.update(database=database, accounting_imported_at=stamp)
                        snapshot.setdefault('imported_at', stamp)
                        save_snapshot(conn, 'enova_checking', company['id'], count.month, snapshot)
                    details = {'year': year, 'company_id': company['id'], 'months': months,
                               'counts': {count.month: count.total for count in counts}}
                    log_import(conn, 'enova_document_year', source=database, updated=len(months),
                               details=details)
                    results.append({'database': database, 'company': company['nazwa'], 'ok': True, **details})
            except Exception as exc:
                from app.integrations.enova.odbc_support import describe_sql_error
                # Reuse redaction rather than leaking connection strings from an ODBC error.
                diagnostic = describe_sql_error(exc, secrets=(config.get('password_enc'),))
                error = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else diagnostic['hint']
                results.append({'database': database, 'ok': False, 'error': error})
                log_import(conn, 'enova_document_year', source=database, status='failed',
                           details={'year': year, 'error': error})
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
        entry = {'department': department, 'form': form, 'status': status,
                 'employee_id': employee_id, 'employee_name': employee_name, 'source_system': source_system,
                 'sent_at': filing_timestamp(row.get('sent_at')), 'upo': upo,
                 'upo_at': filing_timestamp(row.get('upo_at'))}
        # Data utworzenia deklaracji w Enovie (opcjonalna kolumna odczytu).
        if row.get('created_at'):
            entry['created_at'] = filing_timestamp(row.get('created_at'))
        result[month].append(entry)
        if row.get('raw_status'):
            result[month][-1]['raw_status'] = str(row['raw_status'])[:256]
        if row.get('form_variant'):
            result[month][-1]['form_variant'] = str(row['form_variant'])[:64]
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


def _now():
    return datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()


CACHE_TTL_MINUTES = 10


def _cache_key(source, result_kind, months):
    if result_kind == 'counts':
        result_kind = f"counts_{source['accounting_type']}_{source.get('vat_czynny')}"
    return f'enova_fetch_cache_{source["database"]}_{result_kind}_{months[0]}_{months[-1]}'


def _cached(source, result_kind, months):
    """Ten sam odczyt w krótkim odstępie czasu nie uderza drugi raz w Enovę."""
    from app.db.dao_conn import get_conn as _conn
    with _conn() as cache_conn:
        row = cache_conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',
                                 (_cache_key(source, result_kind, months),)).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0])
    except ValueError:
        return None
    if value.get('adapter') != ADAPTER_REVISION:
        return None
    created = datetime.fromisoformat(value['at'])
    if datetime.now(ZoneInfo('Europe/Warsaw')) - created > timedelta(minutes=CACHE_TTL_MINUTES):
        return None
    return value.get('rows')


def _remember(source, result_kind, months, rows):
    from app.db.dao_conn import get_conn as _conn
    with _conn() as cache_conn:
        cache_conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                           'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                           (_cache_key(source, result_kind, months),
                            json.dumps({'adapter': ADAPTER_REVISION, 'at': _now(), 'rows': rows},
                                       ensure_ascii=False)))


def _counts(config, source, months, result_kind, *, force=False):
    if force or not config.get('fetch_cache', True):
        return fetch_counts(config, source, months, result_kind=result_kind)
    cached = _cached(source, result_kind, months)
    if cached is not None:
        return cached
    rows = fetch_counts(config, source, months, result_kind=result_kind)
    _remember(source, result_kind, months, rows)
    return rows


def sync_checking(conn, config, source, company, day, *, divisor, apply, months=None, force=False):
    requested_months(day, config.get('months_back', 3))
    month = day.strftime('%Y-%m')
    # Retain historical snapshots for period navigation; the UI compares only the previous month.
    months = months if months is not None else history_months(month, 12 + config.get('months_back', 3) - 1) + [month]
    if not months or months != sorted(set(months)) or len(months) > 36:
        raise ValueError('Nieprawidłowy zakres sprawdzania.')
    for period in months:
        month_bounds(period)
    company_id = company['id']
    report_source = checking_source(source, company)
    key = f'enova_checking_checked_{company_id}'
    signature = {'version': 3, 'adapter_revision': ADAPTER_REVISION, 'day': day.isoformat(), 'server': config.get('server'),
                 'database': source['database'], 'months': months,
                 'accounting_type': report_source['accounting_type'], 'vat_czynny': report_source.get('vat_czynny'),
                 'divisor': divisor,
                 'payroll_query': source.get('payroll_query'), 'declarations_query': source.get('declarations_query'), 'hr_events_query': source.get('hr_events_query')}
    if source.get('hr_events_query'):
        signature['hr_adapter_revision'] = 1
    checked = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (key,)).fetchone()
    if apply and not force and checked and json.loads(checked[0]) == signature:
        return {'mode': 'skipped'}
    counts = validate_rows(_counts(config, report_source, months, 'counts', force=force), report_source, months, divisor)
    if any(count.report_breakdown is None for count in counts):
        raise ValueError('Brak podziału na rodzaje dokumentów w odczycie sprawdzania.')
    payroll = None
    if source.get('payroll_query'):
        query = {**source, 'count_query': source['payroll_query']}
        payroll = validate_payroll(_counts(config, query, months, 'payroll', force=force), source, months)
    declarations = None
    if source.get('declarations_query'):
        query = {**source, 'count_query': source['declarations_query']}
        declarations = validate_declarations(_counts(config, query, months, 'declarations', force=force), source, months)
    events, hr_error = None, False
    if source.get('hr_events_query'):
        from app.integrations.enova.hr_events import fetch_events
        try:
            events = fetch_events(config, source, day)
        except Exception as exc:
            hr_error = True
            if apply:
                log_import(conn, 'enova_hr', source=source['database'], status='failed',
                           details={'company_id': company_id, 'error_type': type(exc).__name__})
    if force and source.get('jpk_query'):
        from app.integrations.enova.jpk import import_jpk
        import_jpk(conn, config, source, company_id, months, day, apply=apply, force=True)
    if apply:
        stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
        for count in counts:
            value = {'documents': {'payroll': payroll[count.month] if payroll is not None else None},
                     'declarations': declarations[count.month] if declarations is not None else None,
                     'imported_at': stamp, 'database': source['database']}
            _store_accounting_breakdown(value, count)
            save_snapshot(conn, 'enova_checking', company_id, count.month, value)
        if hr_error:
            previous = read_snapshot(conn, 'enova_hr', company_id) or {}
            save_snapshot(conn, 'enova_hr', company_id, '', {**previous, 'last_attempt_failed': True})
        else:
            hr_snapshot = {'events': events, 'imported_at': stamp, 'as_of': day.isoformat(), 'database': source['database']}
            save_snapshot(conn, 'enova_hr', company_id, '', hr_snapshot)
        if not force and not hr_error:
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                         'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc', (key, json.dumps(signature)))
    return {'mode': 'applied' if apply else 'preview', 'months': len(months),
            'payroll_configured': payroll is not None, 'declarations_configured': declarations is not None,
            'hr_configured': bool(source.get('hr_events_query')), 'hr_failed': hr_error}


@office_endpoint('app.integrations.enova.checking.run_checking_import')
def run_checking_import(config, *, apply=False, today=None, months=None, force=False, stop=None):
    """stop: optional Event; a set event abandons the remaining companies without writing."""
    from app.db.dao_conn import get_conn
    from app.integrations.enova.document_counts import find_company
    if apply and not config.get('enabled', False):
        raise ValueError('Import Enova wyłączony.')
    day = today or datetime.now(ZoneInfo('Europe/Warsaw')).date()
    results = []
    rules = load_rules()
    with get_conn() as conn:
        if apply and not conn.execute('SELECT pg_try_advisory_xact_lock(748210)').fetchone()[0]:
            raise CheckingBusy('Import sprawdzania jest już uruchomiony.')
        for source in config['sources']:
            if stop is not None and stop.is_set():
                break
            company = None
            try:
                with conn.transaction():
                    company = find_company(conn, normalize_nip(source['nip']))
                    if apply:
                        record('enova', 'reading', company_id=company['id'])
                    result = sync_checking(conn, config, source, company, day, apply=apply, months=months, force=force,
                                           divisor=effective_divisor(rules, company['id']))
                    results.append({'database': source['database'], 'company_id':company['id'], 'kind': 'checking', 'ok': True, **result})
                if apply:
                    record('enova', 'read', company_id=company['id'])
            except Exception as exc:
                if apply:
                    record('enova', 'failed', company_id=company['id'] if company else None)
                error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                results.append({'database': source['database'], 'kind': 'checking', 'ok': False, 'error': error})
                if apply:
                    log_import(conn, 'enova_checking', source=source['database'], status='failed',
                               details={'company_id': company['id'] if company else None, 'error': error})
    return results
