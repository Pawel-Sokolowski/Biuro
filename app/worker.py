"""Unattended shared-settings worker: python -m app.worker [--once]."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, time
import hashlib
from contextvars import copy_context
import json
import logging
import os
import signal
import threading
import time as clock
from zoneinfo import ZoneInfo

from app.config import get_dsn, get_schema
from app.db.dao_app_settings import get_setting, set_setting
from app.services.worker_settings import load_settings

logger = logging.getLogger(__name__)
JOBS = ('mail', 'reminders', 'invoices', 'enova', 'ksef_import')


def mail_job(stop=None):
    from app.email.email_storage import get_all_email_accounts
    from app.email.email_sync import sync_automatic_accounts
    from app.utils.system_mailbox import get_system_account
    accounts = [account for account in get_all_email_accounts() if account.get('aktywne')]
    system = get_system_account()
    if system and system.get('aktywne') and system.get('serwer_imap'):
        accounts.append(system)
    errors = sync_automatic_accounts(accounts, cancel_event=stop)
    if errors:
        raise RuntimeError(f'Nie zsynchronizowano {len(errors)} kont/folderów. Sprawdź połączenia poczty i wspólny klucz.')
    return {'accounts': len(accounts)}


def reminders_job():
    from app.services.email_reminders import run_reminders
    return {'sent': run_reminders()}


def invoices_job():
    from app.db.dao_conn import get_conn
    from app.invoices.ksef_submission import process_invoice_batch
    # Queue entries record the user, sender and reviewed recipients. Never select
    # all drafts or all issued invoices, and never resubmit an invoice to KSeF.
    with get_conn() as conn:
        rows = conn.execute("""SELECT id,invoice_email_actor_id FROM faktury
            WHERE invoice_email_status IN ('queued','failed_before_send')
              AND invoice_email_actor_id IS NOT NULL AND cancelled_at IS NULL
              AND (ksef_numer IS NOT NULL OR ksef_status IN ('wysylanie','oczekuje','do_sprawdzenia','przyjeta'))
              AND NOT EXISTS (SELECT 1 FROM faktury_kosz t WHERE t.faktura_id=faktury.id AND t.restored_at IS NULL)
            ORDER BY id LIMIT 50""").fetchall()
    sent, errors = 0, 0
    for row in rows:
        result = process_invoice_batch([row['id']], row['invoice_email_actor_id'], send=False)
        sent += sum(entry.get('delivery', {}).get('status') == 'sent'
                    for entry in result['accepted'] if entry.get('delivery'))
        errors += len(result['errors'])
    if errors:
        raise RuntimeError(f'Błędy {errors} wysyłek. Szczegóły przy fakturach; niepewne wysyłki nie są ponawiane.')
    return {'queued': len(rows), 'sent': sent}


def enova_job(now=None):
    from app.integrations.enova.connection_settings import load_connection
    from app.integrations.enova.document_counts import run_import
    now = now or datetime.now(ZoneInfo('Europe/Warsaw'))
    settings = load_settings()
    config = load_connection()
    day = now.date().isoformat()
    # Connection/rule changes are picked up by the existing importer next day;
    # failures do not consume this completion marker and retry at the interval.
    if not config['enabled'] or now.time().replace(tzinfo=None) < time.fromisoformat(settings['enova_time']):
        return {'mode': 'waiting'}
    if (get_setting('worker_enova_completed') == day
            and (not config.get('checking_enabled', False) or get_setting('worker_enova_checking_completed') == day)):
        return {'mode': 'checked_today'}
    results = run_import(config, apply=True, today=now.date())
    if config.get('checking_enabled', False):
        from app.integrations.enova.checking import run_checking_import
        checking = run_checking_import(config, apply=True, today=now.date())
        results.extend(checking)
    if any(not result['ok'] for result in results):
        raise RuntimeError('Import Enova nieukończony. Sprawdź audyt importów i połączenie SQL.')
    set_setting('worker_enova_completed', day)
    if config.get('checking_enabled', False):
        set_setting('worker_enova_checking_completed', day)
    return {'companies': len(config['sources']) if 'sources' in config else len(results)}


def ksef_import_job():
    from app.invoices.ksef_sync import is_configured, sync_month
    from app.utils.months import previous_month
    if not is_configured():
        return {'mode': 'not_configured'}
    month = datetime.now(ZoneInfo('Europe/Warsaw')).strftime('%Y-%m')
    for period in (previous_month(month), month):
        sync_month(period)
    return {'months': 2}


def run_job(name, stop=None):
    from app.utils.request_cache import request_scope
    with request_scope():
        return {'mail': lambda: mail_job(stop), 'reminders': reminders_job,
                'invoices': invoices_job, 'enova': enova_job, 'ksef_import': ksef_import_job}[name]()


@contextmanager
def leadership():
    """A dedicated session lock prevents two workers for the same database/schema."""
    import psycopg
    from psycopg import sql
    conn = psycopg.connect(get_dsn(), connect_timeout=10, autocommit=True)
    key = None
    acquired = False
    try:
        if get_schema():
            conn.execute(sql.SQL('SET search_path TO {},public').format(sql.Identifier(get_schema())))
        schema = conn.execute('SELECT current_schema()').fetchone()[0]
        key = int.from_bytes(hashlib.blake2b(f'worker:{schema}'.encode(), digest_size=8).digest(), 'big', signed=True)
        acquired = conn.execute('SELECT pg_try_advisory_lock(%s)', (key,)).fetchone()[0]
        if not acquired:
            raise RuntimeError('Worker tej bazy już działa.')
        yield conn
    finally:
        if acquired and not conn.closed:
            try:
                conn.execute('SELECT pg_advisory_unlock(%s)', (key,))
            except Exception:
                pass
        conn.close()


def serve(*, once=False, stop=None):
    stop = stop or threading.Event()
    running, last_started, statuses = {}, {}, {}
    with leadership() as leader, ThreadPoolExecutor(max_workers=len(JOBS)) as pool:
        while not stop.is_set():
            leader.execute('SELECT 1')  # Stop scheduling immediately if leadership disconnects.
            settings = load_settings()
            for name, future in list(running.items()):
                if not future.done():
                    continue
                try:
                    outcome = future.result()
                    statuses[name] = {'state': 'ok', 'result': outcome, 'finished_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
                except Exception as exc:
                    # Avoid logging arbitrary third-party exception strings/credentials.
                    logger.error('worker_job_failed job=%s error_type=%s', name, type(exc).__name__)
                    statuses[name] = {'state': 'failed', 'error_type': type(exc).__name__,
                                      'finished_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
                del running[name]
            for name in JOBS:
                if settings['enabled'] and settings[name] and name not in running:
                    due = name not in last_started or (not once and clock.monotonic() - last_started[name] >= settings[name + '_interval'])
                    if due:
                        last_started[name] = clock.monotonic()
                        statuses[name] = {'state': 'running'}
                        running[name] = pool.submit(copy_context().run, run_job, name, stop)
            set_setting('background_worker_heartbeat', json.dumps({
                'checked_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat(),
                'enabled': settings['enabled'], 'jobs': statuses}))
            if once and not running:
                return int(any(value['state'] == 'failed' for value in statuses.values()))
            stop.wait(1 if once else 5)
    return 0


def main(argv=None):
    import argparse
    from app.utils.env_config import load_environment, ensure_encryption_key
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Wykonaj jeden przebieg włączonych zadań.')
    args = parser.parse_args(argv)
    load_environment()
    ensure_encryption_key()
    from app.diagnostics import configure_logging
    configure_logging()
    # PDF generation needs Qt's font system, but never opens a window.
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(['biuro-worker'])
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        return serve(once=args.once, stop=stop)
    finally:
        from app.email.email_sync import close_sessions
        from app.db.dao_conn import close_pool
        close_sessions()
        close_pool()
        app.processEvents()


if __name__ == '__main__':
    raise SystemExit(main())
