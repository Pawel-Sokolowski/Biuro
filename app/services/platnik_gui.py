"""Office API for the interactive Płatnik queue."""
from app.db.dao_conn import get_conn
from app.integrations.enova.accounting import require_access
from app.integrations.platnik.declarations import payer_mapping
from app.integrations.platnik.gui_evidence import clean_finding, month as canonical_month
from app.remote.boundary import endpoint as office_endpoint
from app.security.authorization import assigned_company_ids
from app.services.checking_refresh import scope
from app.utils.months import previous_month

DAILY_KEY = 'worker_platnik_gui_enqueued_date'


def _period(value):
    if not isinstance(value, str) or canonical_month(value) != value:
        raise ValueError('Wybierz miesiąc rozliczenia.')
    return value


@office_endpoint('app.services.platnik_gui.request_checks')
def request_checks(actor_id, company_ids, month):
    period = _period(month)
    all_companies = company_ids is None
    if all_companies:
        require_access(actor_id)
        allowed = assigned_company_ids(actor_id)
    else:
        scope(actor_id, company_ids)
    with get_conn() as conn:
        companies = conn.execute(
            'SELECT id,nip,forma_dzialalnosci FROM firmy ORDER BY id',
        ).fetchall()
    if all_companies:
        company_ids = [row['id'] for row in companies if row['id'] in allowed]
    mapped = payer_mapping(companies, company_ids, skip_invalid=all_companies)
    if not mapped:
        raise ValueError('Nie znaleziono płatników dla wybranych firm.')
    with get_conn() as conn:
        queued = _enqueue(conn, mapped, period, actor_id)
    result = {'queued': queued, 'existing': len(mapped) - queued}
    if all_companies:
        result['skipped'] = len(company_ids) - len(set(mapped.values()))
    return result


def _enqueue(conn, mapped, period, actor_id=None):
    return conn.execute("""
        INSERT INTO platnik_gui_checks(company_id, actor_id, nip, period)
        SELECT company_id, %s, nip, %s
        FROM unnest(%s::integer[], %s::text[]) AS payer(company_id, nip)
        ON CONFLICT (company_id, nip, period) WHERE state IN ('queued', 'running') DO NOTHING
    """, (actor_id, period, list(mapped.values()), list(mapped))).rowcount


def enqueue_daily(day):
    """Commit the date and its jobs together so a restart can safely retry."""
    stamp = day.isoformat()
    month = stamp[:7]
    periods = (previous_month(month), month)
    with get_conn() as conn:
        marked = conn.execute("""
            INSERT INTO app_settings(klucz, wartosc) VALUES (%s, %s)
            ON CONFLICT (klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc
            WHERE app_settings.wartosc < EXCLUDED.wartosc
            RETURNING wartosc
        """, (DAILY_KEY, stamp)).fetchone()
        if marked is None:
            return {'date': stamp, 'mode': 'checked_today'}
        companies = conn.execute('SELECT id,nip,forma_dzialalnosci FROM firmy ORDER BY id').fetchall()
        mapped = payer_mapping(companies, skip_invalid=True)
        queued = sum(_enqueue(conn, mapped, period) for period in periods) if mapped else 0
    return {'date': stamp, 'queued': queued, 'periods': list(periods)}


@office_endpoint('app.services.platnik_gui.read_checks')
def read_checks(actor_id, month):
    require_access(actor_id)
    period = _period(month)
    ids = list(assigned_company_ids(actor_id))
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT ON (company_id, nip)
                company_id, nip,
                CASE WHEN state = 'running' AND lease_until <= clock_timestamp()
                          THEN CASE WHEN attempts < 3 THEN 'queued' ELSE 'failed' END
                     ELSE state END AS state,
                CASE WHEN state = 'running' AND lease_until <= clock_timestamp() AND attempts >= 3
                          THEN 'LeaseExpired' ELSE error_code END AS error_code,
                findings
            FROM platnik_gui_checks
            WHERE period=%s AND company_id=ANY(%s)
            ORDER BY company_id, nip, requested_at DESC, id DESC
        """, (period, ids)).fetchall()
    checks = []
    for row in rows:
        state, error = row['state'], row['error_code']
        findings = []
        if state == 'completed':
            try:
                findings = [clean_finding(item) for item in row['findings']]
                if len({(item['form'], item['report']) for item in findings}) != len(findings):
                    raise ValueError('Powtórzony wpis.')
            except (KeyError, TypeError, ValueError):
                state, error = 'failed', 'ProfileMismatch'
        checks.append({'company_id': row['company_id'], 'nip': row['nip'],
                       'state': state, 'error_code': error, 'findings': findings})
    return checks
