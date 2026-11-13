"""Company report scope for checking; absence of imported rows is not an obligation."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from app.accounting.report_types import form_key
from app.db.dao_app_settings import get_setting
from app.db.dao_conn import get_conn

from app.utils.months import month_bounds

MONTHLY_FORMS = (('JPKVAT', 'JPK VAT — ewidencja miesięczna'), ('VATUE', 'VAT-UE'),
                 ('PIT', 'Zaliczka PIT'), ('CIT', 'Zaliczka CIT'),
                 ('DRA', 'ZUS DRA'), ('RCA', 'ZUS RCA'), ('RSA', 'ZUS RSA'),
                 ('RPA', 'ZUS RPA'), ('RZA', 'ZUS RZA'))
VAT_PERIODS = (('auto', 'Według statusu VAT — miesięcznie'), ('monthly', 'VAT miesięczny'),
               ('quarterly', 'VAT kwartalny'), ('none', 'Nie dotyczy'))


def load(company_id):
    return json.loads(get_setting(f'checking_obligations_{company_id}') or '{}') if company_id else {}


def profile(value, month):
    month_bounds(month)
    if isinstance(value, str):
        value = json.loads(value)
    periods = (value or {}).get('periods', {})
    eligible = [period for period in periods if period <= month]
    # Unconfigured legacy companies stay visible until their scope is specified.
    return periods[max(eligible)] if eligible else {'vat_period': 'auto', 'forms': {}}


def save(company_id, edits, actor_id):
    from app.security.authorization import can, assigned_company_ids
    if actor_id is not None and (not can(actor_id, 'can_edit_companies') or company_id not in assigned_company_ids(actor_id)):
        raise PermissionError('Brak uprawnień do edycji zakresu raportów firmy.')
    for month, value in edits.items():
        month_bounds(month)
        if value.get('vat_period') not in dict(VAT_PERIODS) or not isinstance(value.get('forms'), dict):
            raise ValueError('Nieprawidłowy zakres rozliczeń miesięcznych.')
        if set(value['forms']) - dict(MONTHLY_FORMS).keys() or any(
                cadence not in ('monthly', 'quarterly', 'none') for cadence in value['forms'].values()):
            raise ValueError('Nieprawidłowe formularze rozliczeń miesięcznych.')
        if any(value['forms'].get(key) == 'quarterly' for key in ('JPKVAT', 'VATUE', 'DRA', 'RCA', 'RSA', 'RPA', 'RZA')):
            raise ValueError('Raporty ZUS, VAT-UE i ewidencja JPK są miesięczne.')
    with get_conn() as conn:
        if not conn.execute('SELECT id FROM firmy WHERE id=%s', (company_id,)).fetchone():
            raise ValueError('Nie znaleziono firmy.')
        conn.execute('SELECT pg_advisory_xact_lock(748221,%s)', (company_id,))
        row = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s FOR UPDATE',
                           (f'checking_obligations_{company_id}',)).fetchone()
        value = json.loads(row[0]) if row else {}
        value.setdefault('periods', {}).update(edits)
        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                     'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                     (f'checking_obligations_{company_id}', json.dumps(value)))
        conn.execute("INSERT INTO audit_log(tabela,rekord_id,akcja,uzytkownik_id,szczegoly) VALUES ('firmy',%s,'edycja',%s,%s)",
                     (company_id, actor_id, json.dumps({'monthly_obligations': edits})))
    from app.utils.request_cache import clear_request_cache
    clear_request_cache()


def report_applies(company, form, month=None):
    month = month or company.get('checking_period') or datetime.now(ZoneInfo('Europe/Warsaw')).strftime('%Y-%m')
    selected = profile(company.get('checking_obligations'), month)
    key = form_key(form)
    if key in ('VAT', 'VAT7', 'VAT7K', 'JPKVAT', 'JPKV7', 'JPKV7M', 'JPKV7K'):
        status = str(company.get('status_vat') or '').strip().casefold()
        if status in ('zwolniony', 'nieczynny', 'niezarejestrowany'):
            return False
        # Unknown VAT status stays visible for review; old boolean is a fallback
        # only when no explicit status is available.
        if not status and company.get('vat_czynny') is not None:
            if not company['vat_czynny']:
                return False
        cadence = selected['vat_period']
        if cadence == 'none':
            return False
        if key in ('VAT', 'VAT7', 'VAT7K'):
            return cadence != 'quarterly' or int(month[-2:]) % 3 == 0
        key = 'JPKVAT'  # V7K ewidencja is still monthly.
    cadence = selected.get('forms', {}).get(key, 'monthly')
    return cadence != 'none' and (cadence != 'quarterly' or int(month[-2:]) % 3 == 0)
