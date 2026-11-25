"""Read the authenticated history, including read entries, with a private browser profile.

Selectors are configured against the actual Saldeo UI. No guessed endpoint or selectors.
"""
import time
from contextlib import contextmanager
from urllib.parse import urlsplit
from app.db.dao_conn import get_coordination_conn
from app.config import get_data_dir
from .executable import resolve as resolve_browser


OPERATIONS_EXTRACT = """nodes => nodes.slice(0, 1000).flatMap(row => {
    const read = selector => (row.querySelector(selector)?.innerText || '').trim();
    const cell = n => read(':scope > bs-mat-cell.col-order-' + n);
    const type = cell(1);
    const status = read(':scope > bs-mat-cell.col-order-3 .background-operation-list-table__status span');
    if (type.toLowerCase() !== 'komplet dokumentów' || status.toLowerCase() !== 'zakończone') return [];
    const company = cell(2).replace(/\\s+/g, ' ');
    const period = cell(6);
    const title = company + ' - Komplet dokumentów';
    return company && period ? [{text: title + '\\n' + period, time: cell(5), title, period}] : [];
})"""


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


def read_history(config, stop=None, progress=None):
    from playwright.sync_api import sync_playwright
    from app.integrations.saldeo_web.settings import OPERATIONS_ROWS, validate
    validate(config)
    items = []
    pages = rows_seen = 0

    def result(state, *, reason=None):
        return {'state': state, 'items': items[:5000], 'pages': pages,
                'rows_seen': rows_seen, 'reason': reason}

    def report(stage):
        if progress:
            progress(stage, {'pages': pages, 'rows_seen': rows_seen, 'items': len(items)})

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(profile_path(), executable_path=resolve_browser(config), headless=True,
                                                       accept_downloads=False, service_workers='allow')
        try:
            page = context.pages[0] if context.pages else context.new_page()
            # The operations table is an Angular view: give it time on a slow office link.
            page.set_default_timeout(45000)
            report('opening_history')
            if not open_authenticated_history(page, config):
                report('login_required')
                return result('login_required', reason='login_required')
            if config.get('open_selector'):
                page.locator(config['open_selector']).first.click()
            for _ in range(config['max_pages']):
                if stop and stop.is_set():
                    report('interrupted')
                    return result('interrupted', reason='stopped')
                # Require a positive page marker, never treat a failed selector as an empty success.
                page.locator(config['logged_in_selector']).first.wait_for(state='visible')
                # The list renders in place: wait for rows, or for the paginator that an
                # account without operations still shows, instead of timing out on rows.
                ready = page.locator(config['item_selector']).or_(
                    page.locator('input[qa-id="paginatorPage"]'))
                if config.get('empty_selector'):
                    ready = ready.or_(page.locator(config['empty_selector']))
                ready.first.wait_for(state='visible')
                rows = page.locator(config['item_selector']).filter(visible=True)
                row_count = rows.count()
                if row_count == 0:
                    empty = config.get('empty_selector')
                    if not empty or not page.locator(empty).filter(visible=True).count():
                        report('rows_not_found')
                        return result('partial', reason='rows_not_found')
                pages += 1
                rows_seen += row_count
                if config['item_selector'] == OPERATIONS_ROWS:
                    batch = rows.evaluate_all(OPERATIONS_EXTRACT)
                else:
                    batch = rows.evaluate_all('''nodes => nodes.slice(0,1000).map(e => ({
                    id: e.getAttribute('data-notification-id') || e.getAttribute('data-id') || '',
                    text: e.innerText, time: e.querySelector('time')?.getAttribute('datetime') || '',
                    title: e.querySelector('.content-col .font-14')?.innerText || '',
                    period: e.querySelector('.content-col .font-12-grey')?.innerText || ''}))''')
                items.extend(batch)
                report('history_page_read')
                if len(items) >= 5000:
                    return result('partial', reason='item_limit')
                if not config['next_selector']:
                    # Explicitly partial: absence of a configured paginator does not prove full history.
                    return result('partial', reason='paginator_not_configured')
                has_next_page = _next_page(page, config, items)
                if has_next_page is None:
                    report('paginator_not_found')
                    return result('partial', reason='paginator_not_found')
                if not has_next_page:
                    report('history_complete')
                    return result('ok')
                if stop:
                    if stop.wait(config['pause_seconds']):
                        report('interrupted')
                        return result('interrupted', reason='stopped')
                else:
                    page.wait_for_timeout(config['pause_seconds'] * 1000)
            report('page_limit')
            return result('partial', reason='page_limit')
        finally:
            context.close()


def _next_page(page, config, items):
    """Advance a page; False means complete and None means the control is missing.

    Saldeo has two paginators: a page-number input (qa-id=paginatorPage with a max) and,
    for custom views, a next/prev button. Both are supported so an existing installation
    keeps working after the default changed.
    """
    control = page.locator(config['next_selector']).first
    if not control.count() or not control.is_visible():
        return None
    if control.evaluate('e => e.tagName.toLowerCase()') == 'input':
        maximum = str(control.get_attribute('max') or '').strip()
        current = str(control.input_value() or '1').strip()
        if maximum.isdigit() and current.isdigit() and int(current) >= int(maximum):
            return False
        previous = page.locator(config['item_selector']).all_inner_texts()
        control.fill(str(int(current) + 1 if current.isdigit() else 2))
        control.press('Enter')
        page.wait_for_function('''s => JSON.stringify(Array.from(document.querySelectorAll(s.selector)).map(e=>e.innerText)) !== JSON.stringify(s.previous)''',
                               arg={'selector': config['item_selector'], 'previous': previous})
        return True
    if control.is_disabled() or control.get_attribute('aria-disabled') == 'true':
        return False
    previous = page.locator(config['item_selector']).all_inner_texts()
    control.click()
    page.wait_for_function('''s => JSON.stringify(Array.from(document.querySelectorAll(s.selector)).map(e=>e.innerText)) !== JSON.stringify(s.previous)''',
                           arg={'selector': config['item_selector'], 'previous': previous})
    return True


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


def _confirm_history(context, page, config):
    """Open the operations list in the same session and wait for its table."""
    from playwright.sync_api import TimeoutError
    try:
        page.goto(config['history_url'], wait_until='domcontentloaded', timeout=60000)
        if not same_origin(page, config):
            return False
        # Either rows or the paginator must appear; an account with no operations still
        # renders the paginator, so this never waits for content that cannot exist.
        page.locator(config['item_selector']).or_(page.locator('input[qa-id="paginatorPage"]')).first.wait_for(
            state='visible', timeout=60000)
    except TimeoutError:
        return False
    except Exception:
        return False
    return same_origin(page, config)


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
                # The Saldeo header is an Angular app: after signing in it needs several
                # seconds before the notification marker is rendered.
                page.set_default_timeout(30000)
                while context.pages and time.monotonic() < deadline:
                    try:
                        page = context.pages[0]
                        if same_origin(page,config):
                            marker = config.get('logged_in_selector')
                            if marker and page.locator(marker).first.is_visible():
                                # The marker lives in the header, so it is also present on the
                                # history page: confirm the list itself before reporting success.
                                return _confirm_history(context, page, config)
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
