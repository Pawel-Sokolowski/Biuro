"""Company report scope for checking; absence of imported rows is not an obligation."""
from app.remote.boundary import endpoint as office_endpoint
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from app.accounting.report_types import form_key
from app.db.dao_app_settings import get_setting
from app.db.dao_conn import get_conn

from app.utils.months import month_bounds

# Nazwy w interfejsie: zawsze skrót formularza (patrz app/accounting/report_types.py).
MONTHLY_FORMS = (('JPKVAT', 'JPK_VAT'), ('VATUE', 'VAT-UE'),
                 ('VAT8', 'VAT-8'), ('VAT9M', 'VAT-9M'), ('VAT12', 'VAT-12'), ('VAT27', 'VAT-27'),
                 ('PIT', 'PIT'), ('CIT', 'CIT'),
                 ('DRA', 'ZUS DRA'), ('RCA', 'ZUS RCA'), ('RSA', 'ZUS RSA'),
                 ('RPA', 'ZUS RPA'), ('RZA', 'ZUS RZA'),
                 ('ZUA', 'ZUS ZUA — zgłoszenia'), ('ZZA', 'ZUS ZZA — zgłoszenia'),
                 ('ZWUA', 'ZUS ZWUA — wyrejestrowania'), ('ZIUA', 'ZUS ZIUA — zmiany danych'),
                 ('ZCNA', 'ZUS ZCNA — zgłoszenia rodzin'))
VAT_PERIODS = (('auto', 'Według statusu VAT — miesięcznie'), ('monthly', 'VAT miesięczny'),
               ('quarterly', 'VAT kwartalny'), ('none', 'Nie dotyczy'))


def document_types(value):
    """Rodzaje dokumentów Enovy wliczane do liczby dokumentów za miesiąc.

    Brak wpisu albo ``None`` znaczy „wszystkie rodzaje” — tak było dotąd i tak
    zostaje domyślnie. Zapisujemy tylko wtedy, gdy użytkownik coś wyłączy.
    """
    from app.integrations.enova.document_counts import DOCUMENT_TYPE_LABELS
    known = [key for key, _ in DOCUMENT_TYPE_LABELS]
    if not value:
        return None
    chosen = [key for key in value if key in known]
    return chosen if len(chosen) < len(known) else None


# Skróty używane przez importy (Enova, Płatnik) — jeden słownik, żeby „ZUS DRA”
# i „DRA” nie tworzyły dwóch wpisów w jednej firmie.
FORM_ALIASES = {
    'ZUSDRA': 'DRA', 'ZUSRCA': 'RCA', 'ZUSRSA': 'RSA', 'ZUSRPA': 'RPA', 'ZUSRZA': 'RZA',
    'ZUSZUA': 'ZUA', 'ZUSZZA': 'ZZA', 'ZUSZWUA': 'ZWUA', 'ZUSZIUA': 'ZIUA', 'ZUSZCNA': 'ZCNA',
    'ZUSIWA': 'IWA', 'ZUSZSWA': 'ZSWA', 'ZUSZFA': 'ZFA',
}


def canonical_form(value):
    """Sprowadza nazwę formularza do klucza z MONTHLY_FORMS (albo zwraca ją bez zmian)."""
    key = form_key(value)
    key = FORM_ALIASES.get(key, key)
    return key if key in dict(MONTHLY_FORMS) else key


@office_endpoint('app.companies.checking_obligations.load')
def load(company_id):
    return json.loads(get_setting(f'checking_obligations_{company_id}') or '{}') if company_id else {}


def profile(value, month):
    month_bounds(month)
    if isinstance(value, str):
        value = json.loads(value)
    periods = (value or {}).get('periods', {})
    imported = (value or {}).get('imported',{}).get(month,{})
    eligible = [period for period in periods if period <= month]
    # Unconfigured legacy companies stay visible until their scope is specified.
    if eligible:
        manual = periods[max(eligible)]
        return {**manual,'forms':{**imported.get('forms',{}),**manual['forms']},'_configured':True,
                '_imported':imported}
    return {'vat_period':imported.get('vat_period','auto'),'forms':imported.get('forms',{}),'_imported':imported}


