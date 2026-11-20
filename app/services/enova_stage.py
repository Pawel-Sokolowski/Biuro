"""Durable, coalesced Enova reads caused only by changed monthly workflow stages."""
import json
import uuid
from app.db.dao_conn import get_conn

PREFIX = 'enova_stage_pending_'


def record_change(conn, company_id, month, before, payload):
    previous = dict(before) if before else {}
    old = (previous.get('etap') or 'Brak',previous.get('etap_kadry') or 'Brak')
    new = (payload.get('etap','Brak'),payload.get('etap_kadry') if payload.get('etap_kadry') is not None else old[1])
    if old == new:
        return
    if old[0] != new[0] and new[0] == 'Wysłane wyniki':
        from app.integrations.enova.document_counts import count_check_key
        conn.execute('DELETE FROM app_settings WHERE klucz=%s', (count_check_key(company_id, month),))
    value = {'company_id':company_id,'month':month,'revision':uuid.uuid4().hex}
    conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                 'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                 (f'{PREFIX}{company_id}_{month}',json.dumps(value)))


def run(stop=None):
    from app.integrations.enova.connection_settings import load_connection
    from app.integrations.enova.document_counts import normalize_nip,run_import,month_batches
    from app.integrations.enova.checking import run_checking_import
    config = load_connection()
    if not config['enabled']:
        return {'mode':'disabled'}
    with get_conn() as conn:
        pending = conn.execute('SELECT klucz,wartosc FROM app_settings WHERE starts_with(klucz,%s) ORDER BY klucz',
                               (PREFIX,)).fetchall()
    groups = {}
    for row in pending:
        value = json.loads(row['wartosc'])
        groups.setdefault(value['company_id'],[]).append((row,value))
    if not groups:
        return {'mode':'idle','results':[]}
    with get_conn() as conn:
        companies = {row['id']:dict(row) for row in conn.execute(
            'SELECT id,nip FROM firmy WHERE id=ANY(%s)',(list(groups),)).fetchall()}
    results,consumed = [],[]
    for company_id,events in groups.items():
        if stop is not None and stop.is_set():
            break
        if company_id not in companies:
            consumed += [row for row,value in events]
            continue
        months = sorted({value['month'] for row,value in events})
        try:
            sources = [source for source in config['sources']
                       if normalize_nip(source['nip'])==normalize_nip(companies[company_id]['nip'])]
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
            consumed += [row for row,value in events]
            results.append({'company_id':company_id,'ok':True,'months':months})
        except Exception as error:
            results.append({'company_id':company_id,'ok':False,'error_type':type(error).__name__})
    if consumed:
        with get_conn() as conn:
            # Compare revisions so edits arriving during the read survive this batch.
            conn.execute('DELETE FROM app_settings WHERE (klucz,wartosc) IN '
                         '(SELECT unnest(%s::text[]),unnest(%s::text[]))',
                         ([row['klucz'] for row in consumed],[row['wartosc'] for row in consumed]))
    if any(not result['ok'] for result in results):
        raise ValueError('Nie ukończono części odczytów Enovy po zmianie etapu. Żądania pozostają w kolejce.')
    return {'mode':'applied' if results else 'idle','results':results}
