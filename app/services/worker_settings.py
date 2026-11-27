"""Shared worker controls; credentials remain in the existing program settings."""
from app.remote.boundary import endpoint as office_endpoint
from copy import deepcopy
import json
import logging
from app.db.dao_app_settings import get_setting, set_setting

logger = logging.getLogger(__name__)
KEY = 'background_worker'
DEFAULTS = {'enabled': False, 'mail': True, 'reminders': True, 'invoices': True,
            'enova': True, 'enova_weekly': True, 'enova_weekly_time': '20:00', 'ezla': True, 'ksef_import': False, 'mail_interval': 120,
            'reminders_interval': 60, 'invoices_interval': 60,
            'ezla_interval': 3600, 'ksef_import_interval': 3600, 'saldeo': False, 'saldeo_interval': 300, 'platnik': False, 'platnik_interval': 3600,
            'platnik_gui': False, 'platnik_gui_time': '16:00', 'platnik_gui_interval': 60,
            'checking_refresh': True, 'checking_refresh_interval':30,'enova_time': '02:00','ezla_time':'02:00'}

# Enova is checked on a fixed short cadence: the read only touches the local queue
# and contacts Enova when a monthly stage changed. Not a user setting any more.
ENOVA_INTERVAL = 30

# Keys removed from DEFAULTS that may still sit in the stored JSON; dropped on save.
OBSOLETE = ('enova_interval',)


@office_endpoint('app.services.worker_settings.load_settings')
def load_settings():
    value = deepcopy(DEFAULTS)
    try:
        stored = json.loads(get_setting(KEY) or '{}')
        if not isinstance(stored, dict):
            raise ValueError('worker_settings_not_object')
        value.update({key: item for key, item in stored.items() if key not in OBSOLETE})
        validate(value)
    except (TypeError, ValueError, KeyError) as error:
        logger.warning('worker_settings_unreadable error_type=%s', type(error).__name__)
        value = deepcopy(DEFAULTS)
    return value


def validate(value):
    from datetime import time
    time.fromisoformat(value['enova_time'])
    time.fromisoformat(value.get('ezla_time','02:00'))
    weekly_at = time.fromisoformat(value['enova_weekly_time'])
    if weekly_at.isoformat(timespec='minutes') != value['enova_weekly_time'] or weekly_at.tzinfo:
        raise ValueError('Godzina tygodniowego odczytu Enovy musi mieć format GG:MM.')
    at = time.fromisoformat(value['platnik_gui_time'])
    if at.isoformat(timespec='minutes') != value['platnik_gui_time'] or at.tzinfo:
        raise ValueError('Godzina agenta Płatnika musi mieć format GG:MM.')
    for key in ('enabled', 'mail', 'reminders', 'invoices', 'enova', 'enova_weekly', 'ezla', 'ksef_import', 'saldeo', 'platnik', 'platnik_gui', 'checking_refresh'):
        if type(value[key]) is not bool:
            raise ValueError('Nieprawidłowy przełącznik workera.')
    for key in ('mail', 'reminders', 'invoices', 'ezla', 'ksef_import', 'saldeo', 'platnik', 'platnik_gui', 'checking_refresh'):
        interval = value[key + '_interval']
        if type(interval) is not int or not 30 <= interval <= 86400:
            raise ValueError('Odstęp workera musi wynosić 30–86400 sekund.')


@office_endpoint('app.services.worker_settings.save_settings')
def save_settings(value, *, actor_id):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Tylko administrator może zmieniać ustawienia workera.')
    value = {**DEFAULTS,**{key: item for key, item in value.items() if key not in OBSOLETE}}
    validate(value)
    set_setting(KEY, json.dumps({key: value[key] for key in DEFAULTS}))