def default_forms(company):
    """Zakres rozliczeń wynikający z danych rejestrowych firmy.

    Używany tylko wtedy, gdy firma nie ma jeszcze ręcznych ustawień ani importu,
    żeby sprawdzanie nie było puste. Status VAT decyduje o JPK/VAT-UE,
    forma opodatkowania o zaliczkach PIT/CIT.
    """
    vat_status = str(company.get('status_vat') or '').strip().casefold()
    zwolniony = vat_status in ('zwolniony', 'nieczynny', 'niezarejestrowany')
    forms = {'JPKVAT': 'none' if zwolniony else 'monthly',
             'VATUE': 'none' if zwolniony else 'monthly'}
    taxation = str(company.get('forma_opodatkowania') or '').strip().casefold()
    legal = str(company.get('forma_dzialalnosci') or '').strip().casefold()
    # „KPiR”/„księgi rachunkowe” to rodzaj księgowości, nie forma opodatkowania —
    # osoby fizyczne płacą zaliczkę PIT, także na skali, dlatego „księg” nie wyklucza PIT.
    company_form = ('cit' in taxation or 'estoń' in taxation or
                    any(word in legal for word in ('spółk', 'sp. z o.o', 'z o.o', 'akcyjna', 'komandyt',
                                                   'fundacja', 'stowarzyszenie')))
    if company_form:
        forms['CIT'] = 'monthly'
    else:
        forms['PIT'] = 'monthly'
    return {'vat_period': 'auto', 'forms': forms, '_default': True}


def add_imported(conn, company_id, month, entries, source):
    """Positive evidence only. Missing documents never disable an obligation."""
    month_bounds(month)
    supported = dict(MONTHLY_FORMS)
    forms = set()
    for entry in entries:
        key = canonical_form(entry.get('form'))
        if key in ('JPKV7','JPKV7M','JPKV7K','VAT','VAT7','VAT7K'):
            key = 'JPKVAT'
        if key in supported:
            forms.add(key)
    if not forms:
        return
    key = f'checking_obligations_{company_id}'
    conn.execute('SELECT pg_advisory_xact_lock(748221,%s)',(company_id,))
    row = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s FOR UPDATE',(key,)).fetchone()
    value = json.loads(row[0]) if row else {}
    imported = value.setdefault('imported',{}).setdefault(month,{'forms':{},'sources':{}})
    imported['forms'].update(dict.fromkeys(sorted(forms),'monthly'))
    imported.setdefault('sources',{})[source] = {'forms':sorted(forms),
        'at':datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
    variants = {form_key(entry.get('form_variant')) for entry in entries}
    cadences = ({'quarterly'} if variants & {'JPKV7K','VAT7K','VAT7D'} else set()) | (
                {'monthly'} if variants & {'JPKV7M','VAT7'} else set())
    imported['sources'][source]['vat_periods'] = sorted(cadences)
    cadences = {cadence for detail in imported['sources'].values() for cadence in detail.get('vat_periods',[])}
    imported.pop('vat_period',None)
    if len(cadences)==1:
        imported['vat_period'] = next(iter(cadences))
    conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                 'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',(key,json.dumps(value)))


