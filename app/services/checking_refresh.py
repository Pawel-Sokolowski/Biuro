"""Selected-company read requests, executed by the existing server worker."""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app.db.dao_conn import advisory_lock, get_conn
from app.integrations.enova.accounting import require_access
from app.remote.boundary import endpoint as office_endpoint
from app.security.authorization import assigned_company_ids
from app.utils.months import month_bounds, previous_month


KINDS = (
    'enova',
    'ezla',
    'platnik',
    'platnik_declarations',
    'platnik_annual',
    'declaration_year',
)
READ_LOCK = 748219


def stamp():
    return datetime.now(
        ZoneInfo('Europe/Warsaw')
    ).isoformat()


def scope(
    actor_id,
    company_ids,
):
    require_access(actor_id)

    if (
        not isinstance(company_ids, list)
        or not company_ids
        or len(company_ids) > 10000
        or any(
            type(fid) is not int or fid <= 0
            for fid in company_ids
        )
    ):
        raise ValueError(
            'Wybierz jedną lub kilka firm.'
        )

    ids = sorted(set(company_ids))

    if not set(ids) <= set(
        assigned_company_ids(actor_id)
    ):
        raise PermissionError(
            'Brak dostępu do części wybranych firm.'
        )

    with get_conn() as conn:
        rows = conn.execute(
            '''
            SELECT id, nazwa, nip
            FROM firmy
            WHERE id = ANY(%s)
            ORDER BY id
            ''',
            (ids,),
        ).fetchall()

    if len(rows) != len(ids):
        raise ValueError(
            'Część wybranych firm już nie istnieje.'
        )

    return [
        dict(row)
        for row in rows
    ]


def _write(conn, value):
    conn.execute("""
        INSERT INTO checking_requests(id,actor_id,kind,company_ids,period,state,created_at,finished_at,payload)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(id) DO UPDATE SET state=EXCLUDED.state, finished_at=EXCLUDED.finished_at, payload=EXCLUDED.payload
    """, (value['id'], value['actor_id'], value['kind'], value['company_ids'], str(value['period']),
          value['state'], value['created_at'], value.get('finished_at'), Jsonb(value)))


def _save(value):
    with get_conn() as conn:
        _write(conn, value)


def _request(
    actor_id,
    kind,
    company_ids,
    period,
):
    scope(
        actor_id,
        company_ids,
    )

    if kind not in KINDS:
        raise ValueError(
            'Nieprawidłowe źródło odczytu.'
        )

    if kind in (
        'platnik',
        'platnik_annual',
        'declaration_year',
    ):
        minimum_year = (
            2024
            if kind == 'platnik'
            else 2000
        )

        if (
            type(period) is not int
            or not minimum_year <= period <= 2100
        ):
            raise ValueError(
                'Wybierz rok zestawienia Płatnika.'
            )

    else:
        month_bounds(period)

    return {
        'id': uuid.uuid4().hex,
        'actor_id': actor_id,
        'kind': kind,
        'company_ids': sorted(
            set(company_ids)
        ),
        'period': period,
        'state': 'queued',
        'created_at': stamp(),
    }


def _result_state(results):
    failed = sum(
        not row.get(
            'ok',
            row.get('state') == 'ok',
        )
        for row in results
    )

    partial = any(
        row.get('monthly_imported')
        for row in results
    )

    if (
        not results
        or (
            failed == len(results)
            and not partial
        )
    ):
        return 'failed'

    # Każda firma bez zapisanych danych to niepowodzenie w całości: użytkownik
    # nie może zobaczyć „wykonane”, gdy część firm nie została odczytana.
    if failed:
        return 'partial'

    return (
        'partial'
        if failed
        else 'completed'
    )


def _needs_retry(state):
    """Ponawiamy tylko stan, w którym nic nie zostało zapisane."""
    return state in ('failed', 'queued')


