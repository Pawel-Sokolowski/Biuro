"""Monthly contribution amounts from DRA totals and matched RCA positions."""

from collections import defaultdict

from app.utils.money import money

SOCIAL = ('pension', 'disability', 'sickness', 'accident')
AMOUNTS = ('health', *SOCIAL, 'labour_fund', 'guaranteed_benefits', 'bridging_pensions')
COUNT_FIELD = 'contribution_insured_count'
AMOUNT_FIELDS = {'contribution_' + key for key in AMOUNTS} | {COUNT_FIELD}

# Płatnik, Struktury danych osobowych §4.3. RCA uses the funding columns in
# the newer III.B block, rather than the archived single-payer columns.
DRA_COLUMNS = {
    'health': 'VII_1_KWSKDPRZPL',
    'pension': 'IV_1_SSKLUBEM',
    'disability': 'IV_2_SSKLUBR',
    'sickness': 'IV_19_SSKLUBCH',
    'accident': 'IV_20_SSKLUBWYP',
    'labour_fund': 'VIII_1_KWNALSKLFP',
    'guaranteed_benefits': 'VIII_2_KWNALSKFGSP',
    'bridging_pensions': 'IX_3_SSKLADFEP',
}
RCA_COLUMNS = {
    'health': ('III_C_2_KWSKLZP_R', 'III_C_4_KWSKLZU_R'),
    'pension': ('III_B_7_KWSKLEU_R_N', 'III_B_11_KWSKLEP_R_N', 'III_B_15_KWSKLEBP_R',
                'III_B_19_KWSKLEPFRON_R', 'III_B_23_KWSKLEFK_R'),
    'disability': ('III_B_8_KWSKLRU_R_N', 'III_B_12_KWSKLRP_R_N', 'III_B_16_KWSKLRBP_R',
                   'III_B_20_KWSKLRPFRON_R', 'III_B_24_KWSKLRFK_R'),
    'sickness': ('III_B_9_KWSKLCHU_R_N', 'III_B_13_KWSKLCHP_R', 'III_B_17_KWSKLCHBP_R',
                 'III_B_21_KWSKLCHPFRON_R', 'III_B_25_KWSKLCHFK_R'),
    'accident': ('III_B_10_KWSKLWYU_R', 'III_B_14_KWSKLWYP_R', 'III_B_18_KWSKLWYBP_R',
                 'III_B_22_KWSKLWYPFRON_R', 'III_B_26_KWSKLWYFK_R'),
}


def amounts_sql(table, alias, available):
    """Return aligned UNION columns; unknown source fields stay NULL."""
    projections = []
    for key in AMOUNTS:
        expr = 'CAST(NULL AS decimal(19,2))'
        if table == 'ZUSDRA' and DRA_COLUMNS[key] in available:
            expr = f'TRY_CONVERT(decimal(19,2),{alias}.{DRA_COLUMNS[key]})'
        elif table == 'UBEZP_ZUSRCA' and key in RCA_COLUMNS and set(RCA_COLUMNS[key]) <= available:
            columns = [f'{alias}.{name}' for name in RCA_COLUMNS[key]]
            values = [f'TRY_CONVERT(decimal(19,2),{column})' for column in columns]
            present = ' OR '.join(f'{value} IS NOT NULL' for value in values)
            invalid = ' OR '.join(f'({column} IS NOT NULL AND {value} IS NULL)'
                                  for column, value in zip(columns, values))
            total = ' + '.join(f'COALESCE({value},0)' for value in values)
            expr = f'CASE WHEN ({present}) AND NOT ({invalid}) THEN {total} END'
        projections.append(f'{expr} AS contribution_{key}')
    count = (f'TRY_CONVERT(int,{alias}.III_1_LUBEZP)' if table == 'ZUSDRA' and 'III_1_LUBEZP' in available
             else 'CAST(NULL AS int)')
    return ', '.join([*projections, f'{count} AS {COUNT_FIELD}'])


def capture_amounts(entry, row, owner_id):
    if entry['form'] == 'ZUS DRA':
        entry[COUNT_FIELD] = str(row.get(COUNT_FIELD) or '') == '1'
    elif entry['form'] != 'ZUS RCA' or owner_id is None or entry['employee_id'] != str(owner_id):
        return
    entry['contribution_amounts'] = {
        key: money(value) for key in AMOUNTS
        if (value := row.get('contribution_' + key)) not in (None, '')
    }


def _latest(entries, *, by_title=False):
    """Select one version per declaration series and, for RCA, insurance title."""
    if not entries:
        return []
    codes = {entry.get('insurance_code') or '' for entry in entries}
    if by_title and len(codes) > 1 and ({len(code) for code in codes} != {6}):
        return None
    groups = defaultdict(list)
    for entry in entries:
        revision = entry['report_id']
        if not revision.isascii() or not revision.isdecimal() or len(revision) > 2:
            return None
        number = int(revision)
        if not 1 <= number <= 49:
            return None
        code = (entry.get('insurance_code') or '') if by_title else ''
        groups[(number >= 40, code)].append((number, entry))
    selected = []
    for group in groups.values():
        newest = max(number for number, _ in group)
        matching = [entry for number, entry in group if number == newest]
        if len(matching) != 1:
            return None
        selected.extend(matching)
    return selected


def _summary(entries):
    if entries is None:
        return {'problem': 'Niejednoznaczny identyfikator deklaracji lub korekty.'}
    if not entries:
        return {}
    values = {}
    for key in AMOUNTS:
        amounts = [entry['contribution_amounts'].get(key) for entry in entries]
        if None not in amounts:
            values[key] = sum(amounts)
    if all(key in values for key in SOCIAL):
        values['social'] = sum(values[key] for key in SOCIAL)
    return {
        'source': entries[0]['form'],
        'reports': sorted({entry['report_id'] for entry in entries}, key=int),
        'status': ', '.join(sorted({
            entry['status_text'] if entry.get('status') in ('draft', 'sent', 'accepted', 'rejected') else 'Nieustalony'
            for entry in entries
        })),
        'amounts': {key: str(value) for key, value in values.items()},
    }


def summarize(entries):
    dra = _latest([entry for entry in entries if entry['form'] == 'ZUS DRA'])
    rca = _latest([entry for entry in entries if entry['form'] == 'ZUS RCA'
                   and 'contribution_amounts' in entry], by_title=True)
    if rca == [] and dra is not None and all(
            entry[COUNT_FIELD] and (entry.get('insurance_code') or '').startswith('05') for entry in dra):
        rca = dra
    return {'company': _summary(dra), 'owner': _summary(rca)}
