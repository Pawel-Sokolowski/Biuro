"""Standard Płatnik forms, without guessing transmission status codes."""
from app.remote.boundary import endpoint as office_endpoint
from datetime import datetime
from itertools import batched
import json
import logging
from app.db.checking_storage import save_snapshots
from pathlib import Path
from zoneinfo import ZoneInfo
from psycopg import Error as DatabaseError

from app.db.dao_conn import get_conn, get_coordination_conn
from app.integrations.enova.document_counts import normalize_nip
from app.integrations.platnik.holidays import load, connect, sql_operation
from app.utils.crypto import encrypt_password, decrypt_password
from app.utils.months import month_bounds, previous_month
from app.integrations.platnik.form_catalog import ADDITIONAL_MONTHLY, ANNUAL, MONTHLY, records_sql, requirements, resolve_fields

REQUIRED = requirements({**MONTHLY, **ANNUAL})
FIELDS = {'form', 'source_id', 'payer_id', 'nip', 'report_id', 'miesiac',
          'employee_id', 'employee_name', 'raw_status', 'valid_link'}


def source_query(cursor, config, *, annual=False, scoped=False):
    override = str(config.get('annual_declarations_query' if annual else 'declarations_query') or '').strip()
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError('Zamiennik SQL Płatnika wymaga pełnej ścieżki na serwerze workera.')
        return path.read_text(encoding='utf-8-sig')
    specs = ANNUAL if annual else MONTHLY | ADDITIONAL_MONTHLY
    table_list = ','.join("'" + name + "'" for name in (*requirements(specs), 'PLATN_IDENT'))
    cursor.execute("SELECT t.name,c.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id "
                   "JOIN sys.columns c ON c.object_id=t.object_id WHERE s.name='dbo' "
                   "AND t.name IN (" + table_list + ")")
    metadata = cursor.fetchmany(10001)
    if len(metadata) > 10000:
        raise ValueError('Za dużo metadanych adaptera Płatnika.')
    columns = {}
    for table, column in metadata:
        columns.setdefault(table.upper(), set()).add(column.upper())
    specs = resolve_fields(ANNUAL if annual else MONTHLY, columns)
    required = requirements(specs)
    missing = [table + '.' + name for table, names in required.items()
               for name in sorted(names - columns.get(table, set()))]
    document_key = next((name for name in ('ID_DOKUMENT', 'ID') if name in columns.get('DOKUMENT', set())), None)
    if document_key is None:
        missing.append('DOKUMENT: ID_DOKUMENT lub ID')
    if missing:
        raise ValueError('Niekompletny standardowy schemat lub brak widoczności metadanych Płatnika: ' + ', '.join(missing))
    if not annual:
        for code, spec in resolve_fields(ADDITIONAL_MONTHLY, columns).items():
            table = spec['table']
            if table not in columns:
                continue
            missing_fields = requirements({code: spec})[table] - columns[table]
            if missing_fields:
                logging.getLogger(__name__).warning('Płatnik: pominięto %s, brak kolumn %s.',
                                                    table, ', '.join(sorted(missing_fields)))
            else:
                specs[code] = spec
    path = Path(__file__).resolve().parents[3] / 'docs/platnik/queries/declarations.sql'
    query = path.read_text(encoding='utf-8-sig')
    records = records_sql(specs, document_key, 'STATUSPT' in columns['DOKUMENT'],columns)
    period = "CONVERT(char(4),r.reporting_date,112) AS rok" if annual else "CONVERT(char(7),r.reporting_date,120) AS miesiac"
    scope_parameter, scope_cte, scope_filter = '', '', ''
    if scoped:
        scope_parameter = ', CAST(? AS xml) AS Payers'
        scope_cte = """SelectedNips AS (
    SELECT refs.nip.value('(text())[1]', 'nvarchar(10)') AS nip
    FROM Parameters p CROSS APPLY p.Payers.nodes('/p/n') AS refs(nip)
), """
        clean_nip = "UPPER(CONVERT(nvarchar(64),r.nip))"
        for separator in ("N' '", "N'-'", 'NCHAR(9)', 'NCHAR(10)', 'NCHAR(13)', 'NCHAR(160)'):
            clean_nip = f"REPLACE({clean_nip},{separator},N'')"
        scope_filter = f"""AND (r.nip IS NULL OR EXISTS (SELECT 1 FROM SelectedNips s
            CROSS APPLY (SELECT {clean_nip} AS nip) AS raw
            WHERE s.nip = CASE WHEN LEFT(raw.nip,2)=N'PL' THEN SUBSTRING(raw.nip,3,64) ELSE raw.nip END))"""
    return (query.replace('__RECORDS__', records).replace('__PERIOD_RESULT__', period)
            .replace('__SCOPE_PARAMETER__', scope_parameter).replace('__SCOPE_CTE__', scope_cte)
            .replace('__SCOPE_FILTER__', scope_filter))