def retry_request(request_id, actor_id=None):
    """Ponowienie nieudanego odczytu z zachowaniem tego, kto go zlecił."""
    require_access(actor_id)
    with get_conn() as conn:
        row = conn.execute(
            'SELECT payload FROM checking_requests WHERE id=%s FOR UPDATE',
            (request_id,),
        ).fetchone()
        if not row:
            raise ValueError('Nie znaleziono żądania odczytu.')
        value = row['payload']
        if actor_id is not None and value['actor_id'] != actor_id:
            raise PermissionError('Możesz ponowić tylko własne żądanie.')
        if not _needs_retry(value.get('state')):
            raise ValueError('To żądanie zostało już wykonane.')
        value.update(
            state='queued',
            attempts=int(value.get('attempts') or 0) + 1,
            retried_at=stamp(),
        )
        value.pop('error', None)
        _write(conn, value)
    return value


def purge_finished(older_than_days=7):
    """Usuń zakończone żądania starsze niż okres przechowywania."""
    from datetime import timedelta
    limit = datetime.now(ZoneInfo('Europe/Warsaw')) - timedelta(days=max(1, int(older_than_days)))
    with get_conn() as conn:
        return conn.execute("""
            DELETE FROM checking_requests WHERE state IN ('completed','partial','failed') AND finished_at < %s
        """, (limit,)).rowcount


@office_endpoint(
    'app.services.checking_refresh.run_now'
)
def run_now(
    actor_id,
    kind,
    company_ids,
    period,
    sources=None,
):
    value = _request(
        actor_id,
        kind,
        company_ids,
        period,
    )

    if sources is not None:
        if (
            kind != 'declaration_year'
            or not sources
            or not isinstance(
                sources,
                (list, tuple),
            )
            or set(sources) - {
                'platnik',
                'enova',
            }
        ):
            raise ValueError(
                'Wybierz Płatnika lub Enovę '
                'jako źródło deklaracji.'
            )

        value['sources'] = sorted(
            set(sources)
        )

    with advisory_lock(READ_LOCK) as acquired:
        if not acquired:
            raise ValueError(
                'Trwa już pobieranie danych. '
                'Poczekaj na jego zakończenie '
                'i spróbuj ponownie.'
            )

        value.update(
            state='running',
            execution='direct',
            started_at=stamp(),
        )

        _save(value)

        try:
            results = _execute(
                value,
                None,
            )

            if results is None:
                raise ValueError(
                    'Źródło danych jest zajęte '
                    'przez inny odczyt. Ponów '
                    'pobranie po jego zakończeniu.'
                )

            value.update(
                state=_result_state(results),
                results=results,
                finished_at=stamp(),
            )

        except Exception as error:
            from app.remote.secrets import safe_error

            value.update(
                state='failed',
                finished_at=stamp(),
                error=safe_error(error),
            )

        _save(value)

        return value


@office_endpoint(
    'app.services.checking_refresh.enqueue'
)
def enqueue(
    actor_id,
    kind,
    company_ids,
    period,
):
    value = _request(
        actor_id,
        kind,
        company_ids,
        period,
    )

    with get_conn() as conn:
        conn.execute(
            'SELECT pg_advisory_xact_lock(748218)'
        )

        existing = conn.execute("""
            SELECT payload FROM checking_requests
            WHERE actor_id=%s AND kind=%s AND company_ids=%s::integer[] AND period=%s AND state IN ('queued','running')
            ORDER BY created_at,id LIMIT 1
        """, (actor_id, kind, value['company_ids'], str(period))).fetchone()
        if existing:
            return existing['payload']

        counts = conn.execute("""
            SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE actor_id=%s) AS actor
            FROM checking_requests WHERE state IN ('queued','running')
        """, (actor_id,)).fetchone()
        if counts['total'] >= 200 or counts['actor'] >= 5:
            raise ValueError('Kolejka odczytu jest pełna. Poczekaj na zakończenie poprzednich żądań.')
        _write(conn, value)

    return value


