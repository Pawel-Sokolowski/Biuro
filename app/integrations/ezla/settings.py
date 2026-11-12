"""Shared payer-service credentials for the office's configured ZUS reports."""
import json
from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn
from app.utils.crypto import encrypt_password, decrypt_password

KEY = 'ezla_service'


def load_settings():
    return {'enabled': False, 'login': '', 'password_enc': '', 'zip_password_enc': '', 'companies': [], 'pause_seconds': 2, 'error_pause_seconds': 30,
            **json.loads(get_setting(KEY) or '{}')}


def save_settings(value, *, actor_id, password='', zip_password=''):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Tylko administrator może zmieniać usługę e-ZLA.')
    old = load_settings()
    for key, lower, upper in (('pause_seconds', 1, 60), ('error_pause_seconds', 5, 600)):
        number = value.get(key, old[key])
        if type(number) is not int or not lower <= number <= upper:
            raise ValueError('Nieprawidłowy odstęp między firmami e-ZLA.')
    if type(value['enabled']) is not bool or any(type(i) is not int for i in value['companies']):
        raise ValueError('Nieprawidłowe ustawienia e-ZLA.')
    if value.get('error_pause_seconds', old['error_pause_seconds']) < value.get('pause_seconds', old['pause_seconds']):
        raise ValueError('Przerwa po błędzie nie może być krótsza niż zwykła przerwa między firmami.')
    companies = sorted(set(value['companies']))
    with get_conn() as conn:
        rows = conn.execute('SELECT id,nip FROM firmy WHERE id=ANY(%s)', (companies,)).fetchall()
    if len(rows) != len(companies):
        raise ValueError('Nieznana firma w ustawieniach e-ZLA.')
    from app.integrations.enova.document_counts import normalize_nip
    nips = [normalize_nip(row['nip']) for row in rows]
    if len(nips) != len(set(nips)):
        raise ValueError('NIP musi wskazywać jedną firmę w konfiguracji e-ZLA.')
    saved = {'pause_seconds': value.get('pause_seconds', old['pause_seconds']),
             'error_pause_seconds': value.get('error_pause_seconds', old['error_pause_seconds']),
             'enabled': value['enabled'], 'login': str(value['login']).strip(), 'companies': companies,
             'password_enc': encrypt_password(password) if password else old['password_enc'],
             'zip_password_enc': encrypt_password(zip_password) if zip_password else old['zip_password_enc']}
    if saved['enabled'] and (not companies or not all(saved[key] for key in ('login', 'password_enc', 'zip_password_enc'))):
        raise ValueError('Wybierz firmy i uzupełnij login, hasło usługi oraz osobne hasło ZIP.')
    set_setting(KEY, json.dumps(saved))


def credentials(config):
    return config['login'], decrypt_password(config['password_enc']), decrypt_password(config['zip_password_enc'])
