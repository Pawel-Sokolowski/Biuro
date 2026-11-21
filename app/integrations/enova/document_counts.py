"""Daily, repeatable import of monthly Enova counts. Preview is the default."""
from __future__ import annotations
from app.remote.boundary import endpoint as office_endpoint

import argparse
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from app.db.checking_storage import read_snapshot, save_snapshot
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn
from app.integrations.enova.counting_rules import load_rules, effective_divisor, validate_divisor
from app.integrations.enova.query_defaults import ADAPTER_REVISION, DOCUMENTS_QUERY, bundled_directory
from app.utils.months import month_bounds, previous_month


@dataclass(frozen=True)
class MonthlyCount:
    month: str
    documents: int
    bank_transactions: int
    total: int
    by_type: dict | None = None
    cash_transactions: int = 0
    report_types: dict | None = None

    @property
    def report_breakdown(self):
        return self.by_type if self.report_types is None else self.report_types


# Rodzaje dokumentów, które użytkownik może włączyć/wyłączyć do liczby dokumentów.
# Klucze są zgodne z podziałem `rodzaje` z Enovy (SPU = sprzedaż, ZR = zakup).
DOCUMENT_TYPE_LABELS = (
    ('SPU', 'Sprzedaż'),
    ('ZR', 'Zakup'),
    ('BANK', 'Bank'),
    ('POZ', 'Pozostałe'),
)


def counted_documents(by_type, included=None):
    """Suma dokumentów z podziału na rodzaje, z pominięciem wyłączonych rodzajów.

    ``included`` to zbiór rodzajów do wliczenia; ``None`` oznacza wszystkie
    (dotychczasowe zachowanie). Nieznane rodzaje są zawsze wliczane, żeby zmiana
    słownika w Enovie nie zaniżała liczby bez wiedzy użytkownika.
    """
    if by_type is None or included is None:
        return None
    included = {str(key).upper() for key in included}
    if not included:
        return None
    total = 0
    for kind, count in by_type.items():
        key = str(kind).upper()
        # Nieznany rodzaj liczymy zawsze — inaczej zmiana słownika w Enovie
        # po cichu zaniżałaby liczbę dokumentów.
        if key in included or key not in {known for known, _ in DOCUMENT_TYPE_LABELS}:
            total += count
    return total


def normalize_nip(value):
    text = re.sub(r'[\s-]', '', str(value or '').upper()).removeprefix('PL')
    if not re.fullmatch(r'[0-9]{10}', text):
        raise ValueError('NIP musi zawierać 10 cyfr.')
    return text


def sources_by_nip(sources):
    """Index mappings without collapsing duplicate matches."""
    indexed = {}
    for source in sources:
        try:
            nip = normalize_nip(source.get('nip'))
        except ValueError:
            # Configuration saves reject these; old data can contain isolated invalid mappings.
            continue
        indexed.setdefault(nip, []).append(source)
    return indexed


def whole_count(value):
    if value is None or isinstance(value, bool):
        raise ValueError('Brak liczby dokumentów; NULL nie oznacza zera.')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Liczba dokumentów musi być liczbą całkowitą.') from None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError('Liczba dokumentów musi być nieujemną liczbą całkowitą.')
    if number > 2147483647:
        raise ValueError('Liczba dokumentów przekracza zakres pola.')
    return int(number)


