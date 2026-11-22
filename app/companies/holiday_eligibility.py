"""Individual, audited ZUS holiday checks for a reviewed mailing audience.

Codes classify the insurance title, not the full entitlement. Unknown evidence is
kept separate from confirmed conditions; RWS A always overrides an unused claim.
"""
from app.remote.boundary import endpoint as office_endpoint
import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn
from app.security.authorization import allowed_tabs, assigned_company_ids
from app.utils.crypto import encrypt_password, decrypt_password
from app.utils.months import previous_month, month_bounds

BASE_CODES = frozenset(('0510', '0512', '0570', '0572', '0590', '0592'))
RELIEF_CODES = frozenset(('0514', '0574', '0594'))
ELIGIBILITY_LABELS = {'eligible': 'Tak', 'ineligible': 'Nie',
                      'review': 'Do sprawdzenia', 'used': 'Wykorzystano'}
CONDITIONS = {
    'registered': 'Wpis CEIDG lub status komornika',
    'contribution_payer': 'Status płatnika składek w dniu złożenia wniosku',
    'active_in_relief_month': 'Prowadzenie działalności w miesiącu zwolnienia',
    'social': 'Ubezpieczenia emerytalne, rentowe i wypadkowe w miesiącu odniesienia',
    'income': 'Warunek przychodów za dwa właściwe lata',
    'no_former_employer': 'Spełniony warunek dotyczący byłego pracodawcy',
    'de_minimis': 'Dostępny limit pomocy de minimis na składki',
    'unused': 'Potwierdzono niewykorzystanie wakacji w roku ulgi',
}


class EligibilityConflict(ValueError):
    pass


def context(month):
    if not isinstance(month, str) or not re.fullmatch(r'(?:20\d\d|2100)-(0[1-9]|1[0-2])', month):
        raise ValueError('Wybierz miesiąc wakacji ZUS.')
    month_bounds(month)
    if not '2024-01' <= month <= '2100-12':
        raise ValueError('Nieprawidłowy rok wakacji ZUS.')
    application = previous_month(month)
    application_year = int(application[:4])
    return {'month': month, 'year': int(month[:4]), 'application_month': application,
            'reference_month': previous_month(application),
            'income_years': [application_year-2, application_year-1]}


def current_month():
    return datetime.now(ZoneInfo('Europe/Warsaw')).strftime('%Y-%m')


def default_relief_month(year, now_month=None):
    next_month = month_bounds(now_month or current_month())[1][:7]
    return max(f'{year}-01', min(next_month, f'{year}-12'))


def insurance_code(value):
    text = re.sub(r'[\s-]', '', str(value or ''))
    if text and not re.fullmatch(r'\d{4}(?:\d{2})?', text):
        raise ValueError('Kod ubezpieczenia: 4 lub 6 cyfr, np. 05 10 00.')
    return text


def _nip(value):
    from app.integrations.enova.document_counts import normalize_nip
    try:
        return normalize_nip(value)
    except ValueError:
        return ''


def _access(actor_id, company_id=None):
    tabs = allowed_tabs(actor_id)
    if 'E-mail' not in tabs and (company_id is None or 'Sprawdzanie' not in tabs):
        raise PermissionError('Brak dostępu do warunków wakacji ZUS.')
    if company_id is not None and (type(company_id) is not int or company_id not in assigned_company_ids(actor_id)):
        raise PermissionError('Brak dostępu do firmy.')


def _key(month, company_id):
    return f'zus_holiday_mailing_{month}_{company_id}'


def _stored(keys):
    with get_conn() as conn:
        return {row['klucz']: row['wartosc'] for row in conn.execute(
            'SELECT klucz,wartosc FROM app_settings WHERE klucz=ANY(%s)', (list(keys),)).fetchall()}


def _profiles(raw):
    return json.loads(decrypt_password(json.loads(raw)['profiles_enc'])) if raw else {}