@office_endpoint('app.companies.checking_obligations.save')
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
        if any(value['forms'].get(key) == 'quarterly' for key in dict(MONTHLY_FORMS) if key not in ('PIT', 'CIT')):
            raise ValueError('Raporty ZUS, VAT-UE i ewidencja JPK są miesięczne.')
        if 'document_types' in value:
            from app.integrations.enova.document_counts import DOCUMENT_TYPE_LABELS
            allowed = {key for key, _ in DOCUMENT_TYPE_LABELS}
            if value['document_types'] is not None and (
                    not isinstance(value['document_types'], list) or set(value['document_types']) - allowed):
                raise ValueError('Nieprawidłowe rodzaje dokumentów do liczby dokumentów.')
    with get_conn() as conn:
        if not conn.execute('SELECT id FROM firmy WHERE id=%s', (company_id,)).fetchone():
            raise ValueError('Nie znaleziono firmy.')
        conn.execute('SELECT pg_advisory_xact_lock(748221,%s)', (company_id,))
        row = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s FOR UPDATE',
                           (f'checking_obligations_{company_id}',)).fetchone()
        value = json.loads(row[0]) if row else {}
        periods = value.setdefault('periods', {})
        # Zakres obowiązuje od wybranego miesiąca: miesiąc po zmianie nie może
        # dziedziczyć starej wartości, więc brak jawnego wpisu znaczy „bez zmian”
        # do końca, a nie „wróć do domyślnych”.
        previous = {month: periods.get(month) for month in edits}
        periods.update(edits)
        value['periods'] = periods
        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                     'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                     (f'checking_obligations_{company_id}', json.dumps(value)))
        conn.execute("INSERT INTO audit_log(tabela,rekord_id,akcja,uzytkownik_id,szczegoly) VALUES ('firmy',%s,'edycja',%s,%s)",
                     (company_id, actor_id, json.dumps({'monthly_obligations': edits,
                                                        'previous': previous}, ensure_ascii=False)))
    from app.utils.request_cache import clear_request_cache
    clear_request_cache()


@office_endpoint('app.companies.checking_obligations.reset_obligations')
def reset_obligations(actor_id=None):
    """Usuwa ustawiony zakres rozliczeń miesięcznych (wszystkie firmy).

    Kasuje klucze „periods” (zakres ustawiony ręcznie), a zostawia „imported”,
    bo to dane odczytane ze źródła, a nie deklaracja użytkownika. Zwraca liczbę
    firm, w których faktycznie coś usunięto.
    """
    from app.security.authorization import can
    if actor_id is not None and not can(actor_id, 'can_edit_companies'):
        raise PermissionError('Brak uprawnień do wyzerowania deklaracji.')
    cleared = 0
    with get_conn() as conn:
        conn.execute("INSERT INTO audit_log(tabela,akcja,uzytkownik_id,szczegoly) "
                     "VALUES ('app_settings','zerowanie_deklaracji_miesiecznych',%s,%s)",
                     (actor_id, json.dumps({'scope': 'checking_obligations'}, ensure_ascii=False)))
        rows = conn.execute("SELECT klucz, wartosc FROM app_settings "
                            "WHERE klucz LIKE 'checking_obligations_%'").fetchall()
        for key, raw in rows:
            try:
                value = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, ValueError):
                continue
            if not isinstance(value, dict) or not value.get('periods'):
                continue
            value['periods'] = {}
            # Bez ręcznych ustawień zostają wyłącznie formularze wykryte w imporcie.
            conn.execute('UPDATE app_settings SET wartosc=%s WHERE klucz=%s',
                         (json.dumps(value, ensure_ascii=False), key))
            cleared += 1
    from app.utils.request_cache import clear_request_cache
    clear_request_cache()
    return cleared


def report_applies(company, form, month=None):
    month = month or company.get('checking_period') or datetime.now(ZoneInfo('Europe/Warsaw')).strftime('%Y-%m')
    selected = profile(company.get('checking_obligations'), month)

    # Brak ręcznej konfiguracji i brak danych zaimportowanych oznacza brak
    # obowiązków. Import ENOVA/Płatnika uzupełni wyłącznie wykryte formularze.
    if not selected.get('_configured') and not (selected.get('_imported') or {}):
        return False

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
    # Formularz jest obowiązkiem tylko po imporcie albo ręcznym ustawieniu.
    cadence = selected.get('forms', {}).get(key, 'none')
    return cadence != 'none' and (cadence != 'quarterly' or int(month[-2:]) % 3 == 0)