def calculate_total(kind, documents, bank_transactions, bank_divisor=3, *, cash_transactions=0):
    documents, bank_transactions = whole_count(documents), whole_count(bank_transactions)
    cash_transactions = whole_count(cash_transactions)
    if kind not in {'KPIR', 'RYCZALT', 'KH'}:
        raise ValueError('Typ księgowości: KPIR, RYCZALT lub KH.')
    if kind != 'KH' and bank_transactions:
        raise ValueError('Pozycje bankowe są uwzględniane wyłącznie dla KH.')
    if kind == 'RYCZALT' and cash_transactions:
        raise ValueError('Ryczałt nie dolicza pozycji raportów kasowych.')
    validate_divisor(bank_divisor)
    total = Decimal(documents)
    total += Decimal(bank_transactions + cash_transactions) / bank_divisor
    return whole_count(total.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def requested_months(today, months_back):
    if type(months_back) is not int or not 1 <= months_back <= 24:
        raise ValueError('months_back musi wynosić od 1 do 24.')
    months = [today.strftime('%Y-%m')]
    for _ in range(months_back - 1):
        months.append(previous_month(months[-1]))
    return sorted(months)


def validate_rows(rows, source, months, bank_divisor=3):
    found = {}
    expected_nip = normalize_nip(source['nip'])
    kind = source['accounting_type']
    for row in rows:
        if normalize_nip(row['nip']) != expected_nip:
            raise ValueError('NIP zwrócony przez Enova nie odpowiada mapowaniu firmy.')
        month = str(row['miesiac'])
        if month not in months or month in found:
            raise ValueError('Powtórzony miesiąc lub miesiąc spoza zakresu importu.')
        documents = whole_count(row['dokumenty'])
        bank = whole_count(row['pozycje_bankowe'])
        cash = whole_count(row.get('pozycje_kasowe', 0))
        breakdown = row.get('rodzaje')
        if isinstance(breakdown, str):
            breakdown = json.loads(breakdown)
        if breakdown is not None:
            if not isinstance(breakdown, dict):
                raise ValueError('Nieprawidłowy podział dokumentów.')
            breakdown = {str(k): whole_count(v) for k, v in breakdown.items()}
            if sum(breakdown.values()) != documents:
                raise ValueError('Podział dokumentów nie odpowiada sumie.')
        total = calculate_total(kind, documents, bank, bank_divisor, cash_transactions=cash)
        report_types = None
        if 'sprzedaz' in row or 'zakup' in row:
            sales = whole_count(row.get('sprzedaz'))
            purchases = whole_count(row.get('zakup'))
            if sales + purchases > documents:
                raise ValueError('Sprzedaż i zakupy przekraczają sumę dokumentów.')
            if kind in ('KPIR', 'RYCZALT'):
                report_types = {'SPU': sales, 'ZR': purchases, 'DW': total - sales - purchases}
            elif breakdown is not None:
                report_types = dict(breakdown)
                # Allocate the rounded contribution without rounding the month twice.
                bank_count = calculate_total(kind, 0, bank, bank_divisor)
                report_types.update(WBEW=bank_count, RKVE=total - documents - bank_count)
        found[month] = MonthlyCount(month, documents, bank, total, breakdown, cash, report_types)
    if set(found) != set(months):
        missing = ', '.join(month for month in months if month not in found)
        raise ValueError(f"Baza {source.get('database','Enova')}: odczytano {len(found)}/{len(months)} miesięcy. "
                         f'Brak okresów: {missing}. Brak wiersza nie oznacza zera. '
                         'Sprawdź NIP właściciela w bazie Enovy oraz zapytanie źródłowe; forma KPiR/VAT w Biuro nie uzupełnia tych wierszy.')
    return [found[month] for month in months]


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    config['_directory'] = path.parent
    if not isinstance(config.get('enabled', False), bool):
        raise ValueError('enabled musi być wartością true lub false.')
    requested_months(date.today(), config.get('months_back', 3))
    if not config.get('server') or not config.get('sources'):
        raise ValueError('Uzupełnij server i mapowania sources.')
    seen_nips, seen_databases = set(), set()
    for source in config['sources']:
        nip = normalize_nip(source['nip'])
        database = source['database']
        if not isinstance(database, str) or not database.strip():
            raise ValueError('Uzupełnij nazwę bazy Enova.')
        if nip in seen_nips or database.casefold() in seen_databases:
            raise ValueError('Każda firma i baza mogą wystąpić w konfiguracji tylko raz.')
        seen_nips.add(nip)
        seen_databases.add(database.casefold())
        if source.get('accounting_type'):
            calculate_total(source['accounting_type'], 0, 0)
    from app.integrations.enova.query_defaults import with_query_defaults
    return with_query_defaults(config)


def _odbc_quote(value):
    return '{' + str(value).replace('}', '}}') + '}'


def sql_credentials():
    # Keep credentials as a pair; never mix a partial prefixed pair with aliases.
    for user_key, password_key in (('ENOVA_SQL_USER', 'ENOVA_SQL_PASSWORD'),
                                   ('sql_user', 'sql_password'), ('SQL_USER', 'SQL_PASSWORD')):
        user, password = os.environ.get(user_key), os.environ.get(password_key)
        if user or password:
            if not user or not password:
                raise ValueError(f'Uzupełnij parę {user_key} i {password_key} w środowisku konta zadania.')
            return user, password
    raise ValueError('Ustaw sql_user i sql_password lub ENOVA_SQL_USER i ENOVA_SQL_PASSWORD w pliku .env.')


def sql_connection_string(config, database, user, password):
    from app.integrations.enova.odbc_support import validate_server
    trust = config.get('trust_server_certificate', False)
    if type(trust) is not bool:
        raise ValueError('Nieprawidłowa opcja zaufania certyfikatowi serwera.')
    values = {
        'DRIVER': config.get('driver', 'ODBC Driver 18 for SQL Server'),
        'SERVER': validate_server(config['server']), 'UID': user, 'PWD': password,
        'Encrypt': 'yes', 'TrustServerCertificate': 'yes' if trust else 'no', 'APP': 'Biuro document counts',
    }
    if database:
        values['DATABASE'] = database
    return ';'.join(f'{key}={_odbc_quote(value)}' for key, value in values.items())


def _bind_company_identity(cursor,source):
    """A saved database-to-company mapping can fill missing identity, never a conflict."""
    expected = normalize_nip(source['nip'])
    cursor.execute('SELECT DISTINCT NIP FROM dbo.HstDanychFirmy WHERE Termin='
                   '(SELECT MAX(Termin) FROM dbo.HstDanychFirmy WHERE Termin<=CONVERT(date,GETDATE()))')
    identities = cursor.fetchmany(1001)
    if len(identities)>1000:
        raise ValueError('Za wiele identyfikatorów historii firmy w bazie Enovy.')
    actual = set()
    for identity in identities:
        try:
            actual.add(normalize_nip(identity[0]))
        except ValueError:
            pass  # Empty history fields and PESEL are not company NIPs.
    if actual and actual != {expected}:
        raise ValueError(f"Baza {source['database']}: poprawny NIP w danych Enovy jest sprzeczny "
                         'z powiązaniem firmy w Biuro. Nie zaimportowano danych.')
    if not actual:
        import logging
        logging.getLogger(__name__).warning('enova_identity_from_saved_mapping database=%s',source['database'])
    cursor.execute("EXEC sys.sp_set_session_context @key=N'biuro_company_nip', @value=?",expected)


def fetch_counts(config, source, months, *, result_kind="counts"):
    """SQL Server credentials must be SELECT-only; readonly is also requested from ODBC."""
    from app.integrations.enova.odbc_support import load_odbc, resolve_driver
    pyodbc = load_odbc()
    config = {**config, 'driver': resolve_driver(config, pyodbc)}
    if config.get('password_enc'):
        from app.integrations.enova.connection_settings import credentials
        user, password = credentials(config)
    else:
        user, password = sql_credentials()
    query_path = Path(source['count_query'])
    if not query_path.is_absolute():
        query_path = config['_directory'] / query_path
    query = query_path.read_text(encoding='utf-8-sig')
    if not query.strip():
        raise ValueError('Plik zapytania jest pusty.')
    connection_string = sql_connection_string(config, source['database'], user, password)
    from time import perf_counter
    started = perf_counter()
    connection = pyodbc.connect(connection_string, timeout=15, attrs_before={101: 1})
    connected = perf_counter()
    try:
        cursor = connection.cursor()
        from app.integrations.enova.discovery import query_timeout
        connection.timeout = query_timeout(config)
        bundled = query_path.resolve().parent == bundled_directory().resolve() and query_path.name in (
            DOCUMENTS_QUERY, 'kpir.sql','kh.sql','ryczalt.sql','checking.sql','jpk.sql','declarations.sql','payroll.sql')
        if bundled:
            _bind_company_identity(cursor,source)
        if bundled and query_path.name == DOCUMENTS_QUERY:
            cursor.execute("EXEC sys.sp_set_session_context @key=N'biuro_accounting_type', @value=?", source['accounting_type'])
            cursor.execute("EXEC sys.sp_set_session_context @key=N'biuro_vat_czynny', @value=?", source.get('vat_czynny'))
        # Exactly two positional date parameters: inclusive start, exclusive end.
        cursor.execute(query, date.fromisoformat(month_bounds(months[0])[0]),
                       date.fromisoformat(month_bounds(months[-1])[1]))
        columns = [column[0].lower() for column in cursor.description or []]
        contracts = {
            'counts': {'nip', 'miesiac', 'dokumenty', 'pozycje_bankowe'},
            'jpk': {'nip', 'miesiac', 'status', 'upo'},
            'declarations': {'nip', 'miesiac', 'department', 'form', 'status', 'sent_at', 'upo', 'upo_at'},
            'payroll': {'nip', 'miesiac', 'rodzaj', 'liczba'},
            'hr_events': {'nip', 'event_id', 'employee_id', 'employee_name', 'event_type', 'due_date', 'status'},
        }
        required = contracts[result_kind]
        optional = ({'rodzaje', 'pozycje_kasowe', 'sprzedaz', 'zakup'} if result_kind == 'counts' else
                    {'employee_id', 'employee_name', 'source_system', 'raw_status', 'form_variant', 'created_at'} if result_kind == 'declarations' else
                    {'document_number'} if result_kind == 'hr_events' else
                    {'raw_status', 'form_variant', 'sent_at', 'upo_at'} if result_kind == 'jpk' else set())
        if not required <= set(columns) or set(columns) - required - optional or len(columns) != len(set(columns)):
            raise ValueError('Nieprawidłowe kolumny zapytania: ' + ', '.join(sorted(required)))
        limit = len(months) if result_kind in ('counts', 'jpk') else 20000
        rows = cursor.fetchmany(limit + 1)
        if len(rows) > limit:
            raise ValueError('Zapytanie zwróciło za wiele wierszy.')
        if not rows and result_kind=='counts' and bundled:
            raise ValueError(f"Baza {source['database']}: zapytanie nie zwróciło wymaganych miesięcy "
                             'mimo ustalonego powiązania firmy. Nie zapisano zer; sprawdź adapter i okres importu.')
        return [dict(zip(columns, row)) for row in rows]
    finally:
        import logging
        duration = perf_counter() - connected
        logging.getLogger(__name__).log(
            logging.INFO if duration + connected - started >= .25 else logging.DEBUG,
            'enova_source_read kind=%s connect_s=%.3f query_s=%.3f',
            result_kind, connected - started, duration)
        connection.close()


def find_company(conn, nip):
    rows = conn.execute("SELECT id, nazwa, forma_opodatkowania, forma_dzialalnosci, status_vat, vat_czynny FROM firmy WHERE regexp_replace(upper(nip), '[[:space:]-]', '', 'g') "
                        "IN (%s, %s)", (nip, 'PL' + nip)).fetchall()
    if len(rows) != 1:
        raise ValueError('NIP musi wskazywać dokładnie jedną firmę w Biuro.')
    return dict(rows[0])


def resolve_source(source, company):
    """Use Biuro's accounting classification; legacy explicit types remain a fallback."""
    from app.companies.cennik_keys import company_accounting_type, vat_flag_from_status
    resolved = dict(source)
    kind = company_accounting_type(company)
    if company.get('forma_opodatkowania') and not kind:
        # Nieznana etykieta nie może blokować importu: zostaje zapisane powiązanie
        # z ustawień Enovy, a gdy i tego nie ma — domyślnie KPiR.
        import logging
        logging.getLogger(__name__).warning(
            'enova_accounting_type_unknown company_id=%s forma=%s',
            company.get('id'), company.get('forma_opodatkowania'))
    kind = kind or source.get('accounting_type')
    if not kind:
        from app.companies.cennik_keys import supported_forma_labels
        raise ValueError(
            f"Uzupełnij formę opodatkowania firmy {company.get('nazwa') or company.get('id')} w Biuro "
            f"(obsługiwane: {', '.join(supported_forma_labels())}; puste pole nie wystarcza).")
    resolved['accounting_type'] = kind
    if not source.get('count_query'):
        if kind not in ('KPIR', 'KH', 'RYCZALT'):
            raise ValueError('Brak gotowego zapytania dla rodzaju księgowości.')
        if kind == 'RYCZALT':
            status = str(company.get('status_vat') or '').strip()
            vat_flag = vat_flag_from_status(status)
            if status.casefold() == 'niezarejestrowany':
                vat_flag = 'N'
            if vat_flag is not None:
                resolved['vat_czynny'] = vat_flag == 'T'
            elif not status and company.get('vat_czynny') in (0, 1):
                resolved['vat_czynny'] = bool(company['vat_czynny'])
            elif type(source.get('vat_czynny')) is not bool:
                raise ValueError(f"Uzupełnij status VAT firmy {company.get('nazwa') or company.get('id')} w Biuro.")
        resolved['count_query'] = str(bundled_directory() / DOCUMENTS_QUERY)
    elif source.get('accounting_type') and source['accounting_type'] != kind:
        raise ValueError('Typ starego count_query jest niezgodny z formą opodatkowania w Biuro. Usuń stare accounting_type/count_query lub popraw mapowanie.')
    return resolved


def apply_counts(conn, company, source, counts, *, bank_divisor=3):
    changes = []
    for count in counts:
        previous = conn.execute('SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s AND miesiac=%s FOR UPDATE',
                                (company['id'], count.month)).fetchone()
        old_snapshot = read_snapshot(conn, 'enova_snapshot', company['id'], count.month)
        old_total = old_snapshot.get('total') if old_snapshot is not None else None
        manual_key = f"manual_document_count_{company['id']}_{count.month}"
        manual = conn.execute('SELECT 1 FROM app_settings WHERE klucz=%s', (manual_key,)).fetchone()
        provenance = conn.execute('SELECT source_name FROM monthly_document_imports WHERE firma_id=%s AND miesiac=%s',
                                  (company['id'], count.month)).fetchone()
        current = previous[0] if previous else None
        # Legacy nonzero values and other imports belong to the user. Imported values
        # remain refreshable only while they still match our last successful write.
        protected = bool(manual or (provenance and not provenance[0].startswith('Enova: ')) or
                         (old_snapshot is not None and current != old_total) or
                         (old_snapshot is None and current not in (None, 0)))
        if protected:
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                         (manual_key, '1'))
        else:
            conn.execute('INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok) VALUES (%s,%s,%s) '
                         'ON CONFLICT(firma_id,miesiac) DO UPDATE SET ilosc_dok=EXCLUDED.ilosc_dok',
                         (company['id'], count.month, count.total))
            conn.execute('INSERT INTO monthly_document_imports(firma_id,miesiac,source_name) VALUES (%s,%s,%s) '
                         'ON CONFLICT(firma_id,miesiac) DO UPDATE SET source_name=EXCLUDED.source_name, '
                         'imported_by=NULL, imported_at=now()',
                         (company['id'], count.month, 'Enova: ' + source['database']))
        snapshot = {'documents': count.documents, 'bank_transactions': count.bank_transactions,
                    'cash_transactions': count.cash_transactions, 'by_type': count.by_type,
                    'report_types': count.report_types, 'total': count.total, 'bank_divisor': bank_divisor,
                    'database': source['database'], 'accounting_type': source['accounting_type'],
                    'imported_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
        save_snapshot(conn, 'enova_snapshot', company['id'], count.month, snapshot)
        changes.append({'month': count.month, 'before': previous[0] if previous else None,
                        'after': current if protected else count.total, 'manual_preserved': protected, 'documents': count.documents, 'bank_transactions': count.bank_transactions})
    details = {'company_id': company['id'], 'database': source['database'],
               'accounting_type': source['accounting_type'], 'bank_divisor': bank_divisor, 'changes': changes}
    conn.execute("INSERT INTO import_history(import_type,source_path,updated_count,completed_at,status,details) "
                 "VALUES ('enova_document_counts',%s,%s,to_char(now(),'YYYY-MM-DD HH24:MI:SS'),'completed',%s)",
                 (source['database'], sum(not change['manual_preserved'] for change in changes), json.dumps(details, ensure_ascii=False)))
    return changes


def count_check_key(company_id, month):
    return f'enova_document_count_checked_{company_id}_{month}'


def _eligible_months(conn, company_id, months, signature, *, apply):
    rows = conn.execute(
        "SELECT s.miesiac, a.wartosc FROM statusy_miesieczne s LEFT JOIN app_settings a "
        "ON a.klucz = 'enova_document_count_checked_' || s.firma_id::text || '_' || s.miesiac "
        "WHERE s.firma_id=%s AND s.miesiac=ANY(%s) AND s.etap='Wysłane wyniki' ORDER BY s.miesiac",
        (company_id, months)).fetchall()
    eligible = []
    for row in rows:
        try:
            checked = json.loads(row['wartosc']) if row['wartosc'] else None
        except (ValueError, TypeError):
            checked = None
        # Preview respects etap but never consumes the daily successful-check marker.
        if not apply or checked != signature:
            eligible.append(row['miesiac'])
    return eligible


def month_batches(months):
    """Two-bound SQL adapters can batch adjacent months without reading gaps."""
    batches = []
    for month in sorted(set(months)):
        if batches and month_bounds(batches[-1][-1])[1][:7] == month:
            batches[-1].append(month)
        else:
            batches.append([month])
    return batches


@office_endpoint('app.integrations.enova.document_counts.run_import')
def run_import(config, *, apply=False, today=None, fetch=None, months=None):
    if apply and not config.get('enabled', False):
        raise ValueError('Import wyłączony. Najpierw sprawdź podgląd, następnie ustaw enabled=true.')
    if not config.get('sources'):
        raise ValueError('Połączenie Enovy można zapisać bez baz firm. Przed importem znajdź bazy po NIP lub przypisz je ręcznie i zapisz ustawienia.')
    if fetch is None:
        fetch = fetch_counts
    run_day = today or datetime.now(ZoneInfo('Europe/Warsaw')).date()
    months = requested_months(run_day, config.get('months_back', 3)) if months is None else list(months)
    for month in months:
        month_bounds(month)
    # Historical counts belong to Sprawdzanie. Only periods starting with this
    # deployment's activation month may feed the monthly billing counter.
    first_month = config.get('monthly_counts_from') or run_day.strftime('%Y-%m')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', first_month):
        raise ValueError('Nieprawidłowy miesiąc rozpoczęcia importu do widoku miesięcznego.')
    monthly_months = [month for month in months if month >= first_month]
    rules = load_rules()
    results = []
    with get_conn() as conn:
        if apply and not conn.execute('SELECT pg_try_advisory_xact_lock(748209)').fetchone()[0]:
            raise ValueError('Inny import Enova jest już uruchomiony.')
        for source in config['sources']:
            company = None
            try:
                with conn.transaction():
                    company = find_company(conn, normalize_nip(source['nip']))
                    if apply:
                        from app.services.worker_activity import record
                        record('enova', 'reading', company_id=company['id'])
                    source = resolve_source(source, company)
                    divisor = effective_divisor(rules, company['id']) if source['accounting_type'] in ('KH', 'KPIR') else 1
                    signature = {'day': run_day.isoformat(), 'database': source['database'], 'bank_divisor': divisor,
                                 'accounting_type': source['accounting_type'], 'vat_czynny': source.get('vat_czynny'),
                                 'adapter_revision': ADAPTER_REVISION, 'query': str(source['count_query']), 'server': config.get('server')}
                    jpk_result = jpk_error = None
                    if source.get('jpk_query'):
                        from app.integrations.enova.jpk import import_jpk
                        try:
                            with conn.transaction():
                                jpk_result = import_jpk(conn, config, source, company['id'], months, run_day, apply=apply)
                        except Exception as exc:
                            jpk_error = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
                    eligible = _eligible_months(conn, company['id'], monthly_months, signature, apply=apply)
                    counts = []
                    # Batch adjacent months, preserving the adapter's two date bounds.
                    for batch in month_batches(eligible):
                        ready_months = [row[0] for row in conn.execute(
                            "SELECT miesiac FROM statusy_miesieczne WHERE firma_id=%s "
                            "AND miesiac=ANY(%s) AND etap='Wysłane wyniki' ORDER BY miesiac",
                            (company['id'],batch)).fetchall()]
                        for ready_batch in month_batches(ready_months):
                            counts.extend(validate_rows(fetch(config,source,ready_batch),source,ready_batch,divisor))
                    changes = []
                    if apply:
                        # Recheck once after the remote read and lock the accepted rows through the write.
                        still_ready = {row[0] for row in conn.execute(
                            "SELECT miesiac FROM statusy_miesieczne WHERE firma_id=%s "
                            "AND miesiac=ANY(%s) AND etap='Wysłane wyniki' FOR UPDATE",
                            (company['id'],[count.month for count in counts])).fetchall()} if counts else set()
                        counts = [count for count in counts if count.month in still_ready]
                        if counts:
                            changes = apply_counts(conn, company, source, counts, bank_divisor=divisor)
                            for count in counts:
                                conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                                             'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                                             (count_check_key(company['id'], count.month), json.dumps(signature)))
                    results.append({'database': source['database'], 'company_id': company['id'],
                                    'mode': ('applied' if apply else 'preview') if counts else 'skipped',
                                    'skipped_months': [month for month in months if month not in {c.month for c in counts}],
                                    'bank_divisor': divisor, 'jpk': jpk_result,
                                    'manual_preserved': [change['month'] for change in changes if change['manual_preserved']],
                                    'counts': {count.month: count.total for count in counts},
                                    'components': {count.month: {'documents': count.documents,
                                                               'bank_transactions': count.bank_transactions,
                                                               'cash_transactions': count.cash_transactions}
                                                   for count in counts}, 'ok': jpk_error is None})
                    if jpk_error is not None:
                        results[-1]['error'] = 'JPK: ' + jpk_error
                    if apply and not changes:
                        from app.db.import_audit import log_import
                        log_import(conn, 'enova_document_counts', source=source['database'],
                                   status='failed' if jpk_error else 'completed',
                                   details={'company_id': company['id'], 'accounting_type': source['accounting_type'],
                                            'months': [], 'skipped_months': months, 'jpk_error': jpk_error})
                if apply:
                    record('enova', 'read' if jpk_error is None else 'failed', company_id=company['id'])
            except Exception as exc:
                if apply:
                    from app.services.worker_activity import record
                    record('enova', 'failed', company_id=company['id'] if company else None)
                error = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
                results.append({'database': source['database'], 'ok': False, 'error': error})
                if apply:
                    from app.db.import_audit import log_import
                    log_import(conn, 'enova_document_counts', source=source['database'], status='failed',
                               details={'company_id': company['id'] if company else None, 'error': error})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--config')
    group.add_argument('--saved-settings', action='store_true', help='Użyj połączenia zapisanego w aplikacji.')
    parser.add_argument('--apply', action='store_true', help='Zapisz wyniki; domyślnie tylko podgląd.')
    args = parser.parse_args(argv)
    try:
        from app.integrations.enova.connection_settings import load_connection
        config = load_connection() if args.saved_settings else load_config(args.config)
        results = run_import(config, apply=args.apply)
        if config.get('checking_enabled', False):
            from app.integrations.enova.checking import run_checking_import
            results.extend(run_checking_import(config, apply=args.apply))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}, ensure_ascii=True))
        return 1
    for result in results:
        print(json.dumps(result, ensure_ascii=True))
    return int(any(not result['ok'] for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