def _source_rows(raw):
    snapshot = json.loads(raw) if raw else {}
    return json.loads(decrypt_password(snapshot['rows_enc'])) if snapshot.get('rows_enc') else []


def _evidence(raw, nip):
    return [row for row in _source_rows(raw) if nip and row.get('nip') == nip]


def rca_holidays(conn, company_ids, year):
    if not company_ids:
        return {}
    from app.integrations.platnik.declarations import decoded_snapshot

    evidence = {}
    for company_id, period, payload in conn.execute("""
        SELECT company_id, period, payload FROM company_checking_snapshots
        WHERE source='platnik_declarations' AND company_id=ANY(%s) AND period BETWEEN %s AND %s
        ORDER BY company_id, period
    """, (company_ids, max(f'{year}-01', '2024-12'), f'{year}-12')):
        for entry in decoded_snapshot(payload).get('declarations', ()):
            if entry.get('form') != 'ZUS RCA' or not entry.get('employee_id'):
                continue
            code = insurance_code(entry.get('insurance_code'))[:4]
            status = entry.get('status')
            if code not in RELIEF_CODES or status in ('draft', 'rejected'):
                continue
            sent = (status == 'sent' and bool(entry.get('sent_at'))) or (
                status == 'accepted' and bool(entry.get('upo')))
            key = (int(period[5:]), code, _nip(entry.get('payer_nip')))
            company_evidence = evidence.setdefault(company_id, {})
            company_evidence[key] = company_evidence.get(key, False) or sent
    return {company: [{'month': month, 'code': code, 'payer_nip': nip, 'sent': sent}
                      for (month, code, nip), sent in sorted(entries.items())]
            for company, entries in evidence.items()}


def _payer_rca(rca, payer_nip):
    return [item for item in rca if not item['payer_nip'] or item['payer_nip'] == payer_nip]


def _confirmed_rca(rca):
    return any(item['sent'] and item['payer_nip'] for item in rca)


def evidence_hash(rows):
    facts = sorted((str(r.get('source_id')), str(r.get('status_zrodlowy')), str(r.get('miesiac')),
                    str(r.get('kod_ulgi')), str(r.get('bazowy_kod_ubezpieczenia'))) for r in rows)
    return hashlib.sha256(json.dumps(facts, ensure_ascii=False).encode()).hexdigest()


def assess(profile, rows, period, payer_nip, *, now_month=None, known_code='', rca=()):
    """Pure classification: never infer missing declarations from a title code."""
    now = now_month or current_month()
    if any(row.get('status_zrodlowy') == 'A' for row in rows):
        return 'used', ['Aktualny wpis RWS w roku ulgi — nie przypominaj o niewykorzystanych wakacjach.']
    if _confirmed_rca(rca):
        return 'used', ['Potwierdzona wysyłka imiennego RCA z kodem wakacyjnym w wybranym roku.']
    if period['month'] < '2024-12' or period['application_month'] < now:
        return 'ineligible', ['Termin złożenia wniosku za wybrany miesiąc już minął.']
    if not payer_nip:
        return 'review', ['Uzupełnij indywidualny NIP płatnika.']
    if profile.get('payer_nip') != payer_nip or profile.get('month') != period['month']:
        profile = {}
    reasons = []
    try:
        code = insurance_code(profile.get('insurance_code') or known_code)
    except ValueError:
        code = ''
    if code and code[:4] not in BASE_CODES:
        reason = ('Podaj bazowy kod ubezpieczenia, nie kod wakacyjny.' if code[:4] in RELIEF_CODES else
                  'Kod ubezpieczenia nie odpowiada działalności objętej wakacjami składkowymi.')
        return 'ineligible', [reason]
    if not code:
        reasons.append('Brak potwierdzonego bazowego kodu ubezpieczenia.')
    rejected = ['Niespełniony warunek: '+label.lower()+'.' for field, label in CONDITIONS.items() if profile.get(field) is False]
    count = profile.get('insured_count')
    if rejected or (type(count) is int and not 1 <= count <= 10):
        return ('used' if profile.get('unused') is False else 'ineligible'), rejected + (
            ['Limit ubezpieczonych: od 1 do 10 osób łącznie w miesiącu odniesienia.'] if type(count) is int and not 1<=count<=10 else [])
    reasons.extend('Nie potwierdzono: '+label.lower()+'.' for field, label in CONDITIONS.items() if profile.get(field) is not True)
    if type(count) is not int:
        reasons.append('Brak liczby ubezpieczonych za cały miesiąc odniesienia.')
    if profile and profile.get('verified_month') != period['application_month']:
        reasons.append('Potwierdź warunki ponownie w miesiącu złożenia wniosku.')
    if period['application_month'] > now:
        reasons.append('Miesiąc wniosku jeszcze nie nadszedł; warunki miesięczne wymagają ponownej weryfikacji.')
    if any(row.get('status_zrodlowy') not in ('A', 'N') for row in rows):
        reasons.append('Nieznany status RWS — sprawdź odczyt Płatnika.')
    if rca:
        reasons.append('Sprawdź wysyłkę i płatnika raportu RCA z kodem wakacyjnym.')
    if profile and profile.get('evidence_hash') != evidence_hash(rows):
        reasons.append('Wpisy RWS zmieniły się od weryfikacji warunków.')
    return ('review', reasons) if reasons else ('eligible', ['Warunki potwierdzone przez biuro; brak aktualnego RWS w roku ulgi.'])


