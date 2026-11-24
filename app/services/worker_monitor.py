"""Read-only shared worker status; no credentials or report contents in the snapshot."""
from app.remote.boundary import endpoint as office_endpoint
import json
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting


def _setting(raw, default):
    """A corrupt or half-written JSON value must not blank the whole panel."""
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


@office_endpoint('app.services.worker_monitor.snapshot')
def snapshot(actor_id):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Brak dostępu do panelu workera.')
    heartbeat = _setting(get_setting('background_worker_heartbeat'), {})
    events = _setting(get_setting('background_worker_events'), [])
    from app.services.worker_activity import KEY, LIMIT
    activity = _setting(get_setting(KEY), [])
    events = sorted(events + activity, key=lambda event: event.get('at', ''))[-LIMIT:]
    # The worker clears its live cursor when a run ends, so a stage that is still
    # stored while the e-ZLA job is not running is left over from an interrupted run.
    progress = _setting(get_setting('ezla_progress'), {})
    if progress.get('stage') and (heartbeat.get('jobs') or {}).get('ezla', {}).get('state') != 'running':
        progress = {**progress, 'stale': True}
    config = _setting(get_setting('ezla_service'), {})
    with get_conn() as conn:
        rows = conn.execute('''SELECT f.id,f.nazwa,f.nip,s.payload::text AS wartosc FROM firmy f
            LEFT JOIN company_checking_snapshots s ON s.source='ezla_sync' AND s.company_id=f.id AND s.period=''
            ORDER BY lower(f.nazwa)''').fetchall()
        retries = conn.execute("""
            SELECT DISTINCT ON (company_id) company_id,period,attempts,next_attempt_at,last_error
            FROM enova_stage_requests WHERE last_error IS NOT NULL
            ORDER BY company_id,next_attempt_at,period
        """).fetchall()
    return {'heartbeat': heartbeat, 'events': events, 'progress': progress,
            'ezla_enabled': bool(config.get('enabled')), 'ezla_companies': config.get('companies', []),
            'enova_retries': [dict(row) for row in retries],
            'companies': [{**dict(row), 'sync': json.loads(row['wartosc'] or '{}')} for row in rows]}
