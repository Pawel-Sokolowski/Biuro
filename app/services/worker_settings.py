"""Shared worker controls; credentials remain in the existing program settings."""
from copy import deepcopy
import json
from app.db.dao_app_settings import get_setting, set_setting

KEY = 'background_worker'
DEFAULTS = {'enabled': False, 'mail': True, 'reminders': True, 'invoices': True,
            'enova': True, 'ezla': True, 'ksef_import': False, 'mail_interval': 120,
            'reminders_interval': 60, 'invoices_interval': 60, 'enova_interval': 900,
            'ezla_interval': 3600, 'ksef_import_interval': 3600, 'saldeo': False, 'saldeo_interval': 300, 'enova_time': '02:00'}


def load_settings():
    value = deepcopy(DEFAULTS)
    value.update(json.loads(get_setting(KEY) or '{}'))
    validate(value)
    return value


def validate(value):
    from datetime import time
    time.fromisoformat(value['enova_time'])
    for key in ('enabled', 'mail', 'reminders', 'invoices', 'enova', 'ezla', 'ksef_import', 'saldeo'):
        if type(value[key]) is not bool:
            raise ValueError('Nieprawidłowy przełącznik workera.')
    for key in ('mail', 'reminders', 'invoices', 'enova', 'ezla', 'ksef_import', 'saldeo'):
        interval = value[key + '_interval']
        if type(interval) is not int or not 30 <= interval <= 86400:
            raise ValueError('Odstęp workera musi wynosić 30–86400 sekund.')


def save_settings(value, *, actor_id):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Tylko administrator może zmieniać ustawienia workera.')
    validate(value)
    set_setting(KEY, json.dumps({key: value[key] for key in DEFAULTS}))
