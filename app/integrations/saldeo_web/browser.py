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


def open_authenticated_history(page, config):
    """Open the history, attempting one sign-in when the session has expired."""
    from playwright.sync_api import TimeoutError

    history = config['history_url']
    page.goto(history, wait_until='domcontentloaded', timeout=45000)
    if not same_origin(page, config):
        return False
    marker = page.locator(config['logged_in_selector']).filter(visible=True).first
    login = page.locator(config['username_selector']).filter(visible=True).first
    # The history shell and login form can both be rendered after DOMContentLoaded.
    try:
        marker.or_(login).first.wait_for(state='visible')
    except TimeoutError:
        if urlsplit(page.url).path != urlsplit(config['login_url']).path:
            raise
    if marker.is_visible() and not login.is_visible():
        return same_origin(page, config)
    if not config['username'] or not config['password_enc']:
        return False

    page.goto(config['login_url'], wait_until='domcontentloaded', timeout=45000)
    if not same_origin(page, config):
        return False
    try:
        login.wait_for(state='visible')
        if not attempt_saved_login(page, config):
            return False
        marker.wait_for(state='visible')
        if not same_origin(page, config):
            return False
        page.goto(history, wait_until='domcontentloaded', timeout=45000)
        if not same_origin(page, config):
            return False
        marker.wait_for(state='visible')
    except TimeoutError:
        return False
    return same_origin(page, config)


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
            if not open_authenticated_history(page, config):
                return {'state': 'login_required', 'items': []}
            if config.get('open_selector'):
                page.locator(config['open_selector']).first.click()
            for _ in range(config['max_pages']):
                if stop and stop.is_set():
                    return {'state': 'interrupted', 'items': items}
                # Require a positive page marker, never treat a failed selector as an empty success.
                page.locator(config['logged_in_selector']).first.wait_for(state='visible')
                page.wait_for_function('''s => Array.from(document.querySelectorAll(s.items)).some(e=>e.getClientRects().length)
                    || (s.empty && Array.from(document.querySelectorAll(s.empty)).some(e=>e.getClientRects().length))''',
                    arg={'items': config['item_selector'], 'empty': config['empty_selector']})
                batch = page.locator(config['item_selector']).evaluate_all('''nodes => nodes.slice(0,1000).map(e => ({
                    id: e.getAttribute('data-notification-id') || e.getAttribute('data-id') || '',
                    text: e.innerText, time: e.querySelector('time')?.getAttribute('datetime') || '',
                    title: e.querySelector('.content-col .font-14')?.innerText || '',
                    period: e.querySelector('.content-col .font-12-grey')?.innerText || ''}))''')
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


def attempt_saved_login(page,config):
    """Submit only unique visible controls on the configured HTTPS origin."""
    selectors = ('username_selector','password_selector','submit_selector')
    if not same_origin(page,config) or not config.get('username') or not config.get('password_enc') or not all(config.get(k) for k in selectors):
        return False
    controls = [page.locator(config[key]) for key in selectors]
    if not all(control.count()==1 and control.is_visible() for control in controls):
        return False
    from app.utils.crypto import decrypt_password
    controls[0].fill(config['username'])
    if not same_origin(page, config):
        return False
    controls[1].fill(decrypt_password(config['password_enc']))
    if not same_origin(page, config):
        return False
    controls[2].click()
    return True


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
                attempted = False
                page.set_default_timeout(15000)
                while context.pages and time.monotonic() < deadline:
                    try:
                        page = context.pages[0]
                        if same_origin(page,config):
                            marker = config.get('logged_in_selector')
                            if marker and page.locator(marker).first.is_visible():
                                return True
                            if actor_id and not learned:
                                learned = remember_login_fields(page,actor_id)
                                if learned:
                                    config = load()
                            if not attempted:
                                try:
                                    attempted = attempt_saved_login(page,config)
                                except Exception as error:
                                    # Never log Playwright errors: they can contain filled credentials.
                                    attempted = True
                                    import logging
                                    logging.getLogger(__name__).warning('saldeo_interactive_login_failed error_type=%s',type(error).__name__)
                        page.wait_for_timeout(500)
                    except Exception:
                        break
            finally:
                try:
                    context.close()
                except Exception:
                    pass  # Closing the browser manually already closes its context.

    return False


def remember_login_fields(page, actor_id):
    """Learn verified, unique visible controls. Never read values entered by the user."""
    from app.integrations.saldeo_web.settings import load, KEY, LOGIN_SELECTORS
    candidates = LOGIN_SELECTORS
    if not all(page.locator(selector + ':visible').count()==1 for selector in candidates.values()):
        return False
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        return False
    import json
    from app.db.dao_app_settings import set_setting
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
