"""Office API for the interactive Płatnik queue."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn
from app.integrations.enova.accounting import require_access
from app.integrations.platnik.declarations import payer_mapping
from app.integrations.platnik.gui_agent import queue_target
from app.integrations.platnik.gui_evidence import clean_finding, month as canonical_month
from app.remote.boundary import endpoint as office_endpoint
from app.security.authorization import assigned_company_ids
from app.services.checking_refresh import scope
from app.utils.months import previous_month

def _period(value):
    if not isinstance(value, str) or canonical_month(value) != value:
        raise ValueError('Wybierz miesiąc rozliczenia.')
    return value


def _requested_payers(actor_id, company_ids):
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
    return mapped, len(company_ids) - len(set(mapped.values()))


@office_endpoint('app.services.platnik_gui.request_checks')
def request_checks(actor_id, company_ids, month):
    period = _period(month)
    mapped, skipped = _requested_payers(actor_id, company_ids)
    with get_conn() as conn:
        queued = _enqueue(conn, mapped, period, actor_id)
    result = {'queued': queued, 'existing': len(mapped) - queued}
    if company_ids is None:
        result['skipped'] = skipped
    return result


def _enqueue(conn, mapped, period, actor_id, *, day_start=None):
    return conn.execute("""
        INSERT INTO platnik_gui_checks(company_id, actor_id, nip, period)
        SELECT company_id, %s, nip, %s
        FROM unnest(%s::integer[], %s::text[]) AS payer(company_id, nip)
        WHERE %s::timestamptz IS NULL OR NOT EXISTS (
            SELECT FROM platnik_gui_checks old
            WHERE old.company_id=payer.company_id AND old.nip=payer.nip AND old.period=%s
              AND old.state='completed' AND old.finished_at >= %s
        )
        ON CONFLICT (company_id, nip, period) WHERE state IN ('queued', 'running') DO NOTHING
    """, (actor_id, period, list(mapped.values()), list(mapped), day_start, period, day_start)).rowcount


@office_endpoint('app.services.platnik_gui.request_daily_checks')
def request_daily_checks(actor_id, company_ids, target):
    """Queue the end-of-day read for companies the operator can access."""
    now = datetime.now(ZoneInfo('Europe/Warsaw'))
    month = now.strftime('%Y-%m')
    periods = [previous_month(month), month]
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    mapped, skipped = _requested_payers(actor_id, company_ids)
    with get_conn() as conn:
        if queue_target(conn) != target:
            raise ValueError('Połączenie agenta wskazuje inną bazę lub schemat niż Biuro. Sprawdź konfigurację PostgreSQL.')
        queued = sum(_enqueue(conn, mapped, period, actor_id, day_start=day_start) for period in periods)
    return {'queued': queued, 'periods': periods, 'skipped': skipped}


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
