"""Durable, coalesced Enova reads caused only by changed monthly workflow stages."""
import uuid
from app.db.dao_conn import get_conn


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
        ON CONFLICT(company_id,period) DO UPDATE SET revision=EXCLUDED.revision
    """, (company_id, month, uuid.uuid4().hex))


def run(stop=None):
    from app.integrations.enova.connection_settings import load_connection
    from app.integrations.enova.document_counts import normalize_nip,run_import,month_batches,sources_by_nip
    from app.integrations.enova.checking import run_checking_import
    config = load_connection()
    if not config['enabled']:
        return {'mode':'disabled'}
    with get_conn() as conn:
        pending = conn.execute("""
            SELECT r.company_id,r.period,r.revision,f.nip FROM enova_stage_requests r
            JOIN firmy f ON f.id=r.company_id ORDER BY r.company_id,r.period
        """).fetchall()
    groups = {}
    for row in pending:
        groups.setdefault(row['company_id'], []).append(row)
    if not groups:
        return {'mode':'idle','results':[]}
    results,consumed = [],[]
    indexed_sources = sources_by_nip(config['sources'])
    for company_id,events in groups.items():
        if stop is not None and stop.is_set():
            break
        months = [row['period'] for row in events]
        try:
            sources = indexed_sources.get(normalize_nip(events[0]['nip']), [])
            if len(sources)!=1:
                raise ValueError('Brak jednoznacznego mapowania bazy Enovy.')
            selected = {**config,'sources':sources,'months_back':1}
            batch = []
            for periods in month_batches(months):
                batch += run_import(selected,apply=True,months=periods)
                if config.get('checking_enabled',False):
                    batch += run_checking_import(selected,apply=True,months=periods,force=True)
            if not batch or any(not item['ok'] for item in batch):
                raise ValueError('Odczyt Enovy nie został ukończony.')
            consumed.extend(events)
            results.append({'company_id':company_id,'ok':True,'months':months})
        except Exception as error:
            results.append({'company_id':company_id,'ok':False,'error_type':type(error).__name__})
    if consumed:
        with get_conn() as conn:
            # Compare revisions so edits arriving during the read survive this batch.
            conn.execute("""
                DELETE FROM enova_stage_requests r USING
                    unnest(%s::integer[],%s::text[],%s::text[]) AS done(company_id,period,revision)
                WHERE (r.company_id,r.period,r.revision) = (done.company_id,done.period,done.revision)
            """, ([row['company_id'] for row in consumed], [row['period'] for row in consumed],
                  [row['revision'] for row in consumed]))
    if any(not result['ok'] for result in results):
        raise ValueError('Nie ukończono części odczytów Enovy po zmianie etapu. Żądania pozostają w kolejce.')
    return {'mode':'applied' if results else 'idle','results':results}
