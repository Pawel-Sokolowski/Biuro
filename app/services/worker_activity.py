"""Bounded, committed progress events visible while an import transaction is running."""
from contextlib import contextmanager
from datetime import datetime
import json
import logging
import re
from zoneinfo import ZoneInfo
from app.db.dao_conn import get_activity_conn

KEY = 'background_worker_activity'
LIMIT = 500
STAGES = {
    'downloading': 'Pobieranie e-ZLA', 'saving': 'Zapisywanie raportów e-ZLA',
    'downloaded': 'Pobrano e-ZLA', 'reading': 'Odczytywanie danych',
    'read': 'Odczytano dane', 'saved': 'Zapisano dane', 'failed': 'Błąd odczytu',
    'busy': 'Pominięto — odczyt już trwa', 'cancelled': 'Przerwano',
    'pause': 'Przerwa między firmami',
}
logger = logging.getLogger(__name__)


def record(job, stage, *, company_id=None, count=None, error_type=None):
    # Only structured, allowlisted information; never exception strings, SQL, NIP or report contents.
    if job not in ('ezla', 'enova', 'platnik', 'checking_refresh') or stage not in STAGES:
        raise ValueError('Nieprawidłowe zdarzenie postępu workera.')
    event = {'at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat(), 'job': job, 'stage': stage}
    if type(company_id) is int:
        event['company_id'] = company_id
    if type(count) is int and count >= 0:
        event['count'] = count
    if error_type and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', error_type):
        event['error_type'] = error_type
    logger.info('worker_activity job=%s stage=%s company_id=%s count=%s', job, stage, company_id, count)
    try:
        # Separate pool: jobs may hold every data/coordination slot during remote requests.
        # This transaction commits immediately, independently of the import transaction.
        with get_activity_conn() as conn:
            conn.execute("SET LOCAL lock_timeout = '1s'")
            conn.execute('SELECT pg_advisory_xact_lock(748223)')
            row = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (KEY,)).fetchone()
            events = json.loads(row[0]) if row else []
            events = (events + [event])[-LIMIT:]
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                         'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                         (KEY, json.dumps(events)))
    except Exception:
        # Monitoring failure must not turn a successful download into a failed import.
        logger.warning('worker_activity_unavailable')


@contextmanager
def batch(job, company_ids):
    """Report success only after the caller's write transaction exits."""
    ids = tuple(company_ids)
    for fid in ids:
        record(job, 'reading', company_id=fid)
    try:
        yield
    except Exception as error:
        for fid in ids:
            record(job, 'failed', company_id=fid, error_type=type(error).__name__)
        raise
    else:
        for fid in ids:
            record(job, 'saved', company_id=fid)