def fetch(config, months, *, annual=False, selected_nips=None, expected_source=None):
    custom = str(config.get('annual_declarations_query' if annual else 'declarations_query') or '').strip()
    nips = None if selected_nips is None or custom else sorted({normalize_nip(nip) for nip in selected_nips})
    if nips == []:
        return []
    connection = connect(config)
    with sql_operation(config, 'Odczyt dokumentów formularzy ZUS'):
        try:
            cursor = connection.cursor()
            if expected_source is not None:
                from app.integrations.platnik.links import source_key
                if source_key(config, cursor) != expected_source:
                    raise ValueError('Źródło Płatnika zmieniło się podczas odczytu.')
            query = source_query(cursor, config, annual=annual, scoped=nips is not None)
            if not query.strip():
                raise ValueError('Plik zapytania formularzy ZUS jest pusty.')
            start, end = ((f'{months[0]}-01-01', f'{int(months[-1])+1}-01-01') if annual else
                          (month_bounds(months[0])[0], month_bounds(months[-1])[1]))
            parameters = [start, end]
            if nips is not None:
                parameters.append('<p>' + ''.join(f'<n>{nip}</n>' for nip in nips) + '</p>')
            cursor.execute(query, *parameters)
            columns = [column[0].lower() for column in cursor.description or []]
            expected = FIELDS - {'miesiac'} | {'rok'} if annual else FIELDS
            if not expected<=set(columns) or set(columns)-expected-{'insurance_code'} or len(columns)!=len(set(columns)):
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
    forms = ANNUAL if annual else MONTHLY | ADDITIONAL_MONTHLY
    supported_forms = {'ZUS ' + code for code in forms}
    seen, payers = set(), {}
    for row in rows:
        nip = normalize_nip(row['nip'])
        if nip not in result:
            continue
        month = str(row['rok' if annual else 'miesiac'] or '')
        if month not in months or row['valid_link'] != 1 or row['form'] not in supported_forms:
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
                 'payer_nip':nip,
                 'employee_id': employee_id, 'employee_name': employee_name,
                 'source_id': source_id, 'report_id': str(row['report_id'] or '').strip(),
                 'raw_status': code, 'status': 'unknown',
                 # Sam odczyt dokumentu nic nie mówi o wysyłce — nie piszemy,
                 # że dokument został „odczytany”, tylko wprost, czego nie wiadomo.
                 'status_text': 'Status źródłowy: ' + code if code else 'status wysyłki/UPO nieustalony',
                 'sent_at': None, 'upo': '', 'upo_at': None}
        if row.get('insurance_code'):
            from app.companies.holiday_eligibility import insurance_code
            entry['insurance_code'] = insurance_code(row['insurance_code'])
        if annual:
            entry['reporting_year'] = month
        result[nip][month].append(entry)
    return result


def normalize_for_import(rows, months, mapped, links, errors, *, annual=False):
    filtered, payer_ids = [], {}
    errors = dict(errors)
    for row in rows:
        nip = normalize_nip(row['nip'])
        company_id = mapped.get(nip)
        if company_id is None or company_id in errors:
            continue
        payer_id = str(row['payer_id'] or '')
        if payer_id and (link := links.get(nip)) and str(link['payer_id']) != payer_id:
            if not link.get('revision'):
                errors[company_id] = 'PlatnikPayerAmbiguous'
            continue
        if payer_id and payer_ids.setdefault(nip, payer_id) != payer_id:
            errors[company_id] = 'PlatnikPayerAmbiguous'
            continue
        filtered.append((company_id, row))
    ready = {nip: company_id for nip, company_id in mapped.items() if company_id not in errors}
    records = normalize((row for company_id, row in filtered if company_id not in errors), months, ready, annual=annual)
    return records, ready, errors


