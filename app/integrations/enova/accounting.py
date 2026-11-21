"""Read-only checking data with a rolling per-company document history."""
from app.remote.boundary import endpoint as office_endpoint
import calendar
import datetime
import json
from app.db.dao_conn import get_conn
from app.security.authorization import allowed_tabs, assigned_company_ids
from app.companies.cennik_keys import company_accounting_type
from app.integrations.enova.checking import history_months


SUBSTITUTION_KEYS = {'accounting': 'ksiegowosc', 'payroll': 'kadry'}
SIMPLE_DOCUMENT_TYPES = ('SPU', 'ZR', 'DW')
SALES_SYMBOLS = {'SPU', 'SPR', 'SPD', 'SPT', 'SPUUE', 'SPTUE', 'SPUEX', 'SPTEX', 'VME'}
PURCHASE_SYMBOLS = {'ZR', 'ZP', 'ZT', 'ZUE', 'ZŚT', 'ZST'}


def simple_accounting(row):
    return company_accounting_type(row) in ('KPIR', 'RYCZALT')


def accounting_breakdown(row, counts):
    if counts is None or not simple_accounting(row) or counts.keys() <= set(SIMPLE_DOCUMENT_TYPES):
        return counts
    grouped = dict.fromkeys(SIMPLE_DOCUMENT_TYPES, 0)
    for symbol, count in counts.items():
        symbol = symbol.upper()
        key = 'SPU' if symbol in SALES_SYMBOLS else 'ZR' if symbol in PURCHASE_SYMBOLS else 'DW'
        grouped[key] += count
    return grouped


def _employee_names(ids):
    """Identyfikator pracownika -> „Imię Nazwisko” (jeden odczyt, bez N+1)."""
    ids = sorted({int(value) for value in ids if value})
    if not ids:
        return {}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, TRIM(COALESCE(imie,'') || ' ' || COALESCE(nazwisko,'')) AS nazwa "
            'FROM pracownicy WHERE id = ANY(%s)', (ids,)).fetchall()
    return {row['id']: (row['nazwa'] or '').strip() for row in rows}


def _month_last_day(month):
    """Ostatni dzień miesiąca „RRRR-MM”; None, gdy miesiąc jest nieznany."""
    try:
        year, number = (int(part) for part in str(month).split('-')[:2])
        return datetime.date(year, number, calendar.monthrange(year, number)[1])
    except (TypeError, ValueError):
        return None


def attach_substitutions(rows, month=None):
    """Kto faktycznie prowadzi firmę w danym miesiącu: opiekun stały albo zastępca.

    Zastępstwo ma pierwszeństwo, bo opisuje rzeczywistą osobę pracującą nad firmą.
    Zapisujemy też, kogo zastępuje, żeby dało się to pokazać w tooltipie. ``month``
    (RRRR-MM) decyduje, czy zastępstwo już obowiązywało — liczymy od ostatniego dnia
    miesiąca, żeby marcowe zastępstwo nie zmieniało widoku stycznia.
    """
    from app.db.dao_monthly import list_active_substitutions
    substitutions = list_active_substitutions(_month_last_day(month))
    if not substitutions:
        # Klucze muszą istnieć także bez zastępstw: interfejs i JSON czytają je
        # zawsze, a brak klucza kończył się pokazaniem opiekuna stałego.
        for row in rows:
            row.setdefault('owner_original', {})
            for department in SUBSTITUTION_KEYS:
                row.setdefault(f'{department}_substitute', None)
        return rows
    names = _employee_names(
        [sub.get('zastepca_id') for sub in substitutions.values()] +
        [sub.get('zastepowany_id') for sub in substitutions.values()])
    for row in rows:
        original = {}
        for department, role in SUBSTITUTION_KEYS.items():
            field = 'kadrowa' if department == 'payroll' else 'ksiegowa'
            original[department] = row.get(field) or ''
            sub = substitutions.get((row['id'], role)) or {}
            substitute_name = (names.get(sub.get('zastepca_id')) or '').strip()
            if substitute_name:
                row[field] = substitute_name
            # Rola obecna zawsze (z pustym zastepca_id), żeby klucz nie znikał z JSON-a
            # i zachowanie między wersjami było przewidywalne.
            row[f'{department}_substitute'] = {
                'zastepca_id': sub.get('zastepca_id'),
                'zastepowany_id': sub.get('zastepowany_id'),
                'zastepowany': original[department] or (names.get(sub.get('zastepowany_id')) or '').strip(),
                'do_dnia': sub.get('do_dnia') or '',
            } if sub.get('zastepca_id') else None
        row['owner_original'] = original
    return rows


