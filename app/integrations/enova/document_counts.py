"""Daily, repeatable import of monthly Enova counts. Preview is the default."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn
from app.integrations.enova.counting_rules import load_rules, effective_divisor, validate_divisor
from app.utils.months import month_bounds, previous_month


@dataclass(frozen=True)
class MonthlyCount:
    month: str
    documents: int
    bank_transactions: int
    total: int
    by_type: dict | None = None


def normalize_nip(value):
    text = re.sub(r'[\s-]', '', str(value or '').upper()).removeprefix('PL')
    if not re.fullmatch(r'[0-9]{10}', text):
        raise ValueError('NIP musi zawierać 10 cyfr.')
    return text


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


def calculate_total(kind, documents, bank_transactions, bank_divisor=3):
    documents, bank_transactions = whole_count(documents), whole_count(bank_transactions)
    if kind not in {'KPIR', 'RYCZALT', 'KH'}:
        raise ValueError('Typ księgowości: KPIR, RYCZALT lub KH.')
    if kind != 'KH' and bank_transactions:
        raise ValueError('Pozycje bankowe są uwzględniane wyłącznie dla KH.')
    validate_divisor(bank_divisor)
    total = Decimal(documents)
    if kind == 'KH':
        total += Decimal(bank_transactions) / bank_divisor
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
    for row in rows:
        if normalize_nip(row['nip']) != expected_nip:
            raise ValueError('NIP zwrócony przez Enova nie odpowiada mapowaniu firmy.')
        month = str(row['miesiac'])
        if month not in months or month in found:
            raise ValueError('Powtórzony miesiąc lub miesiąc spoza zakresu importu.')
        documents = whole_count(row['dokumenty'])
        bank = whole_count(row['pozycje_bankowe'])
        breakdown = row.get('rodzaje')
        if isinstance(breakdown, str):
            breakdown = json.loads(breakdown)
        if breakdown is not None:
            if not isinstance(breakdown, dict):
                raise ValueError('Nieprawidłowy podział dokumentów.')
            breakdown = {str(k): whole_count(v) for k, v in breakdown.items()}
            if sum(breakdown.values()) != documents:
                raise ValueError('Podział dokumentów nie odpowiada sumie.')
        found[month] = MonthlyCount(month, documents, bank,
                                   calculate_total(source['accounting_type'], documents, bank, bank_divisor), breakdown)
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
    connection = pyodbc.connect(connection_string, timeout=15, attrs_before={101: 1})
    try:
        cursor = connection.cursor()
        from app.integrations.enova.discovery import query_timeout
        connection.timeout = query_timeout(config)
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
        optional = ({'rodzaje'} if result_kind == 'counts' else
                    {'employee_id', 'employee_name', 'source_system', 'raw_status'} if result_kind == 'declarations' else
                    {'document_number'} if result_kind == 'hr_events' else
                    {'raw_status'} if result_kind == 'jpk' else set())
        if not required <= set(columns) or set(columns) - required - optional or len(columns) != len(set(columns)):
            raise ValueError('Nieprawidłowe kolumny zapytania: ' + ', '.join(sorted(required)))
        limit = len(months) if result_kind in ('counts', 'jpk') else 20000
        rows = cursor.fetchmany(limit + 1)
        if len(rows) > limit:
            raise ValueError('Zapytanie zwróciło za wiele wierszy.')
        if not rows and result_kind=='counts' and query_path.resolve().parent == (Path(__file__).resolve().parents[3] / 'docs/enova/queries').resolve():
            cursor.execute('SELECT DISTINCT DeklaracjeNIP FROM dbo.OddzialyFirmy')
            identities = cursor.fetchmany(1001)
            nips = set()
            for identity in identities:
                try:
                    nips.add(normalize_nip(identity[0]))
                except ValueError:
                    pass
            expected = normalize_nip(source['nip'])
            if not nips:
                raise ValueError(f"Baza {source['database']}: zapytanie nie zwróciło żadnych miesięcy, ponieważ nie odczytano "
                                 f'poprawnego NIP-u z dbo.OddzialyFirmy.DeklaracjeNIP. NIP firmy w Biuro: {expected}. '
                                 'Sprawdź dane właściciela tej bazy w Enovie i ręczne powiązanie bazy z firmą. Nie zapisano zer.')
            if nips!={expected}:
                raise ValueError(f"Baza {source['database']}: NIP-y właściciela ({', '.join(sorted(nips))}) "
                                 f'nie odpowiadają jednej firmie Biuro ({expected}). Sprawdź powiązanie bazy.')
        return [dict(zip(columns, row)) for row in rows]
    finally:
        connection.close()


def find_company(conn, nip):
    rows = conn.execute("SELECT id, nazwa, forma_opodatkowania, forma_dzialalnosci, status_vat FROM firmy WHERE regexp_replace(upper(nip), '[[:space:]-]', '', 'g') "
                        "IN (%s, %s)", (nip, 'PL' + nip)).fetchall()
    if len(rows) != 1:
        raise ValueError('NIP musi wskazywać dokładnie jedną firmę w Biuro.')
    return dict(rows[0])


def resolve_source(source, company):
    """Use Biuro's accounting classification; legacy explicit types remain a fallback."""
    from app.companies.cennik_keys import company_accounting_type
    resolved = dict(source)
    kind = company_accounting_type(company)
    if company.get('forma_opodatkowania') and not kind:
        raise ValueError('Nieobsługiwana forma opodatkowania firmy w Biuro.')
    kind = kind or source.get('accounting_type')
    if not kind:
        raise ValueError('Uzupełnij formę opodatkowania firmy w Biuro.')
    resolved['accounting_type'] = kind
    if not source.get('count_query'):
        if kind not in ('KPIR', 'KH', 'RYCZALT'):
            raise ValueError('Brak gotowego zapytania dla rodzaju księgowości.')
        resolved['count_query'] = str(Path(__file__).resolve().parents[3] / 'docs' / 'enova' / 'queries' / (kind.lower() + '.sql'))
    elif source.get('accounting_type') and source['accounting_type'] != kind:
        raise ValueError('Typ starego count_query jest niezgodny z formą opodatkowania w Biuro. Usuń stare accounting_type/count_query lub popraw mapowanie.')
    return resolved