def _people(company, partners=None):
    from app.companies.identities import is_civil
    from app.db.dao_companies import get_partners
    if is_civil(company):
        if partners is None:
            partners = get_partners(company['id'])
        return [{'id':str(p['id']), 'name':' '.join(filter(None, (p.get('imie_pierwsze'),p.get('nazwisko')))) or 'Wspólnik',
                 'payer_nip':_nip(p.get('nip')), 'email':p.get('email') or ''} for p in partners]
    kind = str(company.get('forma_dzialalnosci') or '').strip().casefold()
    excluded = (kind.startswith(('spółka','spolka','sp.','sp z','spółdziel','fundac','stowarz'))
                or kind in ('s.j.', 'sp.k.', 's.k.a.', 's.a.', 's.j', 'sp.k', 's.k.a', 's.a'))
    return [{'id':'company', 'name':company.get('nazwa_skrocona') or company['nazwa'], 'entity_excluded':excluded,
             'payer_nip':_nip(company.get('nip')), 'email':company.get('adres_email') or ''}]


def _suggested_code(person, notes, rows):
    value = notes.get('kod_ubezpieczenia',{}).get('value') if person['id']=='company' else ''
    codes = {r.get('bazowy_kod_ubezpieczenia') for r in rows if r.get('bazowy_kod_ubezpieczenia')}
    return value or (next(iter(codes)) if len(codes)==1 else '')


def assess_person(person, profile, rows, period, notes, rca=(), *, now_month=None, known_code=None):
    if person.get('entity_excluded'):
        return 'ineligible', ['Wakacje dotyczą przedsiębiorcy CEIDG lub komornika, nie tej osoby prawnej.']
    code = _suggested_code(person, notes, rows) if known_code is None else known_code
    return assess(profile, rows, period, person['payer_nip'], now_month=now_month,
                  known_code=code, rca=_payer_rca(rca, person['payer_nip']))