def require_access(actor_id):
    if 'Sprawdzanie' not in allowed_tabs(actor_id):
        raise PermissionError('Brak dostępu do sprawdzania.')


def decoded(value):
    return json.loads(value) if isinstance(value, str) and value else value or {}


@office_endpoint('app.integrations.enova.accounting.list_accounting')
def list_accounting(actor_id, month):
    require_access(actor_id)
    prior = history_months(month)
    ids = list(assigned_company_ids(actor_id))
    with get_conn() as conn:
        rows = conn.execute('''SELECT f.id,f.nazwa,f.nip,f.forma_opodatkowania,f.forma_dzialalnosci,f.status_vat,f.vat_czynny,s.ilosc_dok,
            a.payload::text AS snapshot,j.payload::text AS jpk,c.payload::text AS checking,
            h.payload::text AS hr,z.payload::text AS ezla_sync,
            pd.payload AS platnik_declarations,
            co.payload::text AS checking_obligations,
            trim(COALESCE(pk.imie,'') || ' ' || COALESCE(pk.nazwisko,'')) AS ksiegowa,
            trim(COALESCE(ph.imie,'') || ' ' || COALESCE(ph.nazwisko,'')) AS kadrowa
            FROM firmy f LEFT JOIN pracownicy pk ON pk.id=f.ksiegowa_id
            LEFT JOIN pracownicy ph ON ph.id=f.kadrowa_id
            LEFT JOIN statusy_miesieczne s ON s.firma_id=f.id AND s.miesiac=%s
            LEFT JOIN company_checking_snapshots a ON a.source='enova_snapshot' AND a.company_id=f.id AND a.period=%s
            LEFT JOIN company_checking_snapshots j ON j.source='enova_jpk' AND j.company_id=f.id AND j.period=%s
            LEFT JOIN company_checking_snapshots c ON c.source='enova_checking' AND c.company_id=f.id AND c.period=%s
            LEFT JOIN company_checking_snapshots h ON h.source='enova_hr' AND h.company_id=f.id AND h.period=''
            LEFT JOIN company_checking_snapshots z ON z.source='ezla_sync' AND z.company_id=f.id AND z.period=''
            LEFT JOIN company_checking_snapshots pd ON pd.source='platnik_declarations' AND pd.company_id=f.id AND pd.period=%s
            LEFT JOIN company_checking_snapshots co ON co.source='checking_obligations' AND co.company_id=f.id AND co.period=''
            WHERE f.id=ANY(%s) ORDER BY lower(f.nazwa)''', (month, month, month, month, month, ids)).fetchall()
        history = conn.execute('''SELECT company_id,period,payload-'accounting_details' AS payload
            FROM company_checking_snapshots
            WHERE source='enova_checking' AND company_id=ANY(%s) AND period BETWEEN %s AND %s
            ORDER BY period,company_id''', (ids, min(prior), max(prior))).fetchall()
    by_company = {}
    for record in history:
        by_company.setdefault(record['company_id'], []).append({**decoded(record['payload']), 'month': record['period']})
    result = []
    for record in rows:
        row = dict(record)
        row['checking_period'] = month
        from app.integrations.platnik.declarations import decoded_snapshot
        row['platnik_declarations'] = decoded_snapshot(row.get('platnik_declarations'))
        row['history'] = by_company.get(row['id'], [])
        result.append(row)
    return attach_substitutions(result, month)


