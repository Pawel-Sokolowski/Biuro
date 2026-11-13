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
    config.setdefault('database', '')
    config.setdefault('discovery_query_timeout', 120)
    config.setdefault('jpk_query', '')
    config.setdefault('driver', 'auto')
    config.setdefault('trust_server_certificate', False)
    config.setdefault('checking_enabled', True)
    config.setdefault('declarations_query', '')
    config.setdefault('payroll_query', '')
    config.setdefault('hr_events_query', '')
    config['_directory'] = Path(__file__).resolve().parents[3] / 'docs' / 'enova'
    for source in config['sources']:
        for name in ('declarations_query', 'payroll_query', 'hr_events_query'):
            if config.get(name):
                source[name] = config[name]
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
    # Connection credentials can be saved before database discovery/mapping.
    # Import execution checks that at least one company source is configured.
    saved = {key: config[key] for key in ('server', 'username', 'enabled', 'months_back', 'sources')}
    saved['database'] = str(config.get('database') or '').strip()
    from app.integrations.enova.discovery import query_timeout
    saved['discovery_query_timeout'] = query_timeout(config)
    from app.integrations.enova.odbc_support import SUPPORTED_DRIVERS, validate_server
    validate_server(config['server'])
    driver = config.get('driver', 'auto')
    if driver not in ('auto', *SUPPORTED_DRIVERS):
        raise ValueError('Nieprawidłowy sterownik SQL Server.')
    saved['driver'] = driver
    trust = config.get('trust_server_certificate', False)
    if type(trust) is not bool:
        raise ValueError('Opcja zaufania certyfikatowi musi być wartością logiczną.')
    saved['trust_server_certificate'] = trust
    saved['checking_enabled'] = True
    for name in ('declarations_query', 'payroll_query', 'hr_events_query'):
        saved[name] = str(config.get(name, '')).strip()
    saved['jpk_query'] = str(config.get('jpk_query', '')).strip()
    previous = load_connection()
    from datetime import datetime
    from zoneinfo import ZoneInfo
    # Established once; yearly backfills must never move this boundary backwards.
    saved['monthly_counts_from'] = previous.get('monthly_counts_from') or datetime.now(ZoneInfo('Europe/Warsaw')).strftime('%Y-%m')
    saved['password_enc'] = previous.get('password_enc', '')
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
    from app.integrations.enova.odbc_support import load_odbc, resolve_driver, validate_server
    validate_server(config.get('server'))
    pyodbc = load_odbc()
    config = {**config, 'driver': resolve_driver(config, pyodbc)}
    if not config.get('server') or not config.get('username'):
        raise ValueError('Uzupełnij serwer i login SQL.')
    if not password:
        if not config.get('password_enc'):
            raise ValueError('Wpisz hasło SQL. Puste pole może użyć tylko wcześniej zapisanego hasła.')
        _, password = credentials(config)
    results = []
    main_database = str(config.get('database') or '').strip()
    databases = ([main_database] if main_database else []) + [source['database'] for source in config.get('sources') or []]
    for database in dict.fromkeys(databases or ['']):
        connection = None
        try:
            connection = pyodbc.connect(sql_connection_string(config, database, config['username'], password),
                                        timeout=15, attrs_before={101: 1})
            cursor = connection.cursor()
            connection.timeout = 15
            cursor.execute('SELECT DB_NAME()')
            actual = cursor.fetchone()
            if not actual or (database and str(actual[0]).casefold() != database.casefold()):
                raise ValueError('Połączenie wskazuje inną bazę danych.')
            results.append({'database': database or 'Serwer SQL (baza domyślna)', 'ok': True})
        except Exception as exc:
            from app.integrations.enova.odbc_support import describe_sql_error
            error = describe_sql_error(exc, secrets=(password, config.get('password_enc')))
            results.append({'database': database or 'Serwer SQL (baza domyślna)',
                            'ok': False, 'error': error})
        finally:
            if connection is not None:
                connection.close()
    return results
