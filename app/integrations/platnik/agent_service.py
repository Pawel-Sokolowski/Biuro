"""libpq service and password files for the interactive Płatnik agent.

The agent connects with ``psycopg.connect(service='biuro_platnik_agent')`` instead of
reusing the office DSN: it runs as its own Windows task and must not inherit the
schema owner's credentials. These files live in the agent account's APPDATA and are
written once by the settings page, never by the agent itself.
"""
from pathlib import Path
from urllib.parse import unquote, urlsplit

SERVICE_NAME = 'biuro_platnik_agent'


def _settings_dir():
    """%APPDATA%\\postgresql on Windows; the same path libpq reads."""
    import os
    base = os.getenv('APPDATA')
    if not base:
        raise ValueError('Brak katalogu APPDATA — pliki libpq wymagają konta Windows.')
    return Path(base) / 'postgresql'


def parse_dsn(dsn):
    """Split the office DB_URL into the parts libpq needs; no password is logged."""
    url = urlsplit(str(dsn or '').strip())
    if url.scheme not in ('postgresql', 'postgres') or not url.hostname or not url.path.strip('/'):
        raise ValueError('Nieprawidłowy adres bazy (DB_URL). Oczekiwano postgresql://użytkownik:hasło@host:5432/baza.')
    return {'host': url.hostname, 'port': str(url.port or 5432), 'dbname': url.path.strip('/'),
            'user': unquote(url.username or '')}


def service_file(host, port, dbname, user):
    """One service stanza; the password stays in the separate pgpass file."""
    values = (host, port, dbname, user)
    if any(not str(value).strip() or any(c in str(value) for c in '\r\n=') for value in values):
        raise ValueError('Nieprawidłowe dane usługi libpq dla agenta Płatnika.')
    return f'[{SERVICE_NAME}]\nhost={host}\nport={port}\ndbname={dbname}\nuser={user}\n'


def pgpass_line(host, port, dbname, user, password):
    """libpq escapes both the colon and the backslash inside a password field."""
    if not str(password or ''):
        raise ValueError('Podaj hasło loginu agenta Płatnika.')
    if any(c in str(value) for c in '\r\n' for value in (host, port, dbname, user, password)):
        raise ValueError('Hasło lub dane usługi zawierają znak nowej linii.')
    escaped = str(password).replace('\\', '\\\\').replace(':', '\\:')
    return f'{host}:{port}:{dbname}:{user}:{escaped}\n'


def write_files(host, port, dbname, user, password, *, directory=None):
    """Write both files with owner-only permissions and return their paths."""
    target = Path(directory) if directory else _settings_dir()
    target.mkdir(parents=True, exist_ok=True)
    service, pgpass = target / '.pg_service.conf', target / 'pgpass.conf'
    for path, text in ((service, service_file(host, port, dbname, user)),
                       (pgpass, pgpass_line(host, port, dbname, user, password))):
        path.write_text(text, encoding='utf-8', newline='\n')
        path.chmod(0o600)
        if path == pgpass:
            _restrict(path)
    return service, pgpass


def _restrict(path):
    """Remove inherited access so only this Windows user can read the password file."""
    import os
    import subprocess
    if os.name != 'nt':
        return
    import csv
    output = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
    sid = next(csv.reader([output.strip()]))[1]
    subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', f'*{sid}:F'],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def files_exist(directory=None):
    target = Path(directory) if directory else _settings_dir()
    return (target / '.pg_service.conf').is_file() and (target / 'pgpass.conf').is_file()
