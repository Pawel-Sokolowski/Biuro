"""Shared Enova bank-position weighting: office default and client overrides."""
import json
from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn

SETTINGS_KEY = 'enova_document_count_rules'


def validate_divisor(value):
    if type(value) is not int or not 1 <= value <= 1000:
        raise ValueError('Dzielnik pozycji bankowych musi być liczbą całkowitą od 1 do 1000.')
    return value


def load_rules():
    raw = get_setting(SETTINGS_KEY)
    rules = json.loads(raw) if raw else {'bank_divisor': 3, 'company_overrides': {}}
    validate_divisor(rules['bank_divisor'])
    overrides = rules.get('company_overrides', {})
    if not isinstance(overrides, dict):
        raise ValueError('Nieprawidłowe ustawienia dzielników firm.')
    for company_id, value in overrides.items():
        if not str(company_id).isdigit() or int(company_id) <= 0:
            raise ValueError('Nieprawidłowy identyfikator firmy w ustawieniach Enova.')
        validate_divisor(value)
    return {'bank_divisor': rules['bank_divisor'], 'company_overrides': overrides}


def effective_divisor(rules, company_id):
    return validate_divisor(rules['company_overrides'].get(str(company_id), rules['bank_divisor']))


def save_rules(default, overrides, *, actor_id):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Tylko administrator może zmieniać reguły importu Enova.')
    validate_divisor(default)
    normalized = {}
    for company_id, value in overrides.items():
        if not str(company_id).isdigit() or int(company_id) <= 0:
            raise ValueError('Nieprawidłowy identyfikator firmy.')
        normalized[str(int(company_id))] = validate_divisor(value)
    with get_conn() as conn:
        ids = {row[0] for row in conn.execute('SELECT id FROM firmy WHERE id=ANY(%s)',
                                             ([int(key) for key in normalized],)).fetchall()}
    if ids != {int(key) for key in normalized}:
        raise ValueError('Lista firm zmieniła się. Otwórz ustawienia ponownie.')
    set_setting(SETTINGS_KEY, json.dumps({'bank_divisor': default, 'company_overrides': normalized}))
