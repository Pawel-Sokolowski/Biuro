"""Refresh the configured Enova sources once per Sunday, with resumable progress."""
from datetime import date, datetime, time, timedelta
import json
import logging
from zoneinfo import ZoneInfo

from app.db.dao_app_settings import get_setting, set_setting
from app.services.worker_settings import load_settings

KEY = 'worker_enova_weekly_progress'
PHASES = ('documents', 'checking')
logger = logging.getLogger(__name__)


def _progress():
    try:
        value = json.loads(get_setting(KEY) or 'null')
        if isinstance(value, dict) and all(isinstance(value.get(key), list) and
                all(isinstance(database, str) for database in value[key]) for key in PHASES):
            return date.fromisoformat(value['sunday']), value
    except (TypeError, ValueError, KeyError):
        logger.warning('enova_weekly_progress_unreadable')
    return None, {}


def run(stop=None, *, now=None):
    from app.integrations.enova.checking import CheckingBusy, run_checking_import
    from app.integrations.enova.connection_settings import load_connection
    from app.integrations.enova.document_counts import requested_months, run_import

    warsaw = ZoneInfo('Europe/Warsaw')
    now = now.astimezone(warsaw) if now is not None else datetime.now(warsaw)
    settings = load_settings()
    if not settings['enova_weekly']:
        return {'mode': 'disabled'}
    at = settings['enova_weekly_time']
    sunday = now.date() + timedelta(days=(6 - now.weekday()) % 7)
    scheduled = datetime.combine(sunday, time.fromisoformat(at), warsaw)
    last_due = sunday if now >= scheduled else sunday - timedelta(days=7)
    stored, progress = _progress()
    target = max(stored, last_due) if stored is not None else sunday
    if stored != target:
        progress = {'sunday': target.isoformat(), 'documents': [], 'checking': []}
        set_setting(KEY, json.dumps(progress))
    if target > last_due:
        return {'mode': 'waiting', 'next_scheduled_at': scheduled.isoformat(), 'scheduled_time': at}

    config = load_connection()
    if not config['enabled']:
        return {'mode': 'disabled'}
    sources = {source['database']: source for source in config['sources']}
    if not sources:
        return {'mode': 'not_configured'}
    months = requested_months(now.date(), config['months_back'])
    attempted, busy = False, False
    for key, importer in (('documents', run_import), ('checking', run_checking_import)):
        if stop is not None and stop.is_set():
            return {'mode': 'cancelled'}
        pending = [source for database, source in sources.items() if database not in progress[key]]
        if not pending:
            continue
        attempted = True
        try:
            rows = importer({**config, 'sources': pending}, apply=True, today=now.date(), months=months, stop=stop)
        except CheckingBusy:
            busy = True
            continue
        except Exception as error:
            logger.error('enova_weekly_import_failed phase=%s error_type=%s', key, type(error).__name__)
            continue
        successes = {row['database'] for row in rows if row['ok'] and not row.get('hr_failed')}
        if successes:
            progress[key] = sorted(set(progress[key]) | successes)
            set_setting(KEY, json.dumps(progress))

    if stop is not None and stop.is_set():
        return {'mode': 'cancelled'}
    completed = set(sources).intersection(progress['documents'], progress['checking'])
    counts = {'companies': len(sources), 'applied': len(completed), 'failed': len(sources) - len(completed)}
    if counts['failed']:
        return {'mode': 'partial' if completed else 'busy' if busy else 'failed', **counts}
    next_slot = datetime.combine(target + timedelta(days=7), time.fromisoformat(at), warsaw)
    return {'mode': 'completed' if attempted else 'checked_this_week', 'next_scheduled_at': next_slot.isoformat(),
            'scheduled_time': at, **counts}
