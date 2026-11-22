"""Annual office worksheet, kept independently of replaceable Płatnik snapshots."""
from app.remote.boundary import endpoint as office_endpoint
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.companies.holiday_eligibility import (
    context, default_relief_month, rca_holidays, worksheet_assessments, worksheet_label,
)
from app.db.dao_conn import get_conn
from app.db.dao_companies import employee_initials
from app.integrations.enova.accounting import require_access
from app.security.authorization import assigned_company_ids
from app.utils.crypto import encrypt_password, decrypt_password
from .holidays import FIELDS, read_year

WORKSHEET_FIELDS = (*FIELDS, 'moze_skorzystac', 'zlozone')


class WorksheetConflict(ValueError):
    pass


def validate_year(year):
    if type(year) is not int or not 2024 <= year <= 2100:
        raise ValueError('Nieprawidłowy rok zestawienia.')


def note_key(year, company_id):
    return f'platnik_holiday_notes_{year}_{company_id}'


def decode_notes(raw):
    if not raw:
        return {}
    return json.loads(decrypt_password(json.loads(raw)['fields_enc']))


@office_endpoint('app.integrations.platnik.holiday_tracking.read_tracking_year')
def read_tracking_year(actor_id, year, month=None):
    validate_year(year)
    period = context(default_relief_month(year) if month is None else month)
    if period['year'] != year:
        raise ValueError('Miesiąc oceny musi należeć do wybranego roku.')
    payload = read_year(actor_id, year)
    companies = payload['companies']
    keys = [note_key(year, company['id']) for company in companies]
    with get_conn() as conn:
        notes = {row['klucz']: decode_notes(row['wartosc']) for row in conn.execute(
            'SELECT klucz,wartosc FROM app_settings WHERE klucz=ANY(%s)', (keys,)).fetchall()}
        actor = conn.execute('SELECT imie,nazwisko FROM pracownicy WHERE id=%s', (actor_id,)).fetchone()
        rca_by_company = rca_holidays(conn, [company['id'] for company in companies], year)
    assessed = worksheet_assessments(companies, period,
                                     {c['id']: notes.get(note_key(year, c['id']), {}) for c in companies},
                                     rca_by_company)
    grouped = {}
    for row in payload['rows']:
        if row.get('firma_id') is not None:
            grouped.setdefault(row['firma_id'], []).append(row)
    rows = []
    for company in sorted(companies, key=lambda c: c['nazwa'].casefold()):
        sources = grouped.get(company['id'], [])
        rca = rca_by_company.get(company['id'], [])
        rca_months = {item['month'] for item in rca if item['sent']}
        # One office case per company and year. Multiple source persons/months
        # remain available in details; no arbitrary "latest" record is selected.
        source_values = {field: None for field in WORKSHEET_FIELDS}
        for field in ('imiona_nazwiska', 'kod_ubezpieczenia', 'miesiac'):
            values = []
            for source in sources:
                value = source.get(field)
                if value is not None and str(value).strip() and str(value).strip() not in values:
                    values.append(str(value).strip())
            source_values[field] = '; '.join(values) or None
        if rca_months:
            months = {int(month) for month in (source_values['miesiac'] or '').split('; ') if month.isdigit()}
            source_values['miesiac'] = '; '.join(map(str, sorted(months | rca_months)))
        eligibility = assessed[company['id']]
        source_values['moze_skorzystac'] = worksheet_label(eligibility)
        fields = notes.get(note_key(year, company['id']), {})
        values = {field: fields[field]['value'] if fields.get(field, {}).get('value') is not None
                  else source_values[field] for field in WORKSHEET_FIELDS}
        values['moze_skorzystac'] = source_values['moze_skorzystac']
        rows.append({**values, 'firma': company['nazwa'], 'firma_id': company['id'],
                     'nip': company['nip'], 'source_values': source_values,
                     'sources': sources, 'notes': fields, 'rca_holidays': rca, 'eligibility': eligibility,
                     'statuses': sorted({s.get('status_zrodlowy') or '?' for s in sources})})
    return {**payload, 'rows': rows, 'source_rows': payload['rows'], 'eligibility_month': period['month'],
            'unassigned': sum(row.get('firma_id') is None for row in payload['rows']),
            'source_count': len(payload['rows']),
            'current_initials': employee_initials(actor['imie'], actor['nazwisko']) if actor else ''}


@office_endpoint('app.integrations.platnik.holiday_tracking.save_field')
def save_field(actor_id, year, company_id, field, value, expected_revision):
    """Per-field optimistic edit under a DB lock; NULL restores source/default.

    Empty text is an explicit office override. An import never writes these keys.
    """
    validate_year(year)
    require_access(actor_id)
    if type(company_id) is not int or not 0 < company_id < 2**31 or company_id not in assigned_company_ids(actor_id):
        raise PermissionError('Brak dostępu do firmy.')
    if field not in WORKSHEET_FIELDS or (value is not None and (not isinstance(value, str) or len(value) > 2000)):
        raise ValueError('Nieprawidłowa komórka zestawienia (maksymalnie 2000 znaków).')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('Nieprawidłowa wersja komórki.')
    key = note_key(year, company_id)
    with get_conn() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(748216,%s)', (company_id,))
        if not conn.execute('SELECT id FROM firmy WHERE id=%s', (company_id,)).fetchone():
            raise ValueError('Firma już nie istnieje.')
        actor = conn.execute('SELECT imie,nazwisko FROM pracownicy WHERE id=%s AND COALESCE(aktywny,1)=1', (actor_id,)).fetchone()
        if not actor:
            raise PermissionError('Pracownik nie jest aktywny.')
        stored = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s FOR UPDATE', (key,)).fetchone()
        notes = decode_notes(stored['wartosc'] if stored else None)
        if notes.get(field, {}).get('revision', 0) != expected_revision:
            raise WorksheetConflict('Ktoś zmienił tę komórkę. Odśwież zestawienie przed ponowną edycją.')
        notes[field] = {'value': value, 'revision': expected_revision + 1,
                        'actor_id': actor_id, 'actor_name': ' '.join(filter(None, (actor['imie'], actor['nazwisko']))),
                        'updated_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
        encoded = json.dumps({'fields_enc': encrypt_password(json.dumps(notes, ensure_ascii=False))})
        conn.execute('''INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)
            ON CONFLICT(klucz) DO UPDATE SET wartosc=excluded.wartosc''', (key, encoded))
    from app.utils.request_cache import clear_request_cache
    clear_request_cache()
    return notes[field]
