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
CONDITIONS = {
    'registered': 'Wpis CEIDG lub status komornika',
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
    if 'E-mail' not in allowed_tabs(actor_id):
        raise PermissionError('Brak dostępu do mailingu.')
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


def evidence_hash(rows):
    facts = sorted((str(r.get('source_id')), str(r.get('status_zrodlowy')), str(r.get('miesiac')),
                    str(r.get('kod_ulgi')), str(r.get('bazowy_kod_ubezpieczenia'))) for r in rows)
    return hashlib.sha256(json.dumps(facts, ensure_ascii=False).encode()).hexdigest()


def assess(profile, rows, period, payer_nip, *, now_month=None, known_code=''):
    """Pure classification: never infer missing declarations from a title code."""
    now = now_month or current_month()
    if any(row.get('status_zrodlowy') == 'A' for row in rows):
        return 'used', ['Aktualny wpis RWS w roku ulgi — nie przypominaj o niewykorzystanych wakacjach.']
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
    if profile and profile.get('evidence_hash') != evidence_hash(rows):
        reasons.append('Wpisy RWS zmieniły się od weryfikacji warunków.')
    return ('review', reasons) if reasons else ('eligible', ['Warunki potwierdzone przez biuro; brak aktualnego RWS w roku ulgi.'])


def _people(company):
    from app.companies.identities import is_civil
    from app.db.dao_companies import get_partners
    if is_civil(company):
        return [{'id':str(p['id']), 'name':' '.join(filter(None, (p.get('imie_pierwsze'),p.get('nazwisko')))) or 'Wspólnik',
                 'payer_nip':_nip(p.get('nip')), 'email':p.get('email') or ''} for p in get_partners(company['id'])]
    kind = str(company.get('forma_dzialalnosci') or '').strip().casefold()
    excluded = kind.startswith(('spółka','spolka','sp.','sp z','spółdziel','fundac','stowarz'))
    return [{'id':'company', 'name':company.get('nazwa_skrocona') or company['nazwa'], 'entity_excluded':excluded,
             'payer_nip':_nip(company.get('nip')), 'email':company.get('adres_email') or ''}]


def _suggested_code(person, notes, rows):
    value = notes.get('kod_ubezpieczenia',{}).get('value') if person['id']=='company' else ''
    codes = {r.get('bazowy_kod_ubezpieczenia') for r in rows if r.get('bazowy_kod_ubezpieczenia')}
    return value or (next(iter(codes)) if len(codes)==1 else '')


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
            status, reasons = assess(profile, rows, period, person['payer_nip'],known_code=_suggested_code(person,notes,rows))
            if person.get('entity_excluded'):
                status,reasons = 'ineligible',['Wakacje dotyczą przedsiębiorcy CEIDG lub komornika, nie tej osoby prawnej.']
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
    for person in people:
        profile = profiles.get(person['id'], {})
        rows = [r for r in current_rows if person['payer_nip'] and r.get('nip')==person['payer_nip']]
        historical = [r for r in previous_rows if person['payer_nip'] and r.get('nip')==person['payer_nip']]
        person['values'] = profile if profile.get('payer_nip')==person['payer_nip'] else {}
        person['suggested_code'] = _suggested_code(person,notes,rows or historical)
        person['revision'] = profile.get('revision',0)
        person['status'],person['reasons'] = assess(profile,rows,period,person['payer_nip'],known_code=person['suggested_code'])
        if person.get('entity_excluded'):
            person['status'],person['reasons'] = 'ineligible',['Wakacje dotyczą przedsiębiorcy CEIDG lub komornika, nie tej osoby prawnej.']
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
