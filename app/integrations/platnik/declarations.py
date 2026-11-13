"""Standard Płatnik forms, without guessing transmission status codes."""
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn, get_coordination_conn
from app.integrations.enova.document_counts import normalize_nip
from app.integrations.platnik.holidays import load, connect, sql_operation
from app.utils.crypto import encrypt_password, decrypt_password
from app.utils.months import month_bounds, previous_month
from app.integrations.platnik.form_catalog import MONTHLY, ANNUAL, requirements, records_sql

REQUIRED = requirements({**MONTHLY, **ANNUAL})
FIELDS = {'form', 'source_id', 'payer_id', 'nip', 'report_id', 'miesiac',
          'employee_id', 'employee_name', 'raw_status', 'valid_link'}


def source_query(cursor, config, *, annual=False):
    override = str(config.get('annual_declarations_query' if annual else 'declarations_query') or '').strip()
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError('Zamiennik SQL Płatnika wymaga pełnej ścieżki na serwerze workera.')
        return path.read_text(encoding='utf-8-sig')
    specs = ANNUAL if annual else MONTHLY
    required = requirements(specs)
    table_list = ','.join("'" + name + "'" for name in required)
    cursor.execute("SELECT t.name,c.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id "
                   "JOIN sys.columns c ON c.object_id=t.object_id WHERE s.name='dbo' "
                   "AND t.name IN (" + table_list + ")")
    metadata = cursor.fetchmany(10001)
    if len(metadata) > 10000:
        raise ValueError('Za dużo metadanych adaptera Płatnika.')
    columns = {}
    for table, column in metadata:
        columns.setdefault(table.upper(), set()).add(column.upper())
    missing = [table + '.' + name for table, names in required.items()
               for name in sorted(names - columns.get(table, set()))]
    document_key = next((name for name in ('ID_DOKUMENT', 'ID') if name in columns.get('DOKUMENT', set())), None)
    if document_key is None:
        missing.append('DOKUMENT: ID_DOKUMENT lub ID')
    if missing:
        raise ValueError('Niekompletny standardowy schemat lub brak widoczności metadanych Płatnika: ' + ', '.join(missing))
    path = Path(__file__).resolve().parents[3] / 'docs/platnik/queries/declarations.sql'
    query = path.read_text(encoding='utf-8-sig')
    records = records_sql(specs, document_key, 'STATUSPT' in columns['DOKUMENT'])
    period = "CONVERT(char(4),r.reporting_date,112) AS rok" if annual else "CONVERT(char(7),r.reporting_date,120) AS miesiac"
    return query.replace('__RECORDS__', records).replace('__PERIOD_RESULT__', period)


def fetch(config, months, *, annual=False):
    connection = connect(config)
    with sql_operation(config, 'Odczyt dokumentów formularzy ZUS'):
        try:
            cursor = connection.cursor()
            query = source_query(cursor, config, annual=annual)
            if not query.strip():
                raise ValueError('Plik zapytania formularzy ZUS jest pusty.')
            start, end = ((f'{months[0]}-01-01', f'{int(months[-1])+1}-01-01') if annual else
                          (month_bounds(months[0])[0], month_bounds(months[-1])[1]))
            cursor.execute(query, start, end)
            columns = [column[0].lower() for column in cursor.description or []]
            expected = FIELDS - {'miesiac'} | {'rok'} if annual else FIELDS
            if set(columns) != expected or len(columns) != len(expected):
                raise ValueError('Nieprawidłowy kontrakt kolumn formularzy ZUS.')
            rows = []
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                rows.extend(dict(zip(columns, row)) for row in batch)
                if len(rows) > 100000:
                    raise ValueError('Odczyt formularzy ZUS przekracza 100000 rekordów. Poprzednie dane pozostawiono.')
            return rows
        finally:
            connection.close()


def normalize(rows, months, selected_nips, *, annual=False):
    result = {nip: {month: [] for month in months} for nip in selected_nips}
    seen, payers = set(), {}
    for row in rows:
        nip = normalize_nip(row['nip'])
        if nip not in result:
            continue
        month = str(row['rok' if annual else 'miesiac'] or '')
        forms = ANNUAL if annual else MONTHLY
        if month not in months or row['valid_link'] != 1 or row['form'] not in {'ZUS ' + code for code in forms}:
            raise ValueError('Nieprawidłowy okres lub powiązanie dokumentu formularzy ZUS. Poprzednie dane pozostawiono.')
        source_id, payer_id = str(row['source_id'] or ''), str(row['payer_id'] or '')
        if not source_id or not payer_id:
            raise ValueError('Brak identyfikatora dokumentu lub płatnika formularzy ZUS.')
        if nip in payers and payers[nip] != payer_id:
            raise ValueError('Kilka kartotek płatnika ma ten sam NIP. Nie połączono dokumentów.')
        payers[nip] = payer_id
        identity = (payer_id, row['form'], source_id)
        if identity in seen:
            raise ValueError('Powtórzony dokument formularzy ZUS. Poprzednie dane pozostawiono.')
        seen.add(identity)
        employee_id = str(row['employee_id'] or '')
        employee_name = str(row['employee_name'] or '').strip()
        if bool(employee_id) != bool(employee_name):
            raise ValueError('Formularz osoby wymaga identyfikatora i nazwy ubezpieczonego.')
        code = str(row['raw_status']).strip() if row['raw_status'] is not None else ''
        entry = {'department': 'payroll', 'form': row['form'], 'source_system': 'platnik',
                 'employee_id': employee_id, 'employee_name': employee_name,
                 'source_id': source_id, 'report_id': str(row['report_id'] or '').strip(),
                 'raw_status': code, 'status': 'unknown',
                 'status_text': 'Status źródłowy: ' + code if code else 'Odczytano dokument; status wysyłki nieustalony',
                 'sent_at': None, 'upo': '', 'upo_at': None}
        if annual:
            entry['reporting_year'] = month
        result[nip][month].append(entry)
    return result


