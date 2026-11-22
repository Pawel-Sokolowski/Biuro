"""Unattended shared-settings worker: python -m app.worker [--once]."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, time, timedelta
import hashlib
from contextvars import copy_context
import json
import logging
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import uuid
import time as clock
from zoneinfo import ZoneInfo

if __name__ == '__main__':
    os.environ['BIO_WORKER_MODE'] = '1'

from app.config import get_dsn, get_schema
from app.db.dao_app_settings import get_setting, set_setting
from app.services.worker_settings import load_settings

logger = logging.getLogger(__name__)
JOBS = ('mail', 'reminders', 'invoices', 'enova', 'ezla', 'ksef_import', 'saldeo', 'platnik', 'platnik_gui', 'checking_refresh')


def _env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


# Jobs share a small executor to leave
# pool capacity for UI/API requests and avoids a startup connection storm.
WORKER_THREADS = _env_int('BIO_WORKER_THREADS', 4, 1, len(JOBS))
HEARTBEAT_SECONDS = _env_int('BIO_WORKER_HEARTBEAT_SECONDS', 10, 5, 60)
LEADER_CHECK_SECONDS = _env_int('BIO_WORKER_LEADER_CHECK_SECONDS', 15, 5, 60)


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


def reminders_job(stop=None):
    from app.services.email_reminders import run_reminders
    return {'sent': run_reminders(stop=stop)}


def invoices_job():
    from app.db.dao_conn import get_conn
    from app.invoices.ksef_submission import process_invoice_batch
    # Queue entries record the user, sender and reviewed recipients. Never select
    # all drafts or all issued invoices, and never resubmit an invoice to KSeF.
    with get_conn() as conn:
        rows = conn.execute("""WITH due AS (
            SELECT id FROM faktury WHERE invoice_email_status IN ('queued','failed_before_send')
              AND invoice_email_actor_id IS NOT NULL AND cancelled_at IS NULL
              AND (invoice_email_next_attempt_at IS NULL OR invoice_email_next_attempt_at <= now())
              AND (ksef_numer IS NOT NULL OR ksef_status IN ('wysylanie','oczekuje','do_sprawdzenia','przyjeta'))
              AND NOT EXISTS (SELECT 1 FROM faktury_kosz t WHERE t.faktura_id=faktury.id AND t.restored_at IS NULL)
            ORDER BY invoice_email_next_attempt_at NULLS FIRST, id LIMIT 50 FOR UPDATE SKIP LOCKED
        )
        UPDATE faktury f SET invoice_email_attempts=f.invoice_email_attempts+1,
            invoice_email_next_attempt_at=now()+make_interval(mins => LEAST(f.invoice_email_attempts+1,30))
        FROM due WHERE f.id=due.id RETURNING f.id,f.invoice_email_actor_id""").fetchall()
    sent, errors = 0, 0
    for row in rows:
        result = process_invoice_batch([row['id']], row['invoice_email_actor_id'], send=False)
        sent += sum(entry.get('delivery', {}).get('status') == 'sent'
                    for entry in result['accepted'] if entry.get('delivery'))
        errors += len(result['errors'])
    if errors:
        raise RuntimeError(f'Błędy {errors} wysyłek. Szczegóły przy fakturach; niepewne wysyłki nie są ponawiane.')
    return {'queued': len(rows), 'sent': sent}


def enova_job(stop=None, *, now=None):
    from app.services.enova_stage import run
    return run(stop, now=now)


def ksef_import_job():
    from app.invoices.ksef_sync import is_configured, sync_month
    from app.utils.months import previous_month
    if not is_configured():
        return {'mode': 'not_configured'}
    month = datetime.now(ZoneInfo('Europe/Warsaw')).strftime('%Y-%m')
    unmatched_by_month = {}
    for period in (previous_month(month), month):
        result = sync_month(period)
        unmatched_by_month[period] = len(result.get('niedopasowane', ()))
    return {'months': len(unmatched_by_month), 'unmatched_by_month': unmatched_by_month,
            'mode': 'partial' if any(unmatched_by_month.values()) else 'completed'}


def ezla_job(stop=None,now=None):
    from app.integrations.ezla.sync import run_sync
    now = now or datetime.now(ZoneInfo('Europe/Warsaw'))
    settings = load_settings()
    monday = now.date() - timedelta(days=now.weekday())
    if now < datetime.combine(monday, time.fromisoformat(settings['ezla_time']), tzinfo=now.tzinfo):
        return {'mode':'waiting','companies':0,'failed':0}
    week = monday.isoformat()
    if get_setting('worker_ezla_completed_week')==week:
        return {'mode':'checked_this_week','companies':0,'failed':0}
    result = run_sync(stop=stop)
    if result.get('mode') in ('disabled','busy','cancelled') or (stop is not None and stop.is_set()):
        return result
    if any(row.get('state')=='busy' for row in result.get('results',[])):
        return {**result,'mode':'busy'}
    if result['failed']:
        raise RuntimeError('Nie pobrano e-ZLA dla części firm. Szczegóły w raporcie e-ZLA.')
    set_setting('worker_ezla_completed_week',week)
    return {**{key: result[key] for key in ('companies', 'failed')}, 'mode': result.get('mode', 'completed')}


def saldeo_job(stop=None):
    from app.integrations.saldeo_web.sync import run
    return run(stop)


def platnik_job():
    from app.integrations.platnik.holidays import worker_job as holidays_job, PlatnikSqlError
    from app.integrations.platnik.declarations import worker_job as declarations_job
    results, failed = {}, []
    for name, job in (('holidays', holidays_job), ('declarations', declarations_job)):
        try:
            results[name] = job()
        except Exception as error:
            failed.append(name + ': ' + (str(error) if isinstance(error, (PlatnikSqlError, ValueError)) else 'Odczyt nie powiódł się'))
    if failed:
        raise RuntimeError('Nieukończone odczyty Płatnika: ' + ', '.join(failed))
    if any(run.get('insurance_registry_errors') or run.get('declaration_link_errors')
           for run in results.get('declarations', {}).values()):
        results['mode'] = 'partial'
    return results


def platnik_gui_schedule(now, at, result):
    day = now.date()
    if (result or {}).get('date') == day.isoformat():
        day += timedelta(days=1)
    return datetime.combine(day, time.fromisoformat(at), now.tzinfo)


def platnik_gui_job():
    from app.services.platnik_gui import enqueue_daily

    now = datetime.now(ZoneInfo('Europe/Warsaw'))
    settings = load_settings()
    if not settings['enabled'] or not settings['platnik_gui']:
        return {'mode': 'disabled'}
    if now < platnik_gui_schedule(now, settings['platnik_gui_time'], None):
        return {'mode': 'waiting'}
    return enqueue_daily(now.date())


def checking_refresh_job(stop=None):
    from app.services.checking_refresh import process_next, purge_finished
    try:
        purge_finished()
    except Exception:
        pass  # Sprzątanie historii nie może blokować odczytów.
    result = process_next(stop)
    if result.get('state') in ('failed','partial'):
        raise RuntimeError('Odczyt zleconych danych nieukończony. Sprawdź wynik pobierania w Sprawdzaniu.')
    return result


def run_job(name, stop=None):
    from app.utils.request_cache import request_scope
    with request_scope():
        return {'mail': lambda: mail_job(stop), 'reminders': lambda: reminders_job(stop),
                'invoices': invoices_job, 'enova': lambda: enova_job(stop), 'ezla': lambda: ezla_job(stop), 'ksef_import': ksef_import_job, 'saldeo': lambda: saldeo_job(stop), 'platnik': platnik_job,
                'platnik_gui': platnik_gui_job, 'checking_refresh': lambda: checking_refresh_job(stop)}[name]()


@contextmanager
def leadership():
    """A dedicated session lock prevents two workers for the same database/schema."""
    import psycopg
    from psycopg import sql
    conn = psycopg.connect(
        get_dsn(),
        connect_timeout=10,
        autocommit=True,
        application_name='biuro-worker-leader',
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )
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
    events = json.loads(get_setting('background_worker_events') or '[]')[-200:]
    with leadership() as leader, ThreadPoolExecutor(max_workers=WORKER_THREADS, thread_name_prefix='biuro-job') as pool:
        from app.services.checking_refresh import recover_interrupted
        recover_interrupted()
        from app.version import __version__
        from app.services.worker_setup import worker_env_path
        instance = {'id':uuid.uuid4().hex,'host':socket.gethostname(),'pid':os.getpid(),
                    'version':__version__,'executable':str(Path(sys.executable).resolve()),
                    'config_file':str(worker_env_path()),
                    'started_at':datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
        last_leader_check = 0.0
        last_heartbeat_write = 0.0
        while not stop.is_set():
            now_monotonic = clock.monotonic()
            if now_monotonic - last_leader_check >= LEADER_CHECK_SECONDS:
                leader.execute('SELECT 1')  # Detect a lost leadership session before scheduling more work.
                last_leader_check = now_monotonic
            settings = load_settings()
            settings_now = datetime.now(ZoneInfo('Europe/Warsaw'))
            settings_checked_at = settings_now.isoformat()
            events_changed = False
            for name, future in list(running.items()):
                if not future.done():
                    continue
                started_at = statuses[name].get('started_at')
                try:
                    outcome = future.result()
                    mode = outcome.get('mode') if isinstance(outcome, dict) else None
                    state = mode if mode in ('partial', 'failed') else 'ok'
                    if mode in ('waiting', 'retry_waiting', 'checked_today', 'checked_this_week', 'disabled',
                                'not_configured', 'busy', 'idle', 'empty', 'cancelled'):
                        state = 'skipped'
                    statuses[name] = {'state': state, 'result': outcome,
                                      'finished_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
                except Exception as exc:
                    # Avoid logging arbitrary third-party exception strings/credentials.
                    logger.error('worker_job_failed job=%s error_type=%s', name, type(exc).__name__)
                    statuses[name] = {'state': 'failed', 'error_type': type(exc).__name__,
                                      'finished_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
                statuses[name]['started_at'] = started_at
                last_started[name] = clock.monotonic()  # A full interval after completion, including failures.
                events.append({'at': statuses[name]['finished_at'], 'job': name, 'state': statuses[name]['state'],
                               'error_type': statuses[name].get('error_type'),
                               'mode': (statuses[name].get('result') or {}).get('mode')
                               if isinstance(statuses[name].get('result'), dict) else None})
                events_changed = True
                del running[name]
            gui_scheduled_at = None
            if settings['enabled'] and settings['platnik_gui']:
                gui_status = statuses.setdefault('platnik_gui', {'state': 'skipped', 'result': {'mode': 'waiting'}})
                gui_scheduled_at = platnik_gui_schedule(settings_now, settings['platnik_gui_time'], gui_status.get('result'))
            for name in JOBS:
                if settings['enabled'] and settings[name] and name not in running:
                    due = name not in last_started or (not once and clock.monotonic() - last_started[name] >= (30 if name=='enova' else settings[name + '_interval']))
                    if name == 'platnik_gui':
                        due = due and settings_now >= gui_scheduled_at
                    if due:
                        last_started[name] = clock.monotonic()
                        stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
                        statuses[name] = {'state': 'running', 'started_at': stamp}
                        events.append({'at': stamp, 'job': name, 'state': 'started'})
                        events_changed = True
                        running[name] = pool.submit(copy_context().run, run_job, name, stop)
            if events_changed:
                events = events[-200:]
                set_setting('background_worker_events', json.dumps(events))
            for name, status in statuses.items():
                if status['state'] != 'running' and settings['enabled'] and settings[name]:
                    delay = max(0, (30 if name=='enova' else settings[name + '_interval'])
                                - (clock.monotonic() - last_started.get(name, 0)))
                    now = datetime.now(ZoneInfo('Europe/Warsaw'))
                    next_run = now+timedelta(seconds=delay)
                    if name == 'platnik_gui' and gui_scheduled_at > now:
                        next_run = gui_scheduled_at
                    if name=='ezla' and (status.get('result') or {}).get('mode') in ('waiting','checked_this_week'):
                        at = time.fromisoformat(settings['ezla_time'])
                        next_run = now.replace(hour=at.hour,minute=at.minute,second=0,microsecond=0)+timedelta(days=(-now.weekday())%7)
                        if next_run<=now:
                            next_run += timedelta(days=7)
                    status['next_run_at'] = next_run.isoformat()
                else:
                    status.pop('next_run_at', None)
            now_monotonic = clock.monotonic()
            if (now_monotonic - last_heartbeat_write >= HEARTBEAT_SECONDS or
                    events_changed or (once and not running)):
                set_setting('background_worker_heartbeat', json.dumps({
                    'checked_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat(),
                    'instance':instance,'settings_checked_at':settings_checked_at,
                    'enabled': settings['enabled'], 'jobs': statuses}))
                last_heartbeat_write = now_monotonic
            if once and not running:
                return int(any(value['state'] in ('failed', 'partial') for value in statuses.values()))
            stop.wait(1 if once else 5)
    return 0


def main(argv=None):
    import argparse
    from app.utils.env_config import load_environment, ensure_encryption_key
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Wykonaj jeden przebieg włączonych zadań.')
    parser.add_argument('--check-config', action='store_true', help='Sprawdź konfigurację bez uruchamiania zadań.')
    args = parser.parse_args(argv)
    load_environment()
    from app.diagnostics import configure_logging
    configure_logging()
    app = None
    api_server = None
    try:
        # Configure logging before credential checks: the packaged EXE has no console.
        ensure_encryption_key()
        if args.check_config:
            from app.services.worker_setup import preflight
            preflight()
            logger.info('worker_configuration_ready')
            return 0
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(['biuro-worker'])
        stop = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
        if not args.once:
            from app.remote.http_server import start_configured
            api_server = start_configured()
        return serve(once=args.once, stop=stop)
    except Exception:
        logger.exception('worker_startup_or_runtime_failed')
        return 1
    finally:
        if api_server is not None:
            api_server.shutdown()
            api_server.server_close()
            api_server.office_service.close()
        if app is not None:
            from app.email.email_sync import close_sessions
            from app.db.dao_conn import close_pool
            close_sessions()
            close_pool()
            app.processEvents()


if __name__ == '__main__':
    raise SystemExit(main())
