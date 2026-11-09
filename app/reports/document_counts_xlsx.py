"""Import explicit monthly accounting document counts from annual XLSX matrices."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from app.db.dao_conn import get_conn
from app.reports.import_parse import fold

MONTHS = {name: index for index, name in enumerate((
    'styczen', 'luty', 'marzec', 'kwiecien', 'maj', 'czerwiec',
    'lipiec', 'sierpien', 'wrzesien', 'pazdziernik', 'listopad', 'grudzien'), 1)}


@dataclass(frozen=True)
class CountCell:
    nip: str
    month: str
    count: int
    sheet: str
    row: int


def month_header(value):
    if isinstance(value, (date, datetime)):
        return value.strftime('%Y-%m')
    text = fold(value)
    if re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', text):
        return text
    match = re.fullmatch(r'(' + '|'.join(MONTHS) + r')\s*([0-9]{4})\s*r?\.?', text)
    if match and 1900 <= int(match[2]) <= 2199:
        return f'{match[2]}-{MONTHS[match[1]]:02d}'
    return None


def nip_value(value):
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value():
            return None
        value = str(int(number))
    text = re.sub(r'[\s-]', '', str(value or '').upper()).removeprefix('PL')
    return text if re.fullmatch(r'[0-9]{10}', text) else None


def count_value(value):
    if value is None or str(value).strip() in ('', '-', '—', '–'):
        return None
    if isinstance(value, bool):
        raise ValueError('wartość logiczna zamiast liczby')
    try:
        number = Decimal(re.sub(r'\s', '', str(value)).replace(',', '.'))
    except InvalidOperation:
        raise ValueError('oczekiwano liczby całkowitej') from None
    if not number.is_finite() or number < 0 or number != number.to_integral_value() or number > 2147483647:
        raise ValueError('oczekiwano nieujemnej liczby całkowitej')
    return int(number)


def read_document_counts(path):
    """Ignore annual totals. Blank months are absent; explicit zero is retained."""
    import openpyxl
    data = openpyxl.load_workbook(path, read_only=True, data_only=True)
    formulas = None
    warnings, records = [], {}
    found_header = False
    try:
        formulas = openpyxl.load_workbook(path, read_only=True, data_only=False)
        for sheet in data.worksheets:
            nip_column, months, headers = None, {}, False
            raw_rows = formulas[sheet.title].iter_rows(values_only=True)
            for line, (values, raw) in enumerate(zip(sheet.iter_rows(values_only=True), raw_rows), 1):
                labels = [fold(value) for value in values]
                if 'nip' in labels or 'nip firmy' in labels:
                    candidate_nip = labels.index('nip') if 'nip' in labels else labels.index('nip firmy')
                    candidate_months = {i: month_header(value) for i, value in enumerate(values) if month_header(value)}
                    if candidate_months:
                        if len(set(candidate_months.values())) != len(candidate_months):
                            raise ValueError(f'{sheet.title}, wiersz {line}: powtórzona kolumna miesiąca.')
                        nip_column, months, headers, found_header = candidate_nip, candidate_months, True, True
                        continue
                if not headers or not any(value is not None and str(value).strip() for value in values):
                    continue
                nip = nip_value(values[nip_column] if nip_column < len(values) else None)
                if not nip:
                    # Ignore footer totals only when no valid company NIP is present.
                    if any(label in ('razem', 'suma', 'ogolem', 'lacznie') for label in labels[:max(nip_column, 1)]):
                        continue
                    warnings.append(f'{sheet.title}, wiersz {line}: nieprawidłowy NIP; pominięto firmę.')
                    continue
                for column, month in months.items():
                    value = values[column] if column < len(values) else None
                    if value is None and column < len(raw) and isinstance(raw[column], str) and raw[column].startswith('='):
                        warnings.append(f'{sheet.title}, wiersz {line}, {month}: formuła bez zapisanego wyniku; pominięto miesiąc.')
                        continue
                    try:
                        count = count_value(value)
                    except ValueError as exc:
                        raise ValueError(f'{sheet.title}, wiersz {line}, {month}: {exc}.') from None
                    if count is None:
                        continue
                    key = nip, month
                    if key in records and records[key].count != count:
                        raise ValueError(f'{sheet.title}, wiersz {line}: sprzeczne liczby dla NIP {nip}, {month}.')
                    records[key] = CountCell(nip, month, count, sheet.title, line)
        if not found_header:
            raise ValueError('Nie znaleziono nagłówka NIP i miesięcy, np. „styczeń 2026r.”.')
        return list(records.values()), warnings
    finally:
        data.close()
        if formulas is not None:
            formulas.close()


def import_document_counts_file(path, *, actor_id, overwrite=False):
    from app.security.authorization import can, is_administrator
    if not actor_id or not (is_administrator(actor_id) or can(actor_id, 'can_view_finances')):
        raise PermissionError('Brak uprawnień do importu liczby dokumentów.')
    records, warnings = read_document_counts(path)
    result = {'kind': 'document_counts', 'updated': 0, 'unchanged': 0, 'skipped': 0,
              'unmatched': 0, 'warnings': warnings}
    source = Path(path).name
    with get_conn() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(748210)')
        by_nip = {}
        for row in conn.execute('SELECT id,nip FROM firmy').fetchall():
            nip = nip_value(row['nip'])
            if nip:
                by_nip.setdefault(nip, []).append(row['id'])
        missing = set()
        for cell in records:
            firms = by_nip.get(cell.nip, [])
            if len(firms) != 1:
                result['unmatched'] += 1
                if cell.nip not in missing:
                    warnings.append(f'NIP {cell.nip}: ' + ('kilka firm w bazie; pominięto.' if firms else 'brak firmy w bazie; pominięto.'))
                    missing.add(cell.nip)
                continue
            fid = firms[0]
            previous = conn.execute('SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s AND miesiac=%s FOR UPDATE',
                                    (fid, cell.month)).fetchone()
            if previous and previous[0] and previous[0] != cell.count and not overwrite:
                result['skipped'] += 1
                continue
            conn.execute('INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok) VALUES (%s,%s,%s) '
                         'ON CONFLICT(firma_id,miesiac) DO UPDATE SET ilosc_dok=EXCLUDED.ilosc_dok',
                         (fid, cell.month, cell.count))
            conn.execute('INSERT INTO monthly_document_imports(firma_id,miesiac,source_name,imported_by) '
                         'VALUES (%s,%s,%s,%s) ON CONFLICT(firma_id,miesiac) DO UPDATE '
                         'SET source_name=EXCLUDED.source_name,imported_by=EXCLUDED.imported_by,imported_at=now()',
                         (fid, cell.month, source, actor_id))
            result['unchanged' if previous and previous[0] == cell.count else 'updated'] += 1
        conn.execute("INSERT INTO import_history(import_type,source_path,updated_count,warning_count,user_id,completed_at,status,details) "
                     "VALUES ('monthly_document_counts_xlsx',%s,%s,%s,%s,to_char(now(),'YYYY-MM-DD HH24:MI:SS'),'completed',%s)",
                     (source, result['updated'], len(warnings), actor_id, json.dumps(result, ensure_ascii=False)))
    return result


def imported_document_counts(conn, company_ids, months=None):
    if not company_ids:
        return {}
    rows = conn.execute('SELECT i.firma_id,i.miesiac,s.ilosc_dok FROM monthly_document_imports i '
                        'JOIN statusy_miesieczne s ON s.firma_id=i.firma_id AND s.miesiac=i.miesiac '
                        'WHERE i.firma_id=ANY(%s) AND (%s::text[] IS NULL OR i.miesiac=ANY(%s))',
                        (list(company_ids), list(months) if months is not None else None,
                         list(months) if months is not None else None)).fetchall()
    return {(row['firma_id'], row['miesiac']): int(row['ilosc_dok'] or 0) for row in rows}