def payer_mapping(companies, company_ids=None):
    from app.companies.identities import payer_nips, is_civil
    from app.db.dao_companies import get_partners_for_companies

    selected_ids = None if company_ids is None else set(company_ids)
    selected = [row for row in companies if selected_ids is None or row['id'] in selected_ids]
    if selected_ids is not None and {row['id'] for row in selected} != selected_ids:
        raise ValueError('Nieznana firma w odczycie formularzy ZUS.')
    partners = get_partners_for_companies([row['id'] for row in companies if is_civil(row)])

    def identities(row):
        return payer_nips(row, partners.get(row['id'], ()))

    mapped = {}
    for row in selected:
        try:
            nips = identities(row)
        except ValueError:
            if selected_ids is not None:
                raise
            continue
        for nip in nips:
            if nip in mapped and mapped[nip] != row['id']:
                raise ValueError('Kilka firm Biuro ma ten sam NIP płatnika. Nie przypisano formularzy ZUS.')
            mapped[nip] = row['id']
    mapped_ids = set(mapped.values())
    for row in companies:
        if row['id'] in mapped_ids:
            continue
        try:
            nips = identities(row)
        except ValueError:
            continue
        if any(nip in mapped for nip in nips):
            raise ValueError('NIP wybranej firmy jest przypisany także innej firmie Biuro.')
    return mapped


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
    if company_ids is None and (not config.get('declarations_enabled',True) or not config['enabled']):
        return {'mode': 'disabled'}
    with get_coordination_conn() as lock:
        if not lock.execute('SELECT pg_try_advisory_xact_lock(748220)').fetchone()[0]:
            return {'mode': 'busy'}
        with get_conn() as conn:
            companies = conn.execute('SELECT id,nip,forma_dzialalnosci FROM firmy').fetchall()
        mapped = payer_mapping(companies, company_ids)
        if not mapped:
            return {'mode': 'empty', 'companies': 0, 'months': months, 'documents': 0}
        from app.integrations.platnik.links import import_links
        links, declaration_errors, source = import_links(config, mapped, annual=annual)
        from app.services.worker_activity import batch, record
        from app.companies.checking_obligations import add_imported_periods
        candidates = {nip: fid for nip, fid in mapped.items() if fid not in declaration_errors}
        if candidates:
            with batch('platnik', sorted(set(candidates.values()))):
                rows = fetch(config, months, annual=annual, selected_nips=candidates,
                             **({'expected_source': source} if source is not None else {}))
                records, ready, declaration_errors = normalize_for_import(
                    rows, months, mapped, links, declaration_errors, annual=annual,
                )
                nips_by_company = {}
                for nip, company_id in ready.items():
                    nips_by_company.setdefault(company_id, []).append(nip)
                stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
                with get_conn() as conn:
                    for companies_batch in batched(sorted(nips_by_company.items()), 100):
                        snapshots = []
                        for fid, nips in companies_batch:
                            periods = {month: [entry for nip in nips for entry in records[nip][month]] for month in months}
                            for month, entries in periods.items():
                                snapshot = json.dumps({'imported_at': stamp, 'declarations': entries}, ensure_ascii=False)
                                snapshots.append((fid, str(month), encrypt_password(snapshot)))
                            if not annual:
                                add_imported_periods(conn, fid, periods, 'platnik')
                        save_snapshots(conn, 'platnik_declarations', snapshots)
        else:
            records, ready, nips_by_company = {}, {}, {}
        for company_id, reason in declaration_errors.items():
            record('platnik', 'failed', company_id=company_id, error_type=reason)
        registry, registry_errors = None, {}
        if not annual and ready and config.get('server'):
            from app.integrations.platnik.insurance import refresh
            try:
                registry, registry_errors = refresh(config, ready, links, expected_source=source)
            except (ValueError, RuntimeError, DatabaseError) as error:
                registry = 'failed'
                registry_errors = dict.fromkeys(nips_by_company, 'InsuranceRegistryReadFailed')
                logging.getLogger(__name__).warning('Odczyt tytułów ZUS właścicieli: %s', type(error).__name__)
            for company_id, reason in registry_errors.items():
                record('platnik', 'failed', company_id=company_id, error_type=reason)
        documents = sum(len(entries) for by_month in records.values() for entries in by_month.values())
        # Jeden wpis w historii importów na cały odczyt Płatnika (miesiące albo rok).
        from app.db.import_audit import log_import_safely
        mode = ('partial' if ready else 'failed') if declaration_errors else 'applied'
        log_import_safely('platnik_declarations_annual' if annual else 'platnik_declarations',
                          source=config.get('database') or 'Płatnik',
                          updated=documents,
                          status={'applied': 'partial' if registry_errors else 'completed'}.get(mode, mode),
                          details={'companies': len(nips_by_company), 'months': months,
                                   'documents': documents, 'annual': annual},
                          user_id=actor_id)
        result = {'mode': mode, 'companies': len(nips_by_company), 'months': months, 'documents': documents}
        if declaration_errors:
            result['declaration_link_errors'] = declaration_errors
        if registry is not None:
            result['insurance_registry'] = registry
            result['insurance_registry_errors'] = registry_errors
        return result


def decoded_snapshot(value):
    return json.loads(decrypt_password(value)) if value else {}


@office_endpoint('app.integrations.platnik.declarations.import_months')
def import_months(months, *, company_ids=None, actor_id=None):
    return _import_periods(months, company_ids=company_ids, actor_id=actor_id)


@office_endpoint('app.integrations.platnik.declarations.import_year')
def import_year(year, *, company_ids=None, actor_id=None):
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError('Nieprawidłowy rok raportów ZUS.')
    return _import_periods([str(year)], company_ids=company_ids, actor_id=actor_id, annual=True)


@office_endpoint('app.integrations.platnik.declarations.read_year')
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
        rows = conn.execute('''SELECT f.*, y.company_json, a.payload AS platnik_annual
            FROM firmy f LEFT JOIN year_end_snapshots y ON y.firma_id=f.id AND y.rok=%s
            LEFT JOIN company_checking_snapshots a
                ON a.source='platnik_declarations' AND a.company_id=f.id AND a.period=%s
            WHERE f.id=ANY(%s) ORDER BY lower(f.nazwa)''', (year, str(year), ids)).fetchall()
    result = []
    for row in rows:
        company = dict(row)
        yearly = json.loads(company['company_json']) if company.get('company_json') else company
        codes = yearly['declaration_codes'] if 'declaration_codes' in yearly else company_declarations(yearly)
        configured = {form_key(value) for value in codes}
        applicable = [code for code in ANNUAL if code in configured]
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
