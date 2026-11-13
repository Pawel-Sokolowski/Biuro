"""Read-only shared worker status; no credentials or report contents in the snapshot."""
import json
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting


def snapshot(actor_id):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Brak dostępu do panelu workera.')
    heartbeat = json.loads(get_setting('background_worker_heartbeat') or '{}')
    events = json.loads(get_setting('background_worker_events') or '[]')
    from app.services.worker_activity import KEY, LIMIT
    activity = json.loads(get_setting(KEY) or '[]')
    events = sorted(events + activity, key=lambda event: event.get('at', ''))[-LIMIT:]
    progress = json.loads(get_setting('ezla_progress') or '{}')
    config = json.loads(get_setting('ezla_service') or '{}')
    with get_conn() as conn:
        rows = conn.execute('''SELECT f.id,f.nazwa,f.nip,s.wartosc FROM firmy f
            LEFT JOIN app_settings s ON s.klucz='ezla_sync_' || f.id::text ORDER BY lower(f.nazwa)''').fetchall()
    return {'heartbeat': heartbeat, 'events': events, 'progress': progress,
            'ezla_enabled': bool(config.get('enabled')), 'ezla_companies': config.get('companies', []),
            'companies': [{**dict(row), 'sync': json.loads(row['wartosc'] or '{}')} for row in rows]}
