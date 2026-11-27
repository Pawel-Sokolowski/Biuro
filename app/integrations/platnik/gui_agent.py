"""Płatnik GUI agent for a manual run on the HR workstation."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import subprocess
import sys

from app.integrations.platnik.gui_desktop import (describe, download_confirmation, grid_rows,
                                                   interactive_desktop, main_window, register)
from app.integrations.platnik.gui_diagnostic_capture import capture_wizard_walkthrough, discover_executable
from app.integrations.platnik.gui_evidence import ERRORS, OPTIONAL_COLUMNS, REQUIRED_COLUMNS, STATUSES, ReadError, collect, label

MODULE = 'app.integrations.platnik.gui_agent'
LOGGER = logging.getLogger(__name__)
JOB_TIMEOUT = 75


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
    return validate_config(json.loads(path.read_text(encoding='utf-8-sig')), full=full)


def validate_config(config, *, full=True):
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
    if config.get('launcher'):
        launcher = Path(config['launcher'])
        if not launcher.is_absolute() or launcher.suffix.lower() != '.exe' or not launcher.is_file():
            raise ValueError('Podaj pełną ścieżkę do programu uruchamiającego Płatnika (.exe).')
        result['launcher'] = launcher.resolve()
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
    command = ([str(console), '--platnik-agent', 'read-job', '--config', str(path)]
               if getattr(sys, 'frozen', False)
               else [str(console), '-m', MODULE, 'read-job', '--config', str(path)])
    try:
        process = subprocess.run(
            command,
            input=json.dumps(job), text=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=JOB_TIMEOUT, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        outcome = json.loads(process.stdout)
        if (not isinstance(outcome, dict)
                or not (set(outcome) == {'error'} and isinstance(outcome['error'], str)
                        and outcome['error'] in ERRORS
                        or set(outcome) == {'findings'} and isinstance(outcome['findings'], list))):
            return {'error': 'ReadFailed'}
        return outcome
    except subprocess.TimeoutExpired:
        return {'error': 'Timeout'}
    except (subprocess.CalledProcessError, OSError, ValueError):
        return {'error': 'ReadFailed'}


def connect(config):
    import psycopg
    return psycopg.connect(service=config['service'], connect_timeout=5, autocommit=True,
                           application_name='biuro-platnik-gui',
                           options='-c statement_timeout=5000')


def queue_target(connection, schema=None):
    """Identify the active cluster, database and schema across the two connections."""
    return list(connection.execute("""
        SELECT current_database(), COALESCE(%s, current_schema()::text),
               extract(epoch FROM pg_postmaster_start_time())::text
    """, (schema,)).fetchone())


def local_queue_target(path=None):
    config = load_config(Path(path) if path is not None else default_profile())
    with connect(config) as connection:
        return queue_target(connection, config['schema'])


def check_queue_access(config):
    from psycopg import sql
    schema = sql.Identifier(config['schema']).as_string()
    with connect(config) as connection:
        available = connection.execute("""
            SELECT has_function_privilege(current_user, %s, 'EXECUTE'),
                   has_function_privilege(current_user, %s, 'EXECUTE'),
                   has_table_privilege(current_user, %s, 'SELECT,INSERT,UPDATE,DELETE')
        """, (f'{schema}.platnik_gui_claim()',
              f'{schema}.platnik_gui_finish(uuid,uuid,jsonb,text)',
              f'{schema}.platnik_gui_checks')).fetchone()
    if not all(available[:2]) or available[2]:
        raise ValueError('Sprawdź uprawnienia dedykowanego konta agenta.')
    return True


def require_interactive_desktop():
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    return True


def diagnose(path=None, *, progress=None):
    """Check the agent setup without claiming jobs or reading/importing documents."""
    if sys.platform != 'win32':
        raise ValueError('Diagnostyka agenta Płatnika wymaga Windows.')
    path = (Path(path) if path is not None else default_profile()).resolve()
    report = progress or (lambda _stage, _state, _detail: None)

    def step(name, operation, success):
        report(name, 'running', '')
        try:
            result = operation()
        except Exception as error:
            if isinstance(error, ReadError):
                detail = error.detail or ERRORS.get(error.code, 'Operacja nie powiodła się.')
            elif name == 'Profil agenta':
                detail = str(error)
            elif name == 'Połączenie z kolejką PostgreSQL':
                detail = f'Połączenie nie powiodło się ({type(error).__name__}). Sprawdź pliki libpq agenta i uprawnienia.'
            else:
                detail = f'Nie powiodło się ({type(error).__name__}).'
            report(name, 'failed', detail)
            raise
        report(name, 'passed', success(result))
        return result

    config = step('Profil agenta', lambda: load_config(path), lambda value:
                  f'Profil i ścieżka do Płatnika są poprawne: {value["executable"].name}.')
    step('Połączenie z kolejką PostgreSQL', lambda: check_queue_access(config),
         lambda _value: 'Połączenie i uprawnienia do kolejki są poprawne.')
    step('Pulpit Windows', require_interactive_desktop,
         lambda _value: 'Aktywna sesja pulpitu jest dostępna.')
    window = step('Okno Płatnika', lambda: main_window(config),
                  lambda _value: 'Okno Płatnika jest widoczne; uruchomiono je tylko jeśli nie było otwarte.')
    step('Tabela deklaracji', lambda: register(window, config),
         lambda value: f'Znaleziono tabelę i {len(value[2])} skonfigurowanych kolumn.')
    if config.get('confirmation_download'):
        step('Tabela zestawów wysłanych', lambda: register(
            window, {**config['confirmation_download'], 'backend': config['backend']}),
             lambda value: f'Znaleziono tabelę i {len(value[2])} skonfigurowanych kolumn.')
    report('Test zakończony', 'passed',
           'Nie pobrano zadań, nie odczytano danych pracowników i nie wykonano importu.')
    return True


def run(config, path, *, once=False, target=None):
    require_interactive_desktop()

    from psycopg import Error, sql
    from psycopg.types.json import Jsonb

    schema = sql.Identifier(config['schema'])
    claim_query = sql.SQL('SELECT * FROM {}.platnik_gui_claim()').format(schema)
    finish_query = sql.SQL('SELECT {}.platnik_gui_finish(%s, %s, %s, %s)').format(schema)
    result = {'completed': 0, 'failed': 0, 'interrupted': None}
    try:
        with connect(config) as connection:
            if target is not None and queue_target(connection, config['schema']) != target:
                raise ValueError('Połączenie agenta zmieniło się podczas uruchamiania. Ponów koniec dnia.')
            while interactive_desktop():
                raw = connection.execute(claim_query).fetchone()
                if not raw:
                    return result
                job_id, lease, nip, period = raw
                outcome = bounded_read(path, {'nip': nip, 'period': period})
                error = outcome.get('error')
                findings = outcome.get('findings', [])
                saved = connection.execute(finish_query, (job_id, lease, Jsonb(findings), error)).fetchone()[0]
                LOGGER.info('zadanie=%s zapisano=%s stan=%s', job_id, saved, error or 'completed')
                if not saved or error == 'DesktopUnavailable':
                    result['interrupted'] = error or 'ReadFailed'
                    return result
                result['failed' if error else 'completed'] += 1
                if error in ('AppUnavailable', 'ScreenUnavailable', 'ProfileMismatch', 'Timeout'):
                    result['interrupted'] = error
                    return result
                if once:
                    return result
            result['interrupted'] = 'DesktopUnavailable'
    except Error as exc:
        LOGGER.warning('Kolejka PostgreSQL niedostępna (%s).', type(exc).__name__)
        result['interrupted'] = 'ReadFailed'
    return result


def run_pending(path=None, *, once=False, target=None):
    """Process the existing queue, then exit and release the workstation."""
    path = (Path(path) if path is not None else default_profile()).resolve()
    diagnostic = RotatingFileHandler(path.parent / 'agent.log', maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
    diagnostic.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOGGER.addHandler(diagnostic)
    LOGGER.setLevel(logging.INFO)
    try:
        return run(load_config(path), path, once=once, target=target)
    finally:
        LOGGER.removeHandler(diagnostic)
        diagnostic.close()


def default_profile():
    """Where the installed application keeps the agent profile.

    The profile belongs to the Windows user who will operate Płatnik.
    """
    base = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'Biuro'
    return base / 'platnik-agent.json'


def _windows_pipe(standard_handle, mode):
    """Read redirected handles from a windowed PyInstaller executable."""
    from ctypes import WinDLL, wintypes
    import msvcrt

    get_handle = WinDLL('kernel32', use_last_error=True).GetStdHandle
    get_handle.argtypes = [wintypes.DWORD]
    get_handle.restype = wintypes.HANDLE
    handle = get_handle(standard_handle)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError('Brak potoku agenta Płatnika.')
    flags = os.O_BINARY | (os.O_RDONLY if mode == 'r' else os.O_WRONLY)
    return os.fdopen(msvcrt.open_osfhandle(handle, flags), mode, encoding='utf-8')


def _serve_read_job(path):
    from contextlib import ExitStack

    with ExitStack() as stack:
        source = sys.stdin if sys.stdin is not None else stack.enter_context(_windows_pipe(-10, 'r'))
        destination = sys.stdout if sys.stdout is not None else stack.enter_context(_windows_pipe(-11, 'w'))
        try:
            outcome = {'findings': read_job(load_config(path), json.load(source))}
        except ReadError as exc:
            outcome = {'error': exc.code}
        except Exception:
            outcome = {'error': 'ReadFailed'}
        print(json.dumps(outcome, ensure_ascii=True), file=destination, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?',
                        choices=('daily', 'run', 'once', 'probe', 'describe', 'read-job', 'capture-walkthrough'))
    parser.add_argument('--config', type=Path, default=None,
                        help='Profil agenta. Domyślnie katalog danych Biuro: platnik-agent.json')
    parser.add_argument('--output', type=Path, default=None,
                        help='Plik JSON dla capture-walkthrough; domyślnie LOCALAPPDATA\\Biuro\\diagnostics.')
    parser.add_argument('--executable', type=Path, default=None,
                        help='Płatnik EXE, gdy nie ma profilu agenta.')
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        raise SystemExit('Agent Płatnika wymaga Windows.')
    if args.mode in (None, 'run'):
        # Old startup tasks passed no mode or the old continuous mode.
        print('Uruchom zakończenie dnia w Biuro → Kadry → Sprawdzanie z komputera kadr.')
        return
    path = (args.config or default_profile()).resolve()
    if args.mode == 'read-job':
        _serve_read_job(path)
        return
    try:
        capture_only = args.mode == 'capture-walkthrough'
        if capture_only and args.executable is not None:
            executable = args.executable.expanduser().resolve()
            if not executable.is_file():
                raise SystemExit('Nie znaleziono pliku Płatnika: ' + str(executable))
            config = {'executable': executable}
        elif capture_only and not path.is_file():
            config = {'executable': discover_executable()}
        elif not path.is_file():
            raise SystemExit('Brak profilu agenta Płatnika: ' + str(path) + '. Uzupełnij go w Ustawieniach → Płatnik.')
        else:
            config = load_config(path, full=args.mode not in ('describe', 'capture-walkthrough'))
        if args.mode == 'describe':
            print(json.dumps(describe(main_window(config)), ensure_ascii=True, indent=2))
        elif args.mode == 'capture-walkthrough':
            output, count = capture_wizard_walkthrough(config, args.output)
            print(f'Zapisano {count} stanów kreatora do: {output}')
        elif args.mode == 'probe':
            import pywinauto  # noqa: F401  Loaded on the agent VM to check the Windows runtime.
            check_queue_access(config)
            print('Połączenie z kolejką działa. Pulpit: ' + ('aktywny.' if interactive_desktop() else 'niedostępny.'))
        else:
            result = run_pending(path, once=args.mode == 'once')
            print(f"Zakończono: {result['completed']}. Błędy: {result['failed']}.")
            if result['interrupted']:
                raise ReadError(result['interrupted'])
    except ReadError as exc:
        raise SystemExit(ERRORS[exc.code]) from None
    except Exception as error:
        if args.mode == 'capture-walkthrough':
            raise SystemExit(f'Przechwytywanie nie powiodło się ({type(error).__name__}).') from None
        else:
            # Profile paths and credentials may appear in native error details.
            raise SystemExit('Sprawdź profil agenta, uprawnienia i połączenie z PostgreSQL.') from None


if __name__ == '__main__':
    main()
