"""Durable, coalesced Enova reads caused only by changed monthly workflow stages."""
from datetime import datetime, timedelta, timezone
import uuid
from app.db.dao_conn import get_conn

ERROR_MESSAGES = {
    'EnovaMappingMissing': 'Brak jednoznacznego przypisania bazy Enovy.',
    'EnovaDocumentsFailed': 'Nie ukończono odczytu dokumentów lub JPK. Sprawdź historię importów.',
    'EnovaCheckingFailed': 'Nie ukończono odczytu do Sprawdzania. Sprawdź historię importów.',
}


def record_change(conn, company_id, month, before, payload):
    previous = dict(before) if before else {}
    old = (previous.get('etap') or 'Brak',previous.get('etap_kadry') or 'Brak')
    new = (payload.get('etap','Brak'),payload.get('etap_kadry') if payload.get('etap_kadry') is not None else old[1])
    if old == new:
        return
    if old[0] != new[0] and new[0] == 'Wysłane wyniki':
        from app.integrations.enova.document_counts import count_check_key
        conn.execute('DELETE FROM app_settings WHERE klucz=%s', (count_check_key(company_id, month),))
    conn.execute("""
        INSERT INTO enova_stage_requests(company_id,period,revision) VALUES (%s,%s,%s)
        ON CONFLICT(company_id,period) DO UPDATE SET revision=EXCLUDED.revision,
            attempts=0, next_attempt_at=NULL, last_error=NULL
    """, (company_id, month, uuid.uuid4().hex))


def run(stop=None, *, now=None):
    from app.integrations.enova.connection_settings import load_connection
    from app.integrations.enova.document_counts import normalize_nip,run_import,month_batches,sources_by_nip
    from app.integrations.enova.checking import run_checking_import
    from app.services.worker_activity import record
    config = load_connection()
    if not config['enabled']:
        return {'mode':'disabled'}
    with get_conn() as conn:
        pending = conn.execute("""
            SELECT r.company_id,r.period,r.revision,r.attempts,f.nip FROM enova_stage_requests r
            JOIN firmy f ON f.id=r.company_id
            WHERE r.next_attempt_at IS NULL OR r.next_attempt_at <= %s
            ORDER BY r.company_id,r.period
        """, (now or datetime.now(timezone.utc),)).fetchall()
        if not pending:
            waiting = conn.execute('SELECT min(next_attempt_at) FROM enova_stage_requests').fetchone()[0]
            return {'mode': 'retry_waiting' if waiting else 'idle', 'results': []}
    groups = {}
    for row in pending:
        groups.setdefault(row['company_id'], []).append(row)
    results, consumed, retries = [], [], []
    indexed_sources = sources_by_nip(config['sources'])
    for company_id,events in groups.items():
        if stop is not None and stop.is_set():
            break
        months = [row['period'] for row in events]
        reason = 'EnovaMappingMissing'
        try:
            sources = indexed_sources.get(normalize_nip(events[0]['nip']), [])
            if len(sources)!=1:
                raise ValueError('Brak jednoznacznego mapowania bazy Enovy.')
            selected = {**config,'sources':sources,'months_back':1}
            documents, checked = [], []
            for periods in month_batches(months):
                if stop is not None and stop.is_set():
                    break
                reason = 'EnovaDocumentsFailed'
                documents.extend(run_import(selected,apply=True,months=periods,stop=stop))
                if stop is not None and stop.is_set():
                    break
                if config.get('checking_enabled',False):
                    reason = 'EnovaCheckingFailed'
                    checked.extend(run_checking_import(selected,apply=True,months=periods,force=True,stop=stop))
            if stop is not None and stop.is_set():
                break
            if (not documents and not checked) or any(not item['ok'] for item in documents):
                reason = 'EnovaDocumentsFailed'
                raise ValueError('Nie ukończono odczytu dokumentów lub JPK.')
            if config.get('checking_enabled', False) and (
                not checked or any(not item['ok'] or item.get('hr_failed') for item in checked)
            ):
                reason = 'EnovaCheckingFailed'
                raise ValueError('Nie ukończono odczytu do Sprawdzania.')
            consumed.extend(events)
            results.append({'company_id':company_id,'ok':True,'months':months})
        except Exception:
            if stop is not None and stop.is_set():
                break
            failed_at = now or datetime.now(timezone.utc)
            for row in events:
                delay = min(3600, 60 * 2 ** min(row['attempts'], 6))
                retries.append((company_id, row['period'], row['revision'], failed_at + timedelta(seconds=delay), reason))
            record('enova', 'failed', company_id=company_id, error_type=reason)
            results.append({'company_id': company_id, 'ok': False, 'months': months, 'error_type': reason})
    if consumed or retries:
        with get_conn() as conn:
            # Compare revisions so edits arriving during the read survive this batch.
            if consumed:
                conn.execute("""
                    DELETE FROM enova_stage_requests r USING
                        unnest(%s::integer[],%s::text[],%s::text[]) AS done(company_id,period,revision)
                    WHERE (r.company_id,r.period,r.revision) = (done.company_id,done.period,done.revision)
                """, ([row['company_id'] for row in consumed], [row['period'] for row in consumed],
                      [row['revision'] for row in consumed]))
            if retries:
                conn.execute("""
                    UPDATE enova_stage_requests r SET attempts=r.attempts+1,
                        next_attempt_at=retry.next_attempt_at, last_error=retry.reason
                    FROM unnest(%s::integer[],%s::text[],%s::text[],%s::timestamptz[],%s::text[])
                        AS retry(company_id,period,revision,next_attempt_at,reason)
                    WHERE (r.company_id,r.period,r.revision) = (retry.company_id,retry.period,retry.revision)
                """, tuple(map(list, zip(*retries))))
    if retries:
        return {'mode': 'partial' if consumed else 'failed', 'results': results}
    if stop is not None and stop.is_set() and not consumed:
        return {'mode': 'cancelled', 'results': results}
    return {'mode': 'applied' if results else 'cancelled', 'results': results}
