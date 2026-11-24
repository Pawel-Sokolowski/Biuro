"""Entry point for the Windows process scheduled in the interactive user session."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import subprocess
import sys
from time import sleep

from app.integrations.platnik.gui_desktop import describe, download_confirmation, grid_rows, interactive_desktop, main_window
from app.integrations.platnik.gui_evidence import ERRORS, OPTIONAL_COLUMNS, REQUIRED_COLUMNS, STATUSES, ReadError, collect, label

MODULE = 'app.integrations.platnik.gui_agent'
LOGGER = logging.getLogger(__name__)
JOB_TIMEOUT = 75
POLL_SECONDS = 5


def load_view(config, backend, required, optional):
    grid, columns = config['grid'], config['columns']
    grid_keys = {'auto_id', 'control_type', 'class_name'} if backend == 'uia' else {'control_id', 'class_name'}
    if not isinstance(grid, dict) or not grid or grid.keys() - grid_keys:
        raise ValueError('Nieprawidłowe kryteria kontrolki rejestru.')
    for key, value in grid.items():
        if (key == 'control_id' and (type(value) is not int or value <= 0)
                or key != 'control_id' and (not isinstance(value, str) or not value.strip())):
            raise ValueError('Nieprawidłowe kryteria kontrolki rejestru.')
    if (not isinstance(columns, dict) or not required <= columns.keys()
            or columns.keys() - (required | optional)
            or any(not isinstance(field, dict) or field.keys() != {'index', 'header'}
                   or type(field['index']) is not int or not 0 <= field['index'] < 100
                   or not isinstance(field['header'], str) or not label(field['header'])
                   for field in columns.values())
            or len({field['index'] for field in columns.values()}) != len(columns)):
        raise ValueError('Profil wymaga odrębnych kolumn identyfikacji dokumentu, zestawu i potwierdzenia.')
    tabs = config.get('tabs', [])
    if (not isinstance(tabs, list) or len(tabs) > 6
            or any(not isinstance(value, str) or not value.strip() for value in tabs)
            or (tabs and backend != 'uia')):
        raise ValueError('Profil obsługuje wyłącznie zakładki UIA wskazane przez AutomationId.')
    return {'grid': grid, 'columns': columns, 'tabs': tabs}


def load_config(path, *, full=True):
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise ValueError('Nieprawidłowy profil Płatnika.')
    executable = Path(config['executable'])
    if not executable.is_absolute() or executable.suffix.lower() != '.exe' or not executable.is_file():
        raise ValueError('Profil wymaga pełnej ścieżki do pliku Płatnika (.exe).')
    backend = config.get('backend', 'uia')
    if backend not in ('uia', 'win32'):
        raise ValueError('Backend Płatnika musi być uia albo win32.')
    window = config['window']
    if not isinstance(window, dict) or set(window) - {'title', 'class_name'} or not all(
            isinstance(value, str) and value.strip() for value in window.values()) or not window:
        raise ValueError('Profil wymaga dokładnego tytułu lub klasy okna Płatnika.')
    result = {'executable': executable.resolve(), 'backend': backend, 'window': window}
    if not full:
        return result
    view = load_view(config, backend, REQUIRED_COLUMNS, OPTIONAL_COLUMNS)
    statuses = config['status_labels']
    if (not isinstance(statuses, dict) or not statuses
            or any(not label(name) or not isinstance(status, str) or status not in STATUSES
                   for name, status in statuses.items())):
        raise ValueError('Skonfiguruj dokładne etykiety stanów w profilu.')
    statuses_normalized = {label(name): status for name, status in statuses.items()}
    if len(statuses_normalized) != len(statuses) or ('sent' in statuses.values() and 'sent_at' not in view['columns']):
        raise ValueError('Powtórzone etykiety stanów lub brak kolumny daty wysyłki.')
    service = config.get('database_service', 'biuro_platnik_agent')
    schema = config.get('database_schema', 'public')
    if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', value)
           for value in (service, schema)):
        raise ValueError('Nieprawidłowa nazwa usługi lub schematu PostgreSQL.')
    result.update(view, statuses=statuses_normalized, service=service, schema=schema)
    if 'confirmation_download' in config:
        download = config['confirmation_download']
        if ('sent' not in statuses_normalized.values()
                or not {'accepted', 'rejected'} & set(statuses_normalized.values())
                or not isinstance(download, dict)
                or set(download) - {'tabs', 'grid', 'columns'}
                or not {'grid', 'columns'} <= download.keys()
                or (backend == 'uia' and bool(view['tabs']) != bool(download.get('tabs')))):
            raise ValueError('Skonfiguruj widok Zestawy wysłane i powrót do rejestru deklaracji.')
        result['confirmation_download'] = load_view(download, backend, {'set_id'}, {'receipt'})
    return result


def read_job(config, job):
    window = main_window(config)
    nip = job['nip']
    period = job['period']
    statuses = config['statuses']
    findings = collect(grid_rows(window, config, nip=nip), nip, period, statuses)
    download = config.get('confirmation_download')
    if download:
        pending = dict.fromkeys((item['set_id'], item['receipt']) for item in findings
                                if item['status'] == 'sent' and item['receipt'])
        for set_id, receipt in pending:
            if not any(item['set_id'] == set_id and item['receipt'] == receipt and item['status'] == 'sent'
                       for item in findings):
                continue
            download_confirmation(window, {**download, 'backend': config['backend']}, set_id, receipt)
            findings = collect(grid_rows(window, config, nip=nip), nip, period, statuses)
            if not any(item['set_id'] == set_id and item['receipt'] == receipt
                       and item['status'] in ('accepted', 'rejected') for item in findings):
                raise ReadError('ReadFailed')
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    return findings


def bounded_read(path, job):
    python = Path(sys.executable)
    console = python.with_name('python.exe') if python.name.lower() == 'pythonw.exe' else python
    try:
        process = subprocess.run(
            [str(console), '-m', MODULE, 'read-job', '--config', str(path)],
            input=json.dumps(job), text=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=JOB_TIMEOUT, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        return {'error': 'Timeout'}
    except (subprocess.CalledProcessError, OSError, ValueError):
        return {'error': 'ReadFailed'}


def connect(config):
    import psycopg
    return psycopg.connect(service=config['service'], connect_timeout=5, autocommit=True,
                           application_name='biuro-platnik-gui',
                           options='-c statement_timeout=5000')


def run(config, path, *, once=False):
    from psycopg import Error, sql
    from psycopg.types.json import Jsonb

    schema = sql.Identifier(config['schema'])
    claim_query = sql.SQL('SELECT * FROM {}.platnik_gui_claim()').format(schema)
    finish_query = sql.SQL('SELECT {}.platnik_gui_finish(%s, %s, %s, %s)').format(schema)
    connection = None
    try:
        while True:
            try:
                if interactive_desktop():
                    if connection is None or connection.closed:
                        connection = connect(config)
                    raw = connection.execute(claim_query).fetchone()
                    if raw:
                        job_id, lease, nip, period = raw
                        outcome = bounded_read(path, {'nip': nip, 'period': period})
                        error = outcome.get('error')
                        findings = outcome.get('findings', [])
                        saved = connection.execute(finish_query, (job_id, lease, Jsonb(findings), error)).fetchone()[0]
                        LOGGER.info('zadanie=%s zapisano=%s stan=%s', job_id, saved, error or 'completed')
                if once:
                    return
            except Error as exc:
                if connection is not None:
                    connection.close()
                    connection = None
                LOGGER.warning('Kolejka PostgreSQL niedostępna (%s).', type(exc).__name__)
                if once:
                    raise ReadError('ReadFailed') from None
            sleep(POLL_SECONDS)
    finally:
        if connection is not None:
            connection.close()


def default_profile():
    """Where the installed application keeps the agent profile.

    Biuro.exe --platnik-agent runs without --config, so the profile sits beside the
    worker's own data and is edited from the Płatnik settings page.
    """
    base = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'Biuro'
    return base / 'platnik-agent.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', default='run', choices=('run', 'once', 'probe', 'describe', 'read-job'))
    parser.add_argument('--config', type=Path, default=None,
                        help='Profil agenta. Domyślnie katalog danych Biuro: platnik-agent.json')
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        raise SystemExit('Agent Płatnika wymaga Windows.')
    path = (args.config or default_profile()).resolve()
    if not path.is_file():
        raise SystemExit('Brak profilu agenta Płatnika: ' + str(path) + '. Uzupełnij go w Ustawieniach → Płatnik.')
    try:
        config = load_config(path, full=args.mode != 'describe')
        if args.mode == 'describe':
            print(json.dumps(describe(main_window(config)), ensure_ascii=True, indent=2))
        elif args.mode == 'probe':
            import pywinauto  # noqa: F401  Loaded on the agent VM to check the Windows runtime.
            from psycopg import sql
            schema = sql.Identifier(config['schema']).as_string()
            with connect(config) as conn:
                available = conn.execute("""
                    SELECT has_function_privilege(current_user, %s, 'EXECUTE'),
                           has_function_privilege(current_user, %s, 'EXECUTE'),
                           has_table_privilege(current_user, %s, 'SELECT,INSERT,UPDATE,DELETE')
                """, (f'{schema}.platnik_gui_claim()',
                      f'{schema}.platnik_gui_finish(uuid,uuid,jsonb,text)',
                      f'{schema}.platnik_gui_checks')).fetchone()
            if not all(available[:2]) or available[2]:
                raise ValueError('Sprawdź uprawnienia dedykowanego konta agenta.')
            print('Połączenie z kolejką działa. Pulpit: ' + ('aktywny.' if interactive_desktop() else 'niedostępny.'))
        elif args.mode == 'read-job':
            print(json.dumps({'findings': read_job(config, json.load(sys.stdin))}, ensure_ascii=True))
        else:
            handler = RotatingFileHandler(path.parent / 'agent.log', maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
            logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
            run(config, path, once=args.mode == 'once')
    except ReadError as exc:
        if args.mode == 'read-job':
            print(json.dumps({'error': exc.code}))
        else:
            raise SystemExit(ERRORS[exc.code]) from None
    except Exception:
        if args.mode == 'read-job':
            print(json.dumps({'error': 'ReadFailed'}))
        else:
            # Profile paths and credentials may appear in native error details.
            raise SystemExit('Sprawdź profil agenta, uprawnienia i połączenie z PostgreSQL.') from None


if __name__ == '__main__':
    main()
