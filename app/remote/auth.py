"""Hashed, expiring, revocable sessions tied to the current employee password."""
from datetime import timedelta
import hashlib
import secrets

from app.db.dao_conn import get_conn

DDL = '''
CREATE TABLE IF NOT EXISTS office_api_sessions (
    token_hash TEXT PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES pracownicy(id) ON DELETE CASCADE,
    password_stamp TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS office_api_session_employee ON office_api_sessions(employee_id);
CREATE TABLE IF NOT EXISTS office_api_login_limits (
    subject TEXT PRIMARY KEY, attempts INTEGER NOT NULL, window_start TIMESTAMPTZ NOT NULL
);
'''


def initialize():
    with get_conn() as conn:
        conn.execute(DDL)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _attempt(conn, subject, limit):
    row = conn.execute('''INSERT INTO office_api_login_limits(subject,attempts,window_start)
        VALUES (%s,1,now()) ON CONFLICT(subject) DO UPDATE SET
        attempts=CASE WHEN office_api_login_limits.window_start < now()-interval '15 minutes'
            THEN 1 ELSE office_api_login_limits.attempts+1 END,
        window_start=CASE WHEN office_api_login_limits.window_start < now()-interval '15 minutes'
            THEN now() ELSE office_api_login_limits.window_start END RETURNING attempts''', (digest(subject),)).fetchone()
    return row[0] <= limit


def login(email, password, peer, remember=False):
    import bcrypt
    if not isinstance(email, str) or not isinstance(password, str) or len(email) > 320 or len(password.encode()) > 72:
        raise PermissionError('Nieprawidłowe dane logowania.')
    email = email.strip().casefold()
    with get_conn() as conn:
        permitted_ip = _attempt(conn, 'ip:' + peer, 100)
        permitted_account = _attempt(conn, 'account:' + email, 10) if permitted_ip else False
    if not permitted_ip or not permitted_account:
        raise PermissionError('Zbyt wiele prób logowania. Spróbuj ponownie później.')
    with get_conn() as conn:
        employee = conn.execute('SELECT id,haslo_hash,aktywny FROM pracownicy WHERE lower(email)=%s', (email,)).fetchone()
    # Equal-cost verification for unknown accounts.
    stored = employee['haslo_hash'] if employee else '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxUpi01HMYK1JsAtxSw81bSyjXq'
    try:
        valid = bcrypt.checkpw(password.encode(), stored.encode())
    except (ValueError, TypeError):
        valid = False
    if not employee or not employee['aktywny'] or not valid:
        raise PermissionError('Nieprawidłowy e-mail lub hasło.')
    token = secrets.token_urlsafe(32)
    lifetime = timedelta(days=14) if remember else timedelta(hours=12)
    with get_conn() as conn:
        conn.execute('DELETE FROM office_api_sessions WHERE expires_at<=now()')
        conn.execute('DELETE FROM office_api_login_limits WHERE window_start<now()-interval \'1 day\'')
        conn.execute('DELETE FROM office_api_login_limits WHERE subject=%s', (digest('account:' + email),))
        conn.execute('INSERT INTO office_api_sessions(token_hash,employee_id,password_stamp,expires_at) VALUES (%s,%s,%s,now()+%s)',
                     (digest(token),employee['id'],digest(stored),lifetime))
    return {'token':token,'employee_id':employee['id'],'expires_in':int(lifetime.total_seconds())}


def authenticate(token):
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise PermissionError('Sesja wygasła. Zaloguj się ponownie.')
    with get_conn() as conn:
        row = conn.execute('''SELECT p.id,p.haslo_hash,s.password_stamp FROM office_api_sessions s
            JOIN pracownicy p ON p.id=s.employee_id WHERE s.token_hash=%s AND s.expires_at>now()
            AND COALESCE(p.aktywny,0)=1''', (digest(token),)).fetchone()
    if not row or not secrets.compare_digest(row['password_stamp'],digest(row['haslo_hash'])):
        raise PermissionError('Sesja wygasła. Zaloguj się ponownie.')
    return row['id']


def logout(token):
    with get_conn() as conn:
        conn.execute('DELETE FROM office_api_sessions WHERE token_hash=%s', (digest(token),))


def ui_authorization(employee_id):
    """Login-time UI hints only; business operations still authorize on the server."""
    from app.remote.state import service_scope
    from app.security.authorization import is_administrator,get_permissions,allowed_tabs
    with service_scope(employee_id):
        return {'administrator':is_administrator(employee_id),
                'permissions':get_permissions(employee_id),'tabs':allowed_tabs(employee_id)}
