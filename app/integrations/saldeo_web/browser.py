"""Read the authenticated history, including read entries, with a private browser profile.

Selectors are configured against the actual Saldeo UI. No guessed endpoint or selectors.
"""
import time
from contextlib import contextmanager
from urllib.parse import urlsplit
from app.db.dao_conn import get_coordination_conn
from app.config import get_data_dir
from .executable import resolve as resolve_browser


@contextmanager
def browser_lock():
    with get_coordination_conn() as conn:
        acquired = conn.execute('SELECT pg_try_advisory_xact_lock(748214)').fetchone()[0]
        yield acquired


def profile_path():
    from app.config import get_dsn
    import hashlib
    # Isolate offices that share one Windows account.
    path = get_data_dir() / 'saldeo-browser' / hashlib.sha256(get_dsn().encode()).hexdigest()[:24]
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(path)


def same_origin(page, config):
    actual, expected = urlsplit(page.url), urlsplit(config['login_url'])
    return (actual.scheme, actual.hostname, actual.port or 443) == (expected.scheme, expected.hostname, expected.port or 443)


def read_history(config, stop=None):
    from playwright.sync_api import sync_playwright
    from app.integrations.saldeo_web.settings import validate
    validate(config)
    items = []
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(profile_path(), executable_path=resolve_browser(config), headless=True,
                                                       accept_downloads=False, service_workers='allow')
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(15000)
            page.goto(config['history_url'], wait_until='domcontentloaded', timeout=45000)
            if not page.locator(config['logged_in_selector']).first.is_visible():
                selectors = ('username_selector', 'password_selector', 'submit_selector')
                if not config['password_enc'] or not config['username'] or not all(config[k] for k in selectors):
                    return {'state': 'login_required', 'items': []}
                page.goto(config['login_url'], wait_until='domcontentloaded', timeout=45000)
                if not same_origin(page, config):
                    return {'state': 'login_required', 'items': []}
                from app.utils.crypto import decrypt_password
                page.locator(config['username_selector']).fill(config['username'])
                page.locator(config['password_selector']).fill(decrypt_password(config['password_enc']))
                page.locator(config['submit_selector']).click()
                try:
                    page.locator(config['logged_in_selector']).first.wait_for(state='visible')
                except Exception:
                    return {'state': 'login_required', 'items': []}  # Includes MFA/CAPTCHA; never bypass them.
                page.goto(config['history_url'], wait_until='domcontentloaded', timeout=45000)
            if not same_origin(page, config):
                return {'state': 'login_required', 'items': []}
            if config.get('open_selector'):
                page.locator(config['open_selector']).first.click()
            for _ in range(config['max_pages']):
                if stop and stop.is_set():
                    return {'state': 'interrupted', 'items': items}
                # Require a positive page marker, never treat a failed selector as an empty success.
                page.locator(config['logged_in_selector']).first.wait_for(state='visible')
                page.wait_for_function('''s => Array.from(document.querySelectorAll(s.items)).some(e=>e.getClientRects().length)
                    || Array.from(document.querySelectorAll(s.empty)).some(e=>e.getClientRects().length)''',
                    arg={'items': config['item_selector'], 'empty': config['empty_selector']})
                batch = page.locator(config['item_selector']).evaluate_all('''nodes => nodes.slice(0,1000).map(e => ({
                    id: e.getAttribute('data-notification-id') || e.getAttribute('data-id') || '',
                    text: e.innerText, time: e.querySelector('time')?.getAttribute('datetime') || ''}))''')
                items.extend(batch)
                if len(items) >= 5000:
                    return {'state': 'partial', 'items': items[:5000]}
                if not config['next_selector']:
                    # Explicitly partial: absence of a configured paginator does not prove full history.
                    return {'state': 'partial', 'items': items}
                next_page = page.locator(config['next_selector']).first
                if not next_page.count() or not next_page.is_visible() or next_page.is_disabled() or next_page.get_attribute('aria-disabled') == 'true':
                    return {'state': 'ok', 'items': items}
                previous = page.locator(config['item_selector']).all_inner_texts()
                next_page.click()
                page.wait_for_function('''s => JSON.stringify(Array.from(document.querySelectorAll(s.selector)).map(e=>e.innerText)) !== JSON.stringify(s.previous)''',
                                       arg={'selector': config['item_selector'], 'previous': previous})
                if stop:
                    if stop.wait(config['pause_seconds']):
                        return {'state': 'interrupted', 'items': items}
                else:
                    page.wait_for_timeout(config['pause_seconds'] * 1000)
            return {'state': 'partial', 'items': items}
        finally:
            context.close()


def login_window(actor_id=None):
    """Interactive login under the same Windows account as the worker, max 15 minutes."""
    from playwright.sync_api import sync_playwright
    from app.integrations.saldeo_web.settings import load, validate
    config = load()
    # Opening a manual login window must not depend on configured monitoring CSS.
    validate({**config, 'enabled':False})
    with browser_lock() as acquired:
        if not acquired:
            raise ValueError('Przeglądarka jest używana przez worker. Spróbuj po zakończeniu sprawdzenia.')
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(profile_path(), executable_path=resolve_browser(config), headless=False,
                                                           accept_downloads=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(config['login_url'], wait_until='domcontentloaded', timeout=45000)
                deadline = time.monotonic() + 900
                learned = False
                while context.pages and time.monotonic() < deadline:
                    try:
                        if actor_id and not learned and same_origin(context.pages[0],config):
                            learned = remember_login_fields(context.pages[0],actor_id)
                        context.pages[0].wait_for_timeout(500)
                    except Exception:
                        break
            finally:
                try:
                    context.close()
                except Exception:
                    pass  # Closing the browser manually already closes its context.


def remember_login_fields(page, actor_id):
    """Learn verified, unique visible controls. Never read values entered by the user."""
    candidates = {
        'username_selector':'input[qa-id="username"]',
        'password_selector':'input[type="password"]',
        'submit_selector':'button:has-text("Zaloguj")',
    }
    if not all(page.locator(selector + ':visible').count()==1 for selector in candidates.values()):
        return False
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        return False
    import json
    from app.db.dao_app_settings import set_setting
    from app.integrations.saldeo_web.settings import load, KEY
    config = load()
    # Preserve existing user mappings and every setting, including enabled state.
    changed = False
    for key,selector in candidates.items():
        if not config.get(key):
            config[key] = selector
            changed = True
    if changed:
        set_setting(KEY,json.dumps(config,ensure_ascii=False))
    return True
