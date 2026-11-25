import json
from datetime import datetime
from zoneinfo import ZoneInfo
from app.db.dao_app_settings import get_setting, set_setting
from .settings import load
from .browser import browser_lock, read_history
from .history import ingest, apply_pending, parse


def run(stop=None):
    config = load()
    if not config['enabled']:
        return {'state': 'disabled', 'mode': 'disabled'}
    with browser_lock() as acquired:
        if not acquired:
            return {'state': 'browser_busy', 'mode': 'busy'}
        started = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
        stage = 'starting'

        def update_progress(current_stage, counts):
            nonlocal stage
            stage = current_stage
            set_setting('saldeo_web_status', json.dumps({
                'state': 'running', 'stage': stage, 'checked_at': started, **counts,
            }))

        set_setting('saldeo_web_status', json.dumps({
            'state': 'running', 'stage': stage, 'checked_at': started,
        }))
        try:
            applied = apply_pending()  # Retry durable backlog even if the website is unavailable.
            result = read_history(config, stop, progress=update_progress)
            recognized = sum(
                bool(parse((str(item.get('title') or '') + '\n' + str(item.get('period') or '')).strip())
                     or parse(str(item.get('text') or '')))
                for item in result['items']
            )
            inserted = ingest(result['items'], config['history_url'])
            applied += apply_pending()
            status = {'state': result['state'], 'stage': 'complete', 'pages': result.get('pages', 0),
                      'rows_seen': result.get('rows_seen', 0), 'scanned': len(result['items']),
                      'recognized': recognized, 'new': inserted, 'applied': applied,
                      'reason': result.get('reason'),
                      'checked_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}
            set_setting('saldeo_web_status', json.dumps(status))
            if result['state'] == 'login_required':
                raise RuntimeError('Saldeo wymaga logowania lub dokończenia weryfikacji.')
            if result['state'] == 'partial':
                raise RuntimeError('Historia Saldeo nie została odczytana w całości.')
            if result['state'] == 'interrupted':
                raise RuntimeError('Sprawdzenie historii Saldeo zostało przerwane.')
            return status
        except Exception as error:
            # Do not log browser exception strings: URLs or page content may contain secrets.
            try:
                current = json.loads(get_setting('saldeo_web_status') or '{}')
                if not isinstance(current, dict):
                    current = {}
            except (TypeError, ValueError):
                current = {}
            current.update(stage=stage, error_type=type(error).__name__,
                           checked_at=datetime.now(ZoneInfo('Europe/Warsaw')).isoformat())
            if current.get('state') not in ('partial', 'login_required', 'interrupted'):
                current['state'] = 'error'
            set_setting('saldeo_web_status', json.dumps(current))
            raise RuntimeError('Sprawdzenie Saldeo nieukończone. Sprawdź status w panelu Saldeo.') from None