def worksheet_assessments(companies, period, notes, annual_rca):
    from app.companies.identities import is_civil
    from app.db.dao_companies import get_partners_for_companies

    if not companies:
        return {}
    keys = {company['id']: _key(period['month'], company['id']) for company in companies}
    snapshot_key = f"platnik_holidays_{period['year']}"
    stored = _stored([snapshot_key, *keys.values()])
    rws_by_nip = {}
    for row in _source_rows(stored.get(snapshot_key)):
        if nip := _nip(row.get('nip')):
            rws_by_nip.setdefault(nip, []).append(row)
    partners = get_partners_for_companies([company['id'] for company in companies if is_civil(company)])
    now = current_month()
    result = {}
    for company in companies:
        company_id = company['id']
        profiles = _profiles(stored.get(keys[company_id]))
        rca = annual_rca.get(company_id, ())
        assessments = []
        for person in _people(company, partners.get(company_id, ())):
            nip = person['payer_nip']
            status, reasons = assess_person(person, profiles.get(person['id'], {}), rws_by_nip.get(nip, ()),
                                            period, notes.get(company_id, {}), rca, now_month=now)
            assessments.append({'name': person['name'], 'status': status, 'reasons': reasons,
                                'rca_sent': status == 'used' and _confirmed_rca(_payer_rca(rca, nip))})
        if not assessments:
            assessments.append({'name': company['nazwa'], 'status': 'review', 'rca_sent': False,
                                'reasons': ['Uzupełnij dane wspólników spółki cywilnej.']})
        result[company_id] = assessments
    return result


def worksheet_label(assessments):
    statuses = [person['status'] for person in assessments]
    if len(set(statuses)) == 1:
        if statuses[0] == 'used' and all(person['rca_sent'] for person in assessments):
            return 'Wykorzystano według RCA'
        return ELIGIBILITY_LABELS[statuses[0]]
    if 'eligible' in statuses:
        return f"Tak — {statuses.count('eligible')} z {len(statuses)} wspólników"
    if 'review' in statuses:
        return ELIGIBILITY_LABELS['review']
    return 'Nie — część wspólników wykorzystała ulgę'


def _companies(actor_id):
    from app.db.dao_companies import list_mailing_companies
    return list_mailing_companies(actor_id)


@office_endpoint('app.companies.holiday_eligibility.list_candidates')
def list_candidates(actor_id, month):
    _access(actor_id)
    period = context(month)
    companies = _companies(actor_id)
    snapshot_key = f"platnik_holidays_{period['year']}"
    stored = _stored([snapshot_key, *[_key(month,c['id']) for c in companies],
                      *[f"platnik_holiday_notes_{period['year']}_{c['id']}" for c in companies]])
    with get_conn() as conn:
        annual_rca = rca_holidays(conn, [company['id'] for company in companies], period['year'])
    by_nip = {}
    for row in _source_rows(stored.get(snapshot_key)):
        by_nip.setdefault(row.get('nip'),[]).append(row)
    result = []
    for company in companies:
        profiles = _profiles(stored.get(_key(month,company['id'])))
        from app.integrations.platnik.holiday_tracking import decode_notes
        notes = decode_notes(stored.get(f"platnik_holiday_notes_{period['year']}_{company['id']}"))
        people = []
        for person in _people(company):
            rows = by_nip.get(person['payer_nip'],[]) if person['payer_nip'] else []
            profile = profiles.get(person['id'], {})
            status, reasons = assess_person(person, profile, rows, period, notes,
                                            annual_rca.get(company['id'], ()))
            people.append({**person, 'status':status, 'reasons':reasons,
                           'insurance_code':profile.get('insurance_code','')})
        from app.email.bulk_mail import client_addresses
        addresses = {}
        for mode in ('eligible','review'):
            values = []
            for person in people:
                if person['status'] != mode:
                    continue
                try:
                    values.extend(client_addresses(person['email']))
                except ValueError:
                    continue
            addresses[mode] = ', '.join(dict.fromkeys(values))
        result.append({**company, 'holiday_people':people, 'holiday_addresses':addresses})
    return {'context':period, 'companies':result}


