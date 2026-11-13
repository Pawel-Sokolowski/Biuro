import json
from datetime import datetime
from zoneinfo import ZoneInfo
from app.db.dao_app_settings import set_setting
from .settings import load
from .browser import browser_lock, read_history
from .history import ingest, apply_pending


def run(stop=None):
    config = load()
    if not config['enabled']:
        return {'state': 'disabled'}
    with browser_lock() as acquired:
        if not acquired:
            return {'state': 'browser_busy'}
        applied = apply_pending()  # Retry durable backlog even if the website is unavailable.
        try:
            result = read_history(config, stop)
            inserted = ingest(result['items'], config['history_url'])
            applied += apply_pending()
            status = {'state': result['state'], 'new': inserted, 'applied': applied,
                      'checked_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
            set_setting('saldeo_web_status', json.dumps(status))
            if result['state'] in ('login_required', 'partial'):
                raise RuntimeError('Saldeo wymaga logowania lub sprawdzenie historii jest niepełne.')
            return status
        except Exception:
            # Do not log browser exception strings: URLs or page content may contain secrets.
            if 'result' not in locals():
                set_setting('saldeo_web_status', json.dumps({'state': 'error', 'checked_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}))
            raise RuntimeError('Sprawdzenie Saldeo nieukończone. Sprawdź status w panelu Saldeo.') from None