@office_endpoint(
    'app.services.checking_refresh.latest'
)
def latest(actor_id):
    require_access(actor_id)

    with get_conn() as conn:
        row = conn.execute("""
            SELECT payload FROM checking_requests WHERE actor_id=%s ORDER BY created_at DESC,id DESC LIMIT 1
        """, (actor_id,)).fetchone()
        if row and row['payload']['state'] == 'running' and _try_recover_interrupted(conn):
            row = conn.execute('SELECT payload FROM checking_requests WHERE id=%s',
                               (row['payload']['id'],)).fetchone()
    if not row:
        return None
    value = row['payload']
    scope(actor_id, value['company_ids'])
    return value


def recover_interrupted():
    with get_conn() as conn:
        _try_recover_interrupted(conn)


def _try_recover_interrupted(conn):
    # Use the same lock as the executors. A worker restart or a status poll
    # can happen while a direct read in another process is still running.
    if not conn.execute('SELECT pg_try_advisory_xact_lock(%s)', (READ_LOCK,)).fetchone()[0]:
        return False
    _recover_interrupted(conn)
    return True


def _recover_interrupted(conn):
    finished_at = stamp()
    conn.execute("""
        UPDATE checking_requests SET state='failed', finished_at=%s,
            payload=payload || jsonb_build_object('state','failed','finished_at',%s::text,'error',%s::text)
        WHERE state='running' AND payload->>'execution'='direct'
    """, (finished_at, finished_at, 'Pobieranie zostało przerwane. Uruchom je ponownie.'))
    conn.execute("""
        UPDATE checking_requests SET state='queued',
            payload=payload || jsonb_build_object('state','queued','resumed_at',%s::text)
        WHERE state='running' AND payload->>'execution' IS DISTINCT FROM 'direct'
    """, (finished_at,))