def documents(row, department):
    report = decoded(row.get('checking'))
    if report:
        counts = report.get('documents', {}).get(department)
        if counts is not None:
            return accounting_breakdown(row, counts) if department == 'accounting' else counts
    # Legacy totals can be shown, but missing types must not be invented as zero.
    if department == 'accounting':
        snapshot = decoded(row.get('snapshot'))
        counts = snapshot.get('report_types')
        return accounting_breakdown(row, snapshot.get('by_type') if counts is None else counts)
    return None


def active_types(row, department):
    return {kind for report in row.get('history', [])
            for kind, count in (report.get('documents', {}).get(department) or {}).items() if count > 0}


def declaration_entries(row, department):
    from app.companies.checking_obligations import report_applies
    report = decoded(row.get('checking'))
    result = [{**entry,'imported_at':report.get('declarations_imported_at') or report.get('imported_at')}
              for entry in report.get('declarations') or [] if entry['department'] == department]
    if department == 'payroll':
        snapshot = decoded(row.get('platnik_declarations'))
        result += [{**entry, 'imported_at': snapshot.get('imported_at')}
                   for entry in snapshot.get('declarations') or []]
    if department == 'accounting' and not any(entry['form'] == 'JPK_VAT' for entry in result):
        legacy = decoded(row.get('jpk'))
        if legacy and report_applies(row, 'JPK_VAT', reported=bool(
                legacy.get('form_variant') or legacy.get('status') not in ('unknown', 'not_applicable', None))):
            result.append({'sent_at': None, 'upo_at': None, **legacy, 'form': 'JPK_VAT',
                           'employee_id': '', 'employee_name': '', 'source_system': 'enova'})
    result = [entry for entry in result if report_applies(row, entry['form'], reported=True)]
    owner = row.get('kadrowa' if department == 'payroll' else 'ksiegowa') or ''
    original = (row.get('owner_original') or {}).get(department) or owner
    substitute = row.get(f'{department}_substitute')
    for entry in result:
        entry['owner'] = owner
        entry['owner_original'] = original if substitute else ''
        entry['owner_is_substitute'] = bool(substitute)
    for entry in result:
        if entry.get('status') == 'accepted' and not entry.get('upo'):
            entry['upo_confirmed_at'] = entry.get('imported_at') or entry.get('upo_at')
            entry['upo_source'] = 'status Enova'
    return result


def declarations(row, department):
    groups = {}
    for entry in declaration_entries(row, department):
        groups.setdefault(entry['form'], []).append(entry)
    result = {}
    for form, entries in groups.items():
        if len(entries) == 1:
            result[form] = entries[0]
            result[form].setdefault('sent_at', result[form].get('sent_at'))
            result[form].setdefault('upo_at', result[form].get('upo_at'))
        else:
            accepted = sum(entry.get('status') == 'accepted' and bool(entry.get('upo')) for entry in entries)
            receipts = sum(bool(entry.get('upo')) for entry in entries)
            # Zbiorczo pokazujemy zakres dat: wysyłki i UPO mogą się różnić per pracownik.
            sent = sorted(str(entry['sent_at']) for entry in entries if entry.get('sent_at'))
            receipts_at = sorted(str(entry['upo_at']) for entry in entries if entry.get('upo_at'))
            result[form] = {'form': form, 'count': len(entries), 'entries': entries,
                            'status_text': f'Przyjęte: {accepted}/{len(entries)}', 'upo_count': receipts,
                            'sent_at': sent[0] if sent else None,
                            'sent_to': sent[-1] if sent else None,
                            'upo_at': receipts_at[0] if receipts_at else None,
                            'upo_to': receipts_at[-1] if receipts_at else None,
                            'upo_numbers': [entry['upo'] for entry in entries if entry.get('upo')]}
            if any(entry.get('status') == 'unknown' for entry in entries):
                # Dokumenty są odczytane, ale nic nie wiemy o wysyłce. Numer UPO
                # bywa podany bez statusu — wtedy mówimy tylko o statusie.
                result[form]['status_text'] = ('status wysyłki nieustalony' if receipts
                                               else 'status wysyłki/UPO nieustalony')
    return result
