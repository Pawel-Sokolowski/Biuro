"""Shared connection configuration; passwords use the deployment encryption key."""
import json
from pathlib import Path
from app.db.dao_app_settings import get_setting, set_setting

KEY = 'enova_connection'


def load_connection():
    config = json.loads(get_setting(KEY) or '{}')
    config.setdefault('enabled', False)
    config.setdefault('months_back', 3)
    config.setdefault('sources', [])
    config.setdefault('jpk_query', '')
    config.setdefault('driver', 'auto')
    config['_directory'] = Path(__file__).resolve().parents[3] / 'docs' / 'enova'
    for source in config['sources']:
        if config.get('jpk_query'):
            source['jpk_query'] = config['jpk_query']
    return config


def save_connection(config, *, actor_id, password=''):
    from app.security.authorization import is_administrator
    from app.integrations.enova.document_counts import normalize_nip, requested_months
    from datetime import date
    if not is_administrator(actor_id):
        raise PermissionError('Tylko administrator może zmieniać połączenie Enova.')
    requested_months(date.today(), config['months_back'])
    if not str(config.get('server', '')).strip():
        raise ValueError('Uzupełnij serwer SQL (adres IP\\instancja lub host,port).')
    seen_nips, seen_databases = set(), set()
    for source in config['sources']:
        nip = normalize_nip(source['nip'])
        database = source['database'].strip().casefold()
        if not database or nip in seen_nips or database in seen_databases:
            raise ValueError('Każda firma i baza muszą wystąpić dokładnie raz.')
        seen_nips.add(nip)
        seen_databases.add(database)
    if config['enabled'] and not config['sources']:
        raise ValueError('Dodaj mapowanie bazy przynajmniej jednej firmy.')
    saved = {key: config[key] for key in ('server', 'username', 'enabled', 'months_back', 'sources')}
    from app.integrations.enova.odbc_support import SUPPORTED_DRIVERS
    driver = config.get('driver', 'auto')
    if driver not in ('auto', *SUPPORTED_DRIVERS):
        raise ValueError('Nieprawidłowy sterownik SQL Server.')
    saved['driver'] = driver
    saved['jpk_query'] = str(config.get('jpk_query', '')).strip()
    saved['password_enc'] = load_connection().get('password_enc', '')
    if password:
        from app.utils.crypto import encrypt_password
        saved['password_enc'] = encrypt_password(password)
    if not saved['username'].strip() or not saved['password_enc']:
        raise ValueError('Uzupełnij login i hasło SQL.')
    set_setting(KEY, json.dumps(saved, ensure_ascii=False))


def credentials(config):
    from app.utils.crypto import decrypt_password
    return config['username'], decrypt_password(config['password_enc'])


def check_connection(config, *, password=''):
    """Test actual database access without running imports or writing data."""
    from app.integrations.enova.document_counts import sql_connection_string
    from app.integrations.enova.odbc_support import load_odbc, resolve_driver
    pyodbc = load_odbc()
    config = {**config, 'driver': resolve_driver(config, pyodbc)}
    if not config.get('server') or not config.get('username'):
        raise ValueError('Uzupełnij serwer i login SQL.')
    if not password:
        if not config.get('password_enc'):
            raise ValueError('Wpisz hasło SQL. Puste pole może użyć tylko wcześniej zapisanego hasła.')
        _, password = credentials(config)
    results = []
    for source in config.get('sources') or [{'database': ''}]:
        connection = None
        try:
            connection = pyodbc.connect(sql_connection_string(config, source['database'], config['username'], password),
                                        timeout=15, attrs_before={101: 1})
            cursor = connection.cursor()
            cursor.timeout = 15
            cursor.execute('SELECT DB_NAME()')
            actual = cursor.fetchone()
            if not actual or (source['database'] and str(actual[0]).casefold() != source['database'].casefold()):
                raise ValueError('Połączenie wskazuje inną bazę danych.')
            results.append({'database': source['database'] or 'Serwer SQL (baza domyślna)', 'ok': True})
        except Exception:
            # Driver messages can contain connection strings; never display them.
            results.append({'database': source['database'] or 'Serwer SQL (baza domyślna)', 'ok': False})
        finally:
            if connection is not None:
                connection.close()
    return results
