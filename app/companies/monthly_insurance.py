"""Display document codes and the owner's recorded insurance periods."""
from collections import defaultdict
from datetime import date, timedelta

from app.integrations.enova.document_counts import normalize_nip
from app.utils.months import month_bounds

TITLES = {
    '0510': 'ZUS', '0512': 'ZUS', '0540': 'Ulga na start',
    '0570': 'Preferencyjny ZUS', '0572': 'Preferencyjny ZUS',
    '0590': 'Mały ZUS Plus', '0592': 'Mały ZUS Plus',
}
PREFERENTIAL_MONTHS = {'Ulga na start': 6, 'Preferencyjny ZUS': 24}
MANUAL_ENDS = {'Ulga na start': 'data_koniec_zus_start', 'Preferencyjny ZUS': 'data_koniec_zus_pref',
               'Mały ZUS Plus': 'data_koniec_maly_zus'}


def _shift_month(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(year, month + 1, 1)


def _months(start, end):
    day = start.replace(day=1)
    while day <= end:
        yield day
        day = _shift_month(day, 1)


def estimate_end(title, periods, as_of):
    if title in PREFERENTIAL_MONTHS:
        intervals = sorted((start, min(end - timedelta(days=1), as_of) if end else as_of)
                           for start, end, kind in periods if kind == title and start <= as_of)
        if not intervals:
            return None
        first, last = intervals[0]
        for start, end in intervals[1:]:
            if start > last + timedelta(days=1):
                return None  # Re-registration alone cannot identify the start of the statutory clock.
            last = max(last, end)
        length = PREFERENTIAL_MONTHS[title]
        return _shift_month(first, length + (first.day != 1)) - timedelta(days=1)
    if title != 'Mały ZUS Plus' or as_of.year < 2026:
        return None
    plus, activity = set(), set()
    for start, end, kind in periods:
        if not kind:
            continue
        last = min(end - timedelta(days=1), as_of) if end else as_of
        first = max(start, date(2026, 1, 1))
        if first > last:
            continue
        months = set(_months(first, last))
        activity.update(months)
        if kind == title:
            plus.update(months)
    if not plus:
        return None
    months = sorted(month for month in activity if month >= min(plus))
    cycle = months[(len(months) - 1) // 60 * 60:]
    used = sorted(plus.intersection(cycle))
    if len(used) >= 36:
        return _shift_month(used[35], 1) - timedelta(days=1)
    remaining = min(36 - len(used), 60 - len(cycle))
    return _shift_month(as_of, remaining + 1) - timedelta(days=1)


def _registry_dates(registry, month):
    month_start, next_month = map(date.fromisoformat, month_bounds(month))
    imported = date.fromisoformat(registry['imported_at'][:10])
    as_of = min(next_month - timedelta(days=1), imported)
    return month_start, imported, as_of


def summarize_periods(registry, month, company):
    month_start, imported, as_of = _registry_dates(registry, month)
    if as_of < month_start:
        return set(), [], []
    try:
        company_nip = normalize_nip(company.get('nip'))
    except ValueError:
        company_nip = None
    owners = defaultdict(list)
    for row in registry['periods']:
        start = date.fromisoformat(row['from'])
        end = date.fromisoformat(row['until']) if row['until'] else None
        owners[row['nip']].append((row['code'], start, end))
    codes, notes, deadlines = set(), [], []
    for nip, rows in sorted(owners.items()):
        active = [(code, start, end) for code, start, end in rows if start <= as_of and (end is None or as_of < end)]
        titles = {TITLES.get(code[:4]) for code, _, _ in active}
        for code, start, end in sorted(active, key=lambda row: (row[0], row[1], row[2] or date.max)):
            codes.add(code)
            term = f' do {(end - timedelta(days=1)).isoformat()}' if end else ''
            notes.append(f'NIP {nip}: {code} · {TITLES.get(code[:4], "Tytuł ubezpieczenia")} · od {start.isoformat()}{term}')
        if len(titles) != 1 or titles.intersection((None, 'ZUS')):
            continue
        title, = titles
        registered_end = None
        if all(end for _, _, end in active):
            registered_end = max(end for _, _, end in active) - timedelta(days=1)
        confirmed = company.get(MANUAL_ENDS.get(title)) if nip == company_nip else None
        if confirmed:
            deadline = f'{confirmed} · kartoteka'
            if title == 'Mały ZUS Plus':
                estimate = estimate_end(title, [(start, end, TITLES.get(code[:4])) for code, start, end in rows], as_of)
                if estimate and estimate.isoformat() != confirmed:
                    notes.append(f'NIP {nip}: {title} do {estimate.isoformat()} · prognoza')
        else:
            estimate = estimate_end(title, [(start, end, TITLES.get(code[:4])) for code, start, end in rows], as_of)
            if registered_end and (not estimate or registered_end <= estimate):
                deadline = f'{registered_end.isoformat()} · rejestr'
            elif estimate:
                deadline = f'{estimate.isoformat()} · prognoza'
            else:
                notes.append(f'NIP {nip}: {title} · potrzebna pełna historia zgłoszeń')
                continue
        deadlines.append(deadline)
        notes.append(f'NIP {nip}: {title} do {deadline}')
    if codes:
        notes.append(f'Rejestr Płatnika z {imported.isoformat()}, stan na {as_of.isoformat()}.')
    return codes, notes, deadlines


def _latest_code(registry, entries, month):
    if registry:
        first, _, as_of = _registry_dates(registry, month)
        day = as_of.isoformat()
        latest = max(((row['from'], row['code']) for row in registry['periods']
                      if first <= as_of and row['from'] <= day and (not row['until'] or day < row['until'])),
                     default=None)
        if latest:
            start, code = latest
            return f'{code} · {start}'
    if entries:
        latest = max(entries, key=lambda entry: (entry.get('sent_at') or '', entry['insurance_code']))
        sent_at = latest.get('sent_at')
        return f"{latest['insurance_code']} · {sent_at[:10]}" if sent_at else latest['insurance_code']
    return ''


def read_codes(conn, company_ids, month, companies=None):
    from app.integrations.platnik.declarations import decoded_snapshot
    result = {}
    snapshots = defaultdict(dict)
    for row in conn.execute("""
        SELECT company_id,source,payload FROM company_checking_snapshots
        WHERE company_id=ANY(%s) AND (
            (source='platnik_declarations' AND period=%s) OR (source='platnik_insurance' AND period=''))
    """, (list(company_ids), month)):
        snapshots[row['company_id']][row['source']] = decoded_snapshot(row['payload'])
    for company_id, sources in snapshots.items():
        snapshot = sources.get('platnik_declarations', {})
        entries = [entry for entry in snapshot.get('declarations',[]) if entry.get('insurance_code')]
        registry = sources.get('platnik_insurance')
        notes, deadlines = [], []
        if registry:
            _, notes, deadlines = summarize_periods(registry, month, (companies or {}).get(company_id, {}))
        if snapshot:
            notes += list(dict.fromkeys(
                f"{entry.get('employee_name') or 'Płatnik'} — {entry['insurance_code']} — {entry['form']} "
                f"(NIP {entry.get('payer_nip') or 'brak'}, dokument {entry.get('source_id') or '—'})" for entry in entries))
            notes.append('Odczyt dokumentów: ' + str(snapshot.get('imported_at') or 'brak'))
        if notes or deadlines:
            result[company_id] = {'insurance_codes': _latest_code(registry, entries, month),
                                  'insurance_details': '\n'.join(notes), 'insurance_deadline': '; '.join(deadlines)}
    return result