def apply_counts(conn, company, source, counts, *, bank_divisor=3):
    changes = []
    for count in counts:
        previous = conn.execute('SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s AND miesiac=%s FOR UPDATE',
                                (company['id'], count.month)).fetchone()
        key = f"enova_snapshot_{company['id']}_{count.month}"
        old_snapshot = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (key,)).fetchone()
        old_total = json.loads(old_snapshot[0]).get('total') if old_snapshot else None
        manual_key = f"manual_document_count_{company['id']}_{count.month}"
        manual = conn.execute('SELECT 1 FROM app_settings WHERE klucz=%s', (manual_key,)).fetchone()
        provenance = conn.execute('SELECT source_name FROM monthly_document_imports WHERE firma_id=%s AND miesiac=%s',
                                  (company['id'], count.month)).fetchone()
        current = previous[0] if previous else None
        # Legacy nonzero values and other imports belong to the user. Imported values
        # remain refreshable only while they still match our last successful write.
        protected = bool(manual or (provenance and not provenance[0].startswith('Enova: ')) or
                         (old_snapshot and current != old_total) or
                         (not old_snapshot and current not in (None, 0)))
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
                    'by_type': count.by_type, 'total': count.total, 'bank_divisor': bank_divisor,
                    'database': source['database'], 'accounting_type': source['accounting_type'],
                    'imported_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                     'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                     (f"enova_snapshot_{company['id']}_{count.month}", json.dumps(snapshot)))
        changes.append({'month': count.month, 'before': previous[0] if previous else None,
                        'after': current if protected else count.total, 'manual_preserved': protected, 'documents': count.documents, 'bank_transactions': count.bank_transactions})
    details = {'company_id': company['id'], 'database': source['database'],
               'accounting_type': source['accounting_type'], 'bank_divisor': bank_divisor, 'changes': changes}
    conn.execute("INSERT INTO import_history(import_type,source_path,updated_count,completed_at,status,details) "
                 "VALUES ('enova_document_counts',%s,%s,to_char(now(),'YYYY-MM-DD HH24:MI:SS'),'completed',%s)",
                 (source['database'], sum(not change['manual_preserved'] for change in changes), json.dumps(details, ensure_ascii=False)))
    return changes


