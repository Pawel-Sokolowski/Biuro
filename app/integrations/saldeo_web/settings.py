from app.remote.boundary import endpoint as office_endpoint
import json
from urllib.parse import urlsplit
from app.db.dao_app_settings import get_setting, set_setting

KEY = 'saldeo_web'
SALDEO_ORIGIN = 'https://saldeo.brainshare.pl'
LOGIN_SELECTORS = {
    'username_selector': 'input[qa-id="username"]',
    'password_selector': 'input[qa-id="password"]',
    'submit_selector': 'button[qa-id="login-button"]',
}
OPERATIONS_URL = SALDEO_ORIGIN + '/biuro/operacje/lista-operacji.hta'
OPERATIONS_ROWS = 'bs-mat-table.background-operation-list-table .mat-row'
OPERATIONS_NEXT = '.background-operation-list .paginator-wrapper-top button.mat-mdc-paginator-navigation-next'
LEGACY_ITEM_SELECTOR = 'div[qa-id^="row-bo-id-"]'
SESSION_SELECTOR = '[qa-id="bo-counter"]'
DEFAULTS = dict(enabled=False, login_url=SALDEO_ORIGIN + '/login/login.hta', history_url=OPERATIONS_URL,
                username='', password_enc='', logged_in_selector=SESSION_SELECTOR, item_selector=OPERATIONS_ROWS,
                empty_selector='', open_selector='', next_selector=OPERATIONS_NEXT, channel='auto',
                executable_path='', max_pages=20, pause_seconds=2, **LOGIN_SELECTORS)


@office_endpoint('app.integrations.saldeo_web.settings.load')
def load():
    saved = json.loads(get_setting(KEY) or '{}')
    value = {**DEFAULTS, **saved}
    for key in ('username_selector','password_selector'):
        if not value[key]:
            value[key] = DEFAULTS[key]
    if value['login_url'].rstrip('/') == SALDEO_ORIGIN:
        value['login_url'] = DEFAULTS['login_url']
    if value['login_url'] == DEFAULTS['login_url'] and value['submit_selector'] in ('', 'button:has-text("Zaloguj")'):
        value['submit_selector'] = LOGIN_SELECTORS['submit_selector']
    if value['history_url'] == OPERATIONS_URL:
        if not value['logged_in_selector']:
            value['logged_in_selector'] = SESSION_SELECTOR
        old_preset = (saved.get('item_selector') in (None, '', LEGACY_ITEM_SELECTOR)
                      and saved.get('open_selector') in (None, '', SESSION_SELECTOR)
                      and not saved.get('next_selector') and not saved.get('empty_selector'))
        if old_preset:
            value.update(item_selector=OPERATIONS_ROWS, open_selector='', next_selector=OPERATIONS_NEXT)
        elif value['item_selector'] != OPERATIONS_ROWS:
            # A custom history needs its own paginator.
            value['item_selector'] = value['item_selector'] or LEGACY_ITEM_SELECTOR
            value['next_selector'] = saved.get('next_selector', '')
    elif 'next_selector' not in saved:
        value['next_selector'] = ''
    return value


def validate(config):
    origin = None
    for key in ('login_url', 'history_url'):
        value = config[key]
        if not value and key == 'history_url' and not config['enabled']:
            continue
        url = urlsplit(value)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('Adresy Saldeo muszą być HTTPS, bez loginu i hasła w adresie.')
        current = (url.hostname, url.port or 443)
        if origin is not None and current != origin:
            raise ValueError('Logowanie i historia muszą być na tym samym serwerze Saldeo.')
        origin = current
    if config['channel'] not in ('auto', 'msedge', 'chrome'):
        raise ValueError('Wybierz Edge lub Chrome zainstalowany na serwerze.')
    if type(config['enabled']) is not bool or not 1 <= config['max_pages'] <= 100 or not 1 <= config['pause_seconds'] <= 60:
        raise ValueError('Nieprawidłowe limity monitorowania.')
    if config['enabled'] and not all(config[k].strip() for k in ('history_url', 'logged_in_selector', 'item_selector')):
        raise ValueError('Uzupełnij adres historii i selektory widoku: zalogowany i cały wpis.')


@office_endpoint('app.integrations.saldeo_web.settings.save')
def save(config, actor_id, password=''):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Tylko administrator konfiguruje monitoring Saldeo.')
    value = {key: config.get(key, default) for key, default in DEFAULTS.items()}
    value['password_enc'] = load()['password_enc']
    if password:
        from app.utils.crypto import encrypt_password
        value['password_enc'] = encrypt_password(password)
    validate(value)
    set_setting(KEY, json.dumps(value, ensure_ascii=False))