def _execute(
    value,
    stop,
):
    companies = scope(
        value['actor_id'],
        value['company_ids'],
    )

    if value['kind'] == 'declaration_year':
        from app.services.declaration_year import run

        return run(
            value,
            stop,
        )

    if value['kind'] == 'platnik_annual':
        from app.integrations.platnik.declarations import (
            import_year,
        )
        from app.integrations.platnik.links import company_declaration_error

        result = import_year(
            value['period'],
            company_ids=value['company_ids'],
        )

        if result.get('mode') == 'busy':
            return None

        if result.get('mode') == 'disabled':
            raise ValueError(
                'Odczyt formularzy ZUS jest '
                'wyłączony w ustawieniach Płatnika.'
            )

        results = []
        for row in companies:
            error = company_declaration_error(result, row['id'])
            entry = {'company_id': row['id'], 'ok': not error, 'year': value['period']}
            if error:
                entry['error'] = error
            results.append(entry)
        return results

    if value['kind'] == 'platnik_declarations':
        from app.integrations.platnik.declarations import (
            import_months,
        )
        from app.integrations.platnik.insurance import company_registry_error
        from app.integrations.platnik.links import company_declaration_error

        result = import_months(
            [
                previous_month(value['period']),
                value['period'],
            ],
            company_ids=value['company_ids'],
        )

        if result.get('mode') == 'busy':
            return None

        if result.get('mode') == 'disabled':
            raise ValueError(
                'Odczyt RCA/DRA jest wyłączony '
                'w ustawieniach Płatnika.'
            )

        results = []
        for row in companies:
            entry = {'company_id': row['id'], 'ok': True, 'months': result['months']}
            if error := company_declaration_error(result, row['id']):
                entry.update(ok=False, error=error)
            elif error := company_registry_error(result, row['id']):
                entry.update(ok=False, monthly_imported=True, error=error)
            results.append(entry)
        return results

    if value['kind'] == 'ezla':
        from app.integrations.ezla.settings import (
            load_settings,
        )
        from app.integrations.ezla.sync import (
            run_sync,
        )

        configured = set(
            load_settings()['companies']
        )

        selected = [
            row['id']
            for row in companies
            if row['id'] in configured
        ]

        results = [
            {
                'company_id': row['id'],
                'state': 'failed',
                'error': (
                    'Firma nie jest skonfigurowana '
                    'w usłudze e-ZLA.'
                ),
            }
            for row in companies
            if row['id'] not in configured
        ]

        if selected:
            batch = run_sync(
                company_ids=selected,
                stop=stop,
            )

            if (
                batch.get('mode') == 'busy'
                or (
                    stop is not None
                    and stop.is_set()
                )
            ):
                return None

            results += batch.get(
                'results',
                [],
            )

        return results

    if value['kind'] == 'platnik':
        from app.integrations.platnik.holidays import (
            import_year,
        )

        result = import_year(
            value['period'],
            company_ids=value['company_ids'],
        )

        if result.get('mode') == 'busy':
            return None

        return [
            {
                'company_id': row['id'],
                'ok': True,
                'year': value['period'],
            }
            for row in companies
        ]

    from app.integrations.enova.connection_settings import (
        load_connection,
    )
    from app.integrations.enova.checking import (
        CheckingBusy,
        run_checking_import,
    )
    from app.integrations.enova.document_counts import (
        normalize_nip,
        sources_by_nip,
    )

    config = load_connection()
    indexed_sources = sources_by_nip(config['sources'])
    results = []

    for company in companies:
        if (
            stop is not None
            and stop.is_set()
        ):
            return None

        scope(
            value['actor_id'],
            [company['id']],
        )

        value['current_company_id'] = company['id']
        value['processed'] = len(results)

        _save(value)

        try:
            nip = normalize_nip(
                company['nip']
            )

            sources = indexed_sources.get(nip, [])

            if len(sources) != 1:
                raise ValueError(
                    'Brak jednoznacznego powiązania '
                    'firmy z bazą Enovy. Uzupełnij '
                    'połączenie w ustawieniach.'
                )

            batch = run_checking_import(
                {
                    **config,
                    'enabled': True,
                    'sources': sources,
                },
                apply=True,
                months=[
                    previous_month(
                        value['period']
                    ),
                    value['period'],
                ],
                force=True,
            )

            results += [
                {
                    **row,
                    'company_id': company['id'],
                }
                for row in batch
            ]

        except CheckingBusy:
            # The daily/yearly reader owns the same
            # lock; retry after its release.
            return None

        except Exception as exc:
            results.append(
                {
                    'company_id': company['id'],
                    'ok': False,
                    'error': (
                        str(exc)
                        if isinstance(
                            exc,
                            ValueError,
                        )
                        else type(exc).__name__
                    ),
                }
            )

    return results


def process_next(stop=None):
    with advisory_lock(READ_LOCK) as acquired:
        if not acquired:
            return {
                'mode': 'busy',
            }

        with get_conn() as conn:
            _recover_interrupted(conn)
            row = conn.execute("""
                SELECT payload FROM checking_requests WHERE state='queued'
                ORDER BY created_at,id LIMIT 1 FOR UPDATE
            """).fetchone()
            if not row:
                return {'mode': 'idle'}
            value = row['payload']
            value.pop('execution', None)  # A retried direct request now belongs to the worker.
            value.update(state='running', started_at=stamp(), attempts=int(value.get('attempts') or 0))
            _write(conn, value)

        try:
            results = _execute(
                value,
                stop,
            )

            if results is None:
                value.update(
                    state='queued',
                )

            else:
                value.update(
                    state=_result_state(results),
                    results=results,
                    finished_at=stamp(),
                )

        except Exception as exc:
            value.update(
                state='failed',
                finished_at=stamp(),
                error=(
                    str(exc)
                    if isinstance(
                        exc,
                        (
                            ValueError,
                            PermissionError,
                        ),
                    )
                    else type(exc).__name__
                ),
            )

        _save(value)

        return {
            'request_id': value['id'],
            'state': value['state'],
        }
