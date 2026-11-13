"""Local worker provisioning. No jobs, migrations or outgoing messages are executed."""
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import quote


def worker_env_path():
    from app.utils.env_config import is_frozen, PROJECT_ROOT
    base = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'Biuro' if is_frozen() else PROJECT_ROOT
    return base / 'worker.env'


def database_url(host, port, database, username, password, sslmode='prefer'):
    host = host.strip()
    if not host or any(c in host for c in '/@?#\\\r\n'):
        raise ValueError('Podaj adres PostgreSQL, bez protokołu i nazwy bazy.')
    if not database.strip() or not username.strip() or not password:
        raise ValueError('Uzupełnij bazę, użytkownika i hasło PostgreSQL.')
    if sslmode not in ('prefer', 'require', 'verify-full') or not 1 <= int(port) <= 65535:
        raise ValueError('Nieprawidłowy port lub tryb TLS.')
    host = host.strip('[]')
    if ':' in host:
        host = '[' + host + ']'
    return (f'postgresql://{quote(username.strip(), safe="")}:{quote(password, safe="")}@'
            f'{host}:{int(port)}/{quote(database.strip(), safe="")}?sslmode={sslmode}')


def probe_database(dsn, key):
    from cryptography.fernet import Fernet
    import psycopg
    Fernet(key.strip().encode())
    with psycopg.connect(dsn, connect_timeout=10, options='-c statement_timeout=10000') as conn:
        if not conn.execute("SELECT to_regclass('app_settings') IS NOT NULL").fetchone()[0]:
            raise ValueError('To nie jest przygotowana baza Biuro. Wybierz właściwą bazę.')
        from app.db.schema import SCHEMA_VERSION
        row = conn.execute("SELECT wartosc FROM app_settings WHERE klucz='schema_hash'").fetchone()
        if not row or row[0] != SCHEMA_VERSION:
            raise ValueError('Baza wymaga aktualizacji. Wykonaj ją w Biuro przed instalacją workera.')
        _check_tokens(conn, key.strip())
    return True


def save_local_config(dsn, key):
    """Persist to a dedicated file, restricted to this Windows user and SYSTEM."""
    from cryptography.fernet import Fernet
    from dotenv import set_key
    Fernet(key.strip().encode())
    path = worker_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
        temporary.chmod(0o600)
        if os.name == 'nt':
            # whoami provides the SID without depending on localized account names.
            import csv
            output = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True,
                                             creationflags=subprocess.CREATE_NO_WINDOW)
            sid = next(csv.reader([output.strip()]))[1]
            subprocess.run(['icacls', str(temporary), '/inheritance:r', '/grant:r',
                            f'*{sid}:F', '*S-1-5-18:F'], check=True, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        set_key(str(temporary), 'DB_URL', dsn)
        set_key(str(temporary), 'BIO_ENCRYPTION_KEY', key.strip())
        # set_key replaces the file; reapply ACL to the resulting file before publishing it.
        if os.name == 'nt':
            subprocess.run(['icacls', str(temporary), '/inheritance:r', '/grant:r',
                            f'*{sid}:F', '*S-1-5-18:F'], check=True, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    os.environ['DB_URL'] = dsn
    os.environ['BIO_ENCRYPTION_KEY'] = key.strip()
    from app.db.dao_conn import close_all_pools
    close_all_pools()


def check_saved_credentials():
    """Verify decryption only; never connect to SMTP, IMAP, Enova or ZUS."""
    from app.db.dao_conn import get_conn
    with get_conn() as conn:
        return _check_tokens(conn, os.environ.get('BIO_ENCRYPTION_KEY', ''))


class CredentialKeyError(ValueError):
    """Safe, actionable diagnostics containing labels only, never ciphertext or keys."""
    def __init__(self, failed, checked, fingerprint):
        self.failed = tuple(failed)
        self.checked = checked
        self.fingerprint = fingerprint
        labels = '; '.join(failed[:12])
        if len(failed) > 12:
            labels += f'; i kolejne {len(failed)-12}'
        super().__init__(f'Klucz nie odszyfrowuje {len(failed)} z {checked} zapisanych sekretów. '
                         f'Odcisk sprawdzanego klucza: {fingerprint}. Wymagają sprawdzenia: {labels}. '
                         'Użyj dotychczasowego klucza lub ponownie wpisz hasła tych kont/usług. '
                         'Zapisanie nowego klucza nie szyfruje ponownie istniejących danych.')


def _check_tokens(conn, key):
    import hashlib
    import json
    from cryptography.fernet import Fernet, InvalidToken
    cipher = Fernet(key.encode())
    tokens = []
    for row in conn.execute(
            "SELECT id,email,haslo_enc FROM konta_email WHERE NULLIF(haslo_enc,'') IS NOT NULL").fetchall():
        tokens.append((f'Poczta #{row[0]} ({row[1]})', row[2]))
    for row in conn.execute("SELECT klucz,wartosc FROM app_settings WHERE klucz IN ('enova_connection','ezla_service','saldeo_web','platnik_connection') OR wartosc LIKE 'gAAAAA%'").fetchall():
        if row[1].startswith('gAAAAA'):
            tokens.append((f'Ustawienie {row[0]}', row[1]))
        else:
            config = json.loads(row[1] or '{}')
            for name in ('password_enc', 'zip_password_enc'):
                if config.get(name):
                    tokens.append((f'{row[0]} / {name}', config[name]))
    failed = []
    for label, token in tokens:
        try:
            cipher.decrypt(token.encode())
        except (InvalidToken, ValueError, TypeError, AttributeError):
            failed.append(label)
    if failed:
        raise CredentialKeyError(failed, len(tokens), hashlib.sha256(key.encode()).hexdigest()[:16])
    return len(tokens)


def preflight():
    from app.config import get_dsn
    from app.utils.env_config import ensure_encryption_key
    probe_database(get_dsn(), ensure_encryption_key())
    return check_saved_credentials()
