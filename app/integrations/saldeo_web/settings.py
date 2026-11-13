import json
from urllib.parse import urlsplit
from app.db.dao_app_settings import get_setting, set_setting

KEY = 'saldeo_web'
DEFAULTS = dict(enabled=False, login_url='https://saldeo.brainshare.pl/', history_url='https://saldeo.brainshare.pl/biuro/operacje/lista-operacji.hta',
                username='', password_enc='', logged_in_selector='', item_selector='',
                empty_selector='', open_selector='', next_selector='', username_selector='', password_selector='',
                submit_selector='', channel='auto', executable_path='', max_pages=20, pause_seconds=2)


def load():
    return {**DEFAULTS, **json.loads(get_setting(KEY) or '{}')}


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
    if config['enabled'] and not all(config[k].strip() for k in ('history_url', 'logged_in_selector', 'item_selector', 'empty_selector')):
        raise ValueError('Uzupełnij adres historii i selektory widoku: zalogowany, wpis i pusta historia.')


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
