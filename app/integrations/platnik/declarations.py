"""Standard Płatnik DRA/RCA inventory, without guessing transmission status codes."""
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn, get_coordination_conn
from app.integrations.enova.document_counts import normalize_nip
from app.integrations.platnik.holidays import load, connect, sql_operation
from app.utils.crypto import encrypt_password, decrypt_password
from app.utils.months import month_bounds, previous_month

REQUIRED = {
    'DOKUMENT': {'ID_DOKUMENT', 'ID_PLATNIK'},
    'ZUSDRA': {'ID_DOKUMENT', 'ID_PLATNIK', 'II_1_NIP', 'I_2_2OKRESDEKLAR', 'I_2_1IDDEKLS'},
    'ZUSRCA': {'ID_DOKUMENT', 'ID_PLATNIK', 'II_1_NIP', 'I_1_2OKRROZL', 'I_1_1IDRAPS'},
    'UBEZP_ZUSRCA': {'ID_DOKUMENT', 'ID_DOK_NAD', 'ID_PLATNIK', 'ID_UBEZPIECZONY',
                    'III_A_2_IMIEPIERW', 'III_A_1_NAZWISKO'},
}
FIELDS = {'form', 'source_id', 'payer_id', 'nip', 'report_id', 'miesiac',
          'employee_id', 'employee_name', 'raw_status', 'valid_link'}


def source_query(cursor, config):
    override = str(config.get('declarations_query') or '').strip()
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError('Zamiennik SQL Płatnika wymaga pełnej ścieżki na serwerze workera.')
        return path.read_text(encoding='utf-8-sig')
    cursor.execute("SELECT t.name,c.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id "
                   "JOIN sys.columns c ON c.object_id=t.object_id WHERE s.name='dbo' "
                   "AND t.name IN ('DOKUMENT','ZUSDRA','ZUSRCA','UBEZP_ZUSRCA')")
    metadata = cursor.fetchmany(2001)
    if len(metadata) > 2000:
        raise ValueError('Za dużo metadanych adaptera Płatnika.')
    columns = {}
    for table, column in metadata:
        columns.setdefault(table.upper(), set()).add(column.upper())
    missing = [table + '.' + name for table, names in REQUIRED.items()
               for name in sorted(names - columns.get(table, set()))]
    if missing:
        raise ValueError('Niekompletny standardowy schemat lub brak widoczności metadanych Płatnika: ' + ', '.join(missing))
    path = Path(__file__).resolve().parents[3] / 'docs/platnik/queries/declarations.sql'
    query = path.read_text(encoding='utf-8-sig')
    status = 'CONVERT(nvarchar(32),d.STATUSPT)' if 'STATUSPT' in columns['DOKUMENT'] else 'CAST(NULL AS nvarchar(32))'
    return query.replace('__DRA_STATUS__', status).replace('__RCA_STATUS__', status)


def fetch(config, months):
    connection = connect(config)
    with sql_operation(config, 'Odczyt dokumentów RCA/DRA'):
        try:
            cursor = connection.cursor()
            query = source_query(cursor, config)
            if not query.strip():
                raise ValueError('Plik zapytania RCA/DRA jest pusty.')
            cursor.execute(query, month_bounds(months[0])[0], month_bounds(months[-1])[1])
            columns = [column[0].lower() for column in cursor.description or []]
            if set(columns) != FIELDS or len(columns) != len(FIELDS):
                raise ValueError('Nieprawidłowy kontrakt kolumn RCA/DRA.')
            rows = []
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                rows.extend(dict(zip(columns, row)) for row in batch)
                if len(rows) > 100000:
                    raise ValueError('Odczyt RCA/DRA przekracza 100000 rekordów. Poprzednie dane pozostawiono.')
            return rows
        finally:
            connection.close()


def normalize(rows, months, selected_nips):
    result = {nip: {month: [] for month in months} for nip in selected_nips}
    seen, payers = set(), {}
    for row in rows:
        nip = normalize_nip(row['nip'])
        if nip not in result:
            continue
        month = str(row['miesiac'] or '')
        if month not in months or row['valid_link'] != 1 or row['form'] not in ('ZUS DRA', 'ZUS RCA'):
            raise ValueError('Nieprawidłowy okres lub powiązanie dokumentu RCA/DRA. Poprzednie dane pozostawiono.')
        source_id, payer_id = str(row['source_id'] or ''), str(row['payer_id'] or '')
        if not source_id or not payer_id:
            raise ValueError('Brak identyfikatora dokumentu lub płatnika RCA/DRA.')
        if nip in payers and payers[nip] != payer_id:
            raise ValueError('Kilka kartotek płatnika ma ten sam NIP. Nie połączono dokumentów.')
        payers[nip] = payer_id
        identity = (payer_id, row['form'], source_id)
        if identity in seen:
            raise ValueError('Powtórzony dokument RCA/DRA. Poprzednie dane pozostawiono.')
        seen.add(identity)
        employee_id = str(row['employee_id'] or '')
        employee_name = str(row['employee_name'] or '').strip()
        if bool(employee_id) != bool(employee_name):
            raise ValueError('RCA osoby wymaga identyfikatora i nazwy ubezpieczonego.')
        code = str(row['raw_status']).strip() if row['raw_status'] is not None else ''
        entry = {'department': 'payroll', 'form': row['form'], 'source_system': 'platnik',
                 'employee_id': employee_id, 'employee_name': employee_name,
                 'source_id': source_id, 'report_id': str(row['report_id'] or '').strip(),
                 'raw_status': code, 'status': 'unknown',
                 'status_text': 'Status źródłowy: ' + code if code else 'Odczytano dokument; status wysyłki nieustalony',
                 'sent_at': None, 'upo': '', 'upo_at': None}
        result[nip][month].append(entry)
    return result


def import_months(months, *, company_ids=None, actor_id=None):
    if actor_id is not None:
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Tylko administrator uruchamia bezpośredni odczyt Płatnika.')
    if not months or months != sorted(set(months)) or len(months) > 24:
        raise ValueError('Nieprawidłowy zakres miesięcy RCA/DRA.')
    for month in months:
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
            raise ValueError('Nieznana firma w odczycie RCA/DRA.')
        mapped = {}
        for row in selected:
            nip = normalize_nip(row['nip'])
            if nip in mapped:
                raise ValueError('Kilka firm Biuro ma ten sam NIP. Nie przypisano dokumentów RCA/DRA.')
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
        records = normalize(fetch(config, months), months, mapped)
        stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
        with get_conn() as conn:
            for nip, fid in mapped.items():
                for month, entries in records[nip].items():
                    snapshot = json.dumps({'imported_at': stamp, 'declarations': entries}, ensure_ascii=False)
                    conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                                 'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                                 (f'platnik_declarations_{fid}_{month}', encrypt_password(snapshot)))
        return {'mode': 'applied', 'companies': len(mapped), 'months': months,
                'documents': sum(len(entries) for by_month in records.values() for entries in by_month.values())}


def decoded_snapshot(value):
    return json.loads(decrypt_password(value)) if value else {}


def worker_job():
    month = datetime.now(ZoneInfo('Europe/Warsaw')).strftime('%Y-%m')
    return import_months([previous_month(month), month])