def _import_periods(months, *, company_ids=None, actor_id=None, annual=False):
    if actor_id is not None:
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Tylko administrator uruchamia bezpośredni odczyt Płatnika.')
    if not months or months != sorted(set(months)) or len(months) > 24:
        raise ValueError('Nieprawidłowy zakres miesięcy formularzy ZUS.')
    for month in months:
        if annual:
            if not str(month).isdigit() or len(str(month)) != 4 or not 2000 <= int(month) <= 2100:
                raise ValueError('Nieprawidłowy rok raportów ZUS.')
        else:
            month_bounds(month)
    config = load()
    if not config.get('declarations_enabled', True) or (not config['enabled'] and company_ids is None):
        return {'mode': 'disabled'}
    with get_coordination_conn() as lock:
        if not lock.execute('SELECT pg_try_advisory_xact_lock(748220)').fetchone()[0]:
            return {'mode': 'busy'}
        with get_conn() as conn:
            companies = conn.execute('SELECT id,nip FROM firmy').fetchall()
        selected = [row for row in companies if company_ids is None or row['id'] in company_ids]
        if company_ids is not None and {row['id'] for row in selected} != set(company_ids):
            raise ValueError('Nieznana firma w odczycie formularzy ZUS.')
        mapped = {}
        for row in selected:
            try:
                nip = normalize_nip(row['nip'])
            except ValueError:
                if company_ids is not None:
                    raise
                continue
            if nip in mapped:
                raise ValueError('Kilka firm Biuro ma ten sam NIP. Nie przypisano dokumentów formularzy ZUS.')
            mapped[nip] = row['id']
        for row in companies:
            if row['id'] in mapped.values():
                continue
            try:
                nip = normalize_nip(row['nip'])
            except ValueError:
                continue
            if nip in mapped:
                raise ValueError('NIP wybranej firmy jest przypisany także innej firmie Biuro.')
        if not mapped:
            return {'mode': 'empty', 'companies': 0}
        from app.services.worker_activity import batch
        with batch('platnik', mapped.values()):
            rows = fetch(config, months, annual=True) if annual else fetch(config, months)
            records = normalize(rows, months, mapped, annual=annual)
            stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
            with get_conn() as conn:
                for nip, fid in mapped.items():
                    for month, entries in records[nip].items():
                        snapshot = json.dumps({'imported_at': stamp, 'declarations': entries}, ensure_ascii=False)
                        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                                     'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                                     (f"platnik_declarations_{fid}_{'year_' if annual else ''}{month}", encrypt_password(snapshot)))
        return {'mode': 'applied', 'companies': len(mapped), 'months': months,
                'documents': sum(len(entries) for by_month in records.values() for entries in by_month.values())}


def decoded_snapshot(value):
    return json.loads(decrypt_password(value)) if value else {}


def import_months(months, *, company_ids=None, actor_id=None):
    return _import_periods(months, company_ids=company_ids, actor_id=actor_id)


def import_year(year, *, company_ids=None, actor_id=None):
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError('Nieprawidłowy rok raportów ZUS.')
    return _import_periods([str(year)], company_ids=company_ids, actor_id=actor_id, annual=True)


def read_year(actor_id, year):
    from app.integrations.enova.accounting import require_access
    from app.security.authorization import assigned_company_ids
    from app.db.dao_year_end import company_declarations
    from app.accounting.report_types import form_key
    require_access(actor_id)
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError('Nieprawidłowy rok raportów ZUS.')
    ids = list(assigned_company_ids(actor_id))
    with get_conn() as conn:
        rows = conn.execute('''SELECT f.*, y.company_json, a.wartosc AS platnik_annual
            FROM firmy f LEFT JOIN year_end_snapshots y ON y.firma_id=f.id AND y.rok=%s
            LEFT JOIN app_settings a ON a.klucz='platnik_declarations_' || f.id::text || '_year_' || %s
            WHERE f.id=ANY(%s) ORDER BY lower(f.nazwa)''', (year, str(year), ids)).fetchall()
    result = []
    for row in rows:
        company = dict(row)
        yearly = json.loads(company['company_json']) if company.get('company_json') else company
        codes = yearly.get('declaration_codes', company_declarations(yearly))
        applicable = [code for code in ANNUAL if code in {form_key(value) for value in codes}]
        if not applicable:
            continue
        snapshot = decoded_snapshot(company.get('platnik_annual'))
        entries = [entry for entry in snapshot.get('declarations', []) if form_key(entry['form']) in applicable]
        result.append({'id': company['id'], 'nazwa': company['nazwa'], 'nip': company['nip'],
                       'forms': applicable, 'declarations': entries, 'imported_at': snapshot.get('imported_at')})
    return {'year': year, 'companies': result}


def worker_job():
    month = datetime.now(ZoneInfo('Europe/Warsaw')).strftime('%Y-%m')
    results, failures = {}, []
    jobs = [('monthly', lambda: import_months([previous_month(month), month]))]
    for year in (int(month[:4])-1, int(month[:4])):
        jobs.append((str(year), lambda year=year: import_year(year)))
    for name, job in jobs:
        try:
            results[name] = job()
        except Exception as exc:
            failures.append(name + ': ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
    if failures:
        raise ValueError('Nieukończone raporty Płatnika: ' + '; '.join(failures))
    return results