@office_endpoint('app.companies.holiday_eligibility.read_conditions')
def read_conditions(actor_id, company_id, month):
    _access(actor_id, company_id)
    period = context(month)
    company = next((c for c in _companies(actor_id) if c['id']==company_id), None)
    if company is None:
        raise PermissionError('Brak dostępu do firmy.')
    snapshot_key = f"platnik_holidays_{period['year']}"
    previous_key = f"platnik_holidays_{period['year']-1}"
    stored = _stored([snapshot_key,previous_key,_key(month,company_id), f"platnik_holiday_notes_{period['year']}_{company_id}"])
    profiles = _profiles(stored.get(_key(month,company_id)))
    from app.integrations.platnik.holiday_tracking import decode_notes
    notes = decode_notes(stored.get(f"platnik_holiday_notes_{period['year']}_{company_id}"))
    people = _people(company)
    current_rows, previous_rows = _source_rows(stored.get(snapshot_key)), _source_rows(stored.get(previous_key))
    with get_conn() as conn:
        annual_rca = rca_holidays(conn, [company_id], period['year']).get(company_id, ())
    for person in people:
        profile = profiles.get(person['id'], {})
        rows = [r for r in current_rows if person['payer_nip'] and r.get('nip')==person['payer_nip']]
        historical = [r for r in previous_rows if person['payer_nip'] and r.get('nip')==person['payer_nip']]
        person['values'] = profile if profile.get('payer_nip')==person['payer_nip'] else {}
        person['suggested_code'] = _suggested_code(person,notes,rows or historical)
        person['revision'] = profile.get('revision',0)
        person['status'],person['reasons'] = assess_person(
            person, profile, rows, period, notes, annual_rca, known_code=person['suggested_code'])
    return {'context':period,'people':people,'company_name':company['nazwa']}


@office_endpoint('app.companies.holiday_eligibility.save_conditions')
def save_conditions(actor_id, company_id, month, person_id, values, expected_revision):
    _access(actor_id, company_id)
    period = context(month)
    if not isinstance(values,dict) or set(values)-set(CONDITIONS)-{'insurance_code','insured_count'}:
        raise ValueError('Nieprawidłowe warunki wakacji ZUS.')
    if any(values.get(field) is not None and type(values[field]) is not bool for field in CONDITIONS):
        raise ValueError('Warunki wymagają odpowiedzi tak / nie / brak danych.')
    count = values.get('insured_count')
    if count is not None and (type(count) is not int or not 0<=count<=1000000):
        raise ValueError('Nieprawidłowa liczba ubezpieczonych.')
    if type(expected_revision) is not int or expected_revision<0:
        raise ValueError('Nieprawidłowa wersja warunków.')
    company = next((c for c in _companies(actor_id) if c['id']==company_id), None)
    person = next((p for p in _people(company) if p['id']==person_id), None) if company else None
    if not person or not person['payer_nip']:
        raise ValueError('Uzupełnij indywidualny NIP osoby w kartotece firmy / wspólnika.')
    code = insurance_code(values.get('insurance_code'))
    key = _key(month,company_id)
    with get_conn() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(748223,%s)',(company_id,))
        old = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s FOR UPDATE',(key,)).fetchone()
        profiles = _profiles(old['wartosc'] if old else None)
        if profiles.get(person_id,{}).get('revision',0)!=expected_revision:
            raise EligibilityConflict('Ktoś zmienił warunki. Odśwież formularz przed zapisem.')
        source = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',
                              (f"platnik_holidays_{period['year']}",)).fetchone()
        rows = _evidence(source['wartosc'] if source else None,person['payer_nip'])
        profiles[person_id] = {field:values.get(field) for field in CONDITIONS}
        profiles[person_id].update({'insurance_code':code,'insured_count':count,'payer_nip':person['payer_nip'],
            'month':month,'verified_month':current_month(),'evidence_hash':evidence_hash(rows),'revision':expected_revision+1,
            'actor_id':actor_id,'updated_at':datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()})
        conn.execute('''INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)
            ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc''',
            (key,json.dumps({'profiles_enc':encrypt_password(json.dumps(profiles,ensure_ascii=False))})))
    from app.utils.request_cache import clear_request_cache
    clear_request_cache()
    return {'revision':expected_revision+1}
