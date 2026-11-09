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
from app.utils.months import month_bounds, previous_month


@dataclass(frozen=True)
class MonthlyCount:
    month: str
    documents: int
    bank_transactions: int
    total: int


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


def calculate_total(kind, documents, bank_transactions):
    documents, bank_transactions = whole_count(documents), whole_count(bank_transactions)
    if kind not in {'KPIR', 'RYCZALT', 'KH'}:
        raise ValueError('Typ księgowości: KPIR, RYCZALT lub KH.')
    if kind != 'KH' and bank_transactions:
        raise ValueError('Pozycje bankowe są uwzględniane wyłącznie dla KH.')
    total = Decimal(documents)
    if kind == 'KH':
        total += Decimal(bank_transactions) / 3
    return whole_count(total.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def requested_months(today, months_back):
    if type(months_back) is not int or not 1 <= months_back <= 24:
        raise ValueError('months_back musi wynosić od 1 do 24.')
    months = [today.strftime('%Y-%m')]
    for _ in range(months_back - 1):
        months.append(previous_month(months[-1]))
    return sorted(months)


def validate_rows(rows, source, months):
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
        found[month] = MonthlyCount(month, documents, bank,
                                   calculate_total(source['accounting_type'], documents, bank))
    if set(found) != set(months):
        raise ValueError('Zapytanie nie zwróciło wszystkich miesięcy. Brak wiersza nie oznacza zera.')
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
        calculate_total(source['accounting_type'], 0, 0)
        if not source.get('count_query'):
            raise ValueError('Każde mapowanie wymaga pliku count_query.')
    return config


def _odbc_quote(value):
    return '{' + str(value).replace('}', '}}') + '}'


def fetch_counts(config, source, months):
    """SQL Server credentials must be SELECT-only; readonly is also requested from ODBC."""
    try:
        import pyodbc
    except ImportError:
        raise ValueError('Zainstaluj requirements-enova.txt oraz Microsoft ODBC Driver 18 for SQL Server.') from None
    user = os.environ.get('ENOVA_SQL_USER')
    password = os.environ.get('ENOVA_SQL_PASSWORD')
    if not user or not password:
        raise ValueError('Ustaw ENOVA_SQL_USER i ENOVA_SQL_PASSWORD dla konta zadania Windows.')
    query_path = Path(source['count_query'])
    if not query_path.is_absolute():
        query_path = config['_directory'] / query_path
    query = query_path.read_text(encoding='utf-8-sig')
    if not query.strip():
        raise ValueError('Plik zapytania jest pusty.')
    connection_string = ';'.join(f'{key}={_odbc_quote(value)}' for key, value in {
        'DRIVER': config.get('driver', 'ODBC Driver 18 for SQL Server'),
        'SERVER': config['server'], 'DATABASE': source['database'], 'UID': user, 'PWD': password,
        'Encrypt': 'yes', 'TrustServerCertificate': 'no', 'APP': 'Biuro document counts',
    }.items())
    connection = pyodbc.connect(connection_string, timeout=15, attrs_before={101: 1})
    try:
        cursor = connection.cursor()
        cursor.timeout = 120
        # Exactly two positional date parameters: inclusive start, exclusive end.
        cursor.execute(query, date.fromisoformat(month_bounds(months[0])[0]),
                       date.fromisoformat(month_bounds(months[-1])[1]))
        columns = [column[0].lower() for column in cursor.description or []]
        required = {'nip', 'miesiac', 'dokumenty', 'pozycje_bankowe'}
        if set(columns) != required or len(columns) != len(required):
            raise ValueError('Zapytanie musi zwrócić: nip, miesiac, dokumenty, pozycje_bankowe.')
        rows = cursor.fetchmany(len(months) + 1)
        if len(rows) > len(months):
            raise ValueError('Zapytanie zwróciło za wiele wierszy (wymagany jeden na miesiąc).')
        return [dict(zip(columns, row)) for row in rows]
    finally:
        connection.close()


def find_company(conn, nip):
    rows = conn.execute("SELECT id, nazwa FROM firmy WHERE regexp_replace(upper(nip), '[[:space:]-]', '', 'g') "
                        "IN (%s, %s)", (nip, 'PL' + nip)).fetchall()
    if len(rows) != 1:
        raise ValueError('NIP musi wskazywać dokładnie jedną firmę w Biuro.')
    return dict(rows[0])


def apply_counts(conn, company, source, counts):
    changes = []
    for count in counts:
        previous = conn.execute('SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s AND miesiac=%s',
                                (company['id'], count.month)).fetchone()
        conn.execute('INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok) VALUES (%s,%s,%s) '
                     'ON CONFLICT(firma_id,miesiac) DO UPDATE SET ilosc_dok=EXCLUDED.ilosc_dok',
                     (company['id'], count.month, count.total))
        conn.execute('INSERT INTO monthly_document_imports(firma_id,miesiac,source_name) VALUES (%s,%s,%s) '
                     'ON CONFLICT(firma_id,miesiac) DO UPDATE SET source_name=EXCLUDED.source_name, '
                     'imported_by=NULL, imported_at=now()',
                     (company['id'], count.month, 'Enova: ' + source['database']))
        changes.append({'month': count.month, 'before': previous[0] if previous else None,
                        'after': count.total, 'documents': count.documents, 'bank_transactions': count.bank_transactions})
    details = {'company_id': company['id'], 'database': source['database'],
               'accounting_type': source['accounting_type'], 'changes': changes}
    conn.execute("INSERT INTO import_history(import_type,source_path,updated_count,completed_at,status,details) "
                 "VALUES ('enova_document_counts',%s,%s,to_char(now(),'YYYY-MM-DD HH24:MI:SS'),'completed',%s)",
                 (source['database'], len(counts), json.dumps(details, ensure_ascii=False)))
    return changes


def run_import(config, *, apply=False, today=None, fetch=fetch_counts):
    if apply and not config.get('enabled', False):
        raise ValueError('Import wyłączony. Najpierw sprawdź podgląd, następnie ustaw enabled=true.')
    months = requested_months(today or datetime.now(ZoneInfo('Europe/Warsaw')).date(), config.get('months_back', 3))
    results = []
    with get_conn() as conn:
        if apply and not conn.execute('SELECT pg_try_advisory_xact_lock(748209)').fetchone()[0]:
            raise ValueError('Inny import Enova jest już uruchomiony.')
        for source in config['sources']:
            try:
                # Per-company savepoint: failed sources preserve their existing counts.
                with conn.transaction():
                    company = find_company(conn, normalize_nip(source['nip']))
                    counts = validate_rows(fetch(config, source, months), source, months)
                    if apply:
                        apply_counts(conn, company, source, counts)
                    results.append({'database': source['database'], 'company_id': company['id'],
                                    'mode': 'applied' if apply else 'preview',
                                    'counts': {count.month: count.total for count in counts},
                                    'components': {count.month: {'documents': count.documents,
                                                               'bank_transactions': count.bank_transactions}
                                                   for count in counts}, 'ok': True})
            except Exception as exc:
                # Driver errors may contain connection details. Do not log credentials.
                error = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
                results.append({'database': source['database'], 'ok': False, 'error': error})
                if apply:
                    conn.execute("INSERT INTO import_history(import_type,source_path,status,details) "
                                 "VALUES ('enova_document_counts',%s,'failed',%s)",
                                 (source['database'], json.dumps({'error': error}, ensure_ascii=False)))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--apply', action='store_true', help='Zapisz wyniki; domyślnie tylko podgląd.')
    args = parser.parse_args(argv)
    try:
        results = run_import(load_config(args.config), apply=args.apply)
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}, ensure_ascii=True))
        return 1
    for result in results:
        print(json.dumps(result, ensure_ascii=True))
    return int(any(not result['ok'] for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