def _daily_key(company_id, month):
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


def run_import(config, *, apply=False, today=None, fetch=fetch_counts):
    if apply and not config.get('enabled', False):
        raise ValueError('Import wyłączony. Najpierw sprawdź podgląd, następnie ustaw enabled=true.')
    if not config.get('sources'):
        raise ValueError('Połączenie Enovy można zapisać bez baz firm. Przed importem znajdź bazy po NIP lub przypisz je ręcznie i zapisz ustawienia.')
    run_day = today or datetime.now(ZoneInfo('Europe/Warsaw')).date()
    months = requested_months(run_day, config.get('months_back', 3))
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
            try:
                with conn.transaction():
                    company = find_company(conn, normalize_nip(source['nip']))
                    source = resolve_source(source, company)
                    divisor = effective_divisor(rules, company['id']) if source['accounting_type'] == 'KH' else 1
                    signature = {'day': run_day.isoformat(), 'database': source['database'], 'bank_divisor': divisor,
                                 'accounting_type': source['accounting_type'], 'server': config.get('server')}
                    jpk_result = None
                    if source.get('jpk_query'):
                        from app.integrations.enova.jpk import import_jpk
                        jpk_result = import_jpk(conn, config, source, company['id'], months, run_day, apply=apply)
                    eligible = _eligible_months(conn, company['id'], monthly_months, signature, apply=apply)
                    counts = []
                    # Query each eligible month separately: do not query uncompleted gaps.
                    for month in eligible:
                        # A previous month's remote read may have taken time; recheck before each contact.
                        if not conn.execute("SELECT 1 FROM statusy_miesieczne WHERE firma_id=%s "
                                            "AND miesiac=%s AND etap='Wysłane wyniki'",
                                            (company['id'], month)).fetchone():
                            continue
                        counts.extend(validate_rows(fetch(config, source, [month]), source, [month], divisor))
                    changes = []
                    if apply:
                        ready = []
                        for count in counts:
                            # Recheck after SQL Server reads and keep the row locked through the write.
                            status = conn.execute("SELECT etap FROM statusy_miesieczne WHERE firma_id=%s "
                                                  "AND miesiac=%s AND etap='Wysłane wyniki' FOR UPDATE",
                                                  (company['id'], count.month)).fetchone()
                            if status:
                                ready.append(count)
                        counts = ready
                        if counts:
                            changes = apply_counts(conn, company, source, counts, bank_divisor=divisor)
                            for count in counts:
                                conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                                             'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                                             (_daily_key(company['id'], count.month), json.dumps(signature)))
                    results.append({'database': source['database'], 'company_id': company['id'],
                                    'mode': ('applied' if apply else 'preview') if counts else 'skipped',
                                    'skipped_months': [month for month in months if month not in {c.month for c in counts}],
                                    'bank_divisor': divisor, 'jpk': jpk_result,
                                    'manual_preserved': [change['month'] for change in changes if change['manual_preserved']],
                                    'counts': {count.month: count.total for count in counts},
                                    'components': {count.month: {'documents': count.documents,
                                                               'bank_transactions': count.bank_transactions}
                                                   for count in counts}, 'ok': True})
            except Exception as exc:
                error = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
                results.append({'database': source['database'], 'ok': False, 'error': error})
                if apply:
                    conn.execute("INSERT INTO import_history(import_type,source_path,status,details) "
                                 "VALUES ('enova_document_counts',%s,'failed',%s)",
                                 (source['database'], json.dumps({'error': error}, ensure_ascii=False)))
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
