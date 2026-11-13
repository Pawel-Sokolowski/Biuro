"""Selected-company read requests, executed by the existing server worker."""
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from app.db.dao_conn import get_conn, get_coordination_conn
from app.integrations.enova.accounting import require_access
from app.security.authorization import assigned_company_ids
from app.utils.months import month_bounds, previous_month

PREFIX = 'checking_refresh_'
KINDS = ('enova', 'ezla', 'platnik')


def stamp():
    return datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()


def scope(actor_id, company_ids):
    require_access(actor_id)
    if (not isinstance(company_ids, list) or not company_ids or len(company_ids)>500
            or any(type(fid) is not int or fid<=0 for fid in company_ids)):
        raise ValueError('Wybierz jedną lub kilka firm.')
    ids = sorted(set(company_ids))
    if not set(ids)<=set(assigned_company_ids(actor_id)):
        raise PermissionError('Brak dostępu do części wybranych firm.')
    with get_conn() as conn:
        rows = conn.execute('SELECT id,nazwa,nip FROM firmy WHERE id=ANY(%s) ORDER BY id',(ids,)).fetchall()
    if len(rows)!=len(ids):
        raise ValueError('Część wybranych firm już nie istnieje.')
    return [dict(row) for row in rows]


def _save(value):
    with get_conn() as conn:
        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                     'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                     (PREFIX+value['id'],json.dumps(value,ensure_ascii=False)))


def enqueue(actor_id, kind, company_ids, period):
    scope(actor_id,company_ids)
    if kind not in KINDS:
        raise ValueError('Nieprawidłowe źródło odczytu.')
    if kind=='platnik':
        if type(period) is not int or not 2024<=period<=2100:
            raise ValueError('Wybierz rok zestawienia Płatnika.')
    else:
        month_bounds(period)
    value = {'id':uuid.uuid4().hex,'actor_id':actor_id,'kind':kind,
             'company_ids':sorted(set(company_ids)),'period':period,'state':'queued','created_at':stamp()}
    with get_conn() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(748218)')
        rows = conn.execute('SELECT wartosc FROM app_settings WHERE starts_with(klucz,%s)',(PREFIX,)).fetchall()
        pending = [json.loads(row[0]) for row in rows if json.loads(row[0]).get('state') in ('queued','running')]
        for existing in pending:
            if all(existing.get(key)==value[key] for key in ('actor_id','kind','company_ids','period')):
                return existing
        if len(pending)>=200 or sum(row['actor_id']==actor_id for row in pending)>=5:
            raise ValueError('Kolejka odczytu jest pełna. Poczekaj na zakończenie poprzednich żądań.')
        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)',(PREFIX+value['id'],json.dumps(value)))
    return value


def latest(actor_id):
    require_access(actor_id)
    with get_conn() as conn:
        rows = conn.execute('SELECT wartosc FROM app_settings WHERE starts_with(klucz,%s)',(PREFIX,)).fetchall()
    values = sorted((json.loads(row[0]) for row in rows),key=lambda value:value['created_at'],reverse=True)
    for value in values:
        if value['actor_id']==actor_id:
            scope(actor_id,value['company_ids'])
            return value
    return None


def recover_interrupted():
    # Called only after acquiring worker leadership. Reads/upserts are idempotent;
    # no outbound messages or Enova writes exist in this queue.
    with get_conn() as conn:
        rows = conn.execute('SELECT klucz,wartosc FROM app_settings WHERE starts_with(klucz,%s)',(PREFIX,)).fetchall()
        for row in rows:
            value = json.loads(row['wartosc'])
            if value.get('state')=='running':
                value.update(state='queued',resumed_at=stamp())
                conn.execute('UPDATE app_settings SET wartosc=%s WHERE klucz=%s',(json.dumps(value),row['klucz']))


def _execute(value, stop):
    companies = scope(value['actor_id'],value['company_ids'])  # Recheck after waiting in queue.
    if value['kind']=='ezla':
        from app.integrations.ezla.settings import load_settings
        from app.integrations.ezla.sync import run_sync
        configured = set(load_settings()['companies'])
        selected = [row['id'] for row in companies if row['id'] in configured]
        results = [{'company_id':row['id'],'state':'failed','error':'Firma nie jest skonfigurowana w usłudze e-ZLA.'}
                   for row in companies if row['id'] not in configured]
        if selected:
            batch = run_sync(company_ids=selected,stop=stop)
            if batch.get('mode')=='busy' or (stop is not None and stop.is_set()):
                return None
            results += batch.get('results',[])
        return results
    if value['kind']=='platnik':
        from app.integrations.platnik.holidays import import_year
        result = import_year(value['period'],company_ids=value['company_ids'])
        if result.get('mode')=='busy':
            return None
        return [{'company_id':row['id'],'ok':True,'year':value['period']} for row in companies]
    from app.integrations.enova.connection_settings import load_connection
    from app.integrations.enova.checking import run_checking_import,CheckingBusy
    from app.integrations.enova.document_counts import normalize_nip
    config = load_connection()
    results = []
    for company in companies:
        if stop is not None and stop.is_set():
            return None
        scope(value['actor_id'],[company['id']])
        value['current_company_id'] = company['id']
        value['processed'] = len(results)
        _save(value)
        try:
            nip = normalize_nip(company['nip'])
            sources = [source for source in config['sources'] if normalize_nip(source['nip'])==nip]
            if len(sources)!=1:
                raise ValueError('Brak jednoznacznego powiązania firmy z bazą Enovy. Uzupełnij połączenie w ustawieniach.')
            batch = run_checking_import({**config,'enabled':True,'sources':sources},apply=True,
                                       months=[previous_month(value['period']),value['period']],force=True)
            results += [{**row,'company_id':company['id']} for row in batch]
        except CheckingBusy:
            return None  # The daily/yearly reader owns the same lock; retry after its release.
        except Exception as exc:
            results.append({'company_id':company['id'],'ok':False,
                            'error':str(exc) if isinstance(exc,ValueError) else type(exc).__name__})
    return results


def process_next(stop=None):
    with get_coordination_conn() as lock:
        if not lock.execute('SELECT pg_try_advisory_xact_lock(748219)').fetchone()[0]:
            return {'mode':'busy'}
        with get_conn() as conn:
            rows = conn.execute('SELECT wartosc FROM app_settings WHERE starts_with(klucz,%s)',(PREFIX,)).fetchall()
        queued = sorted((json.loads(row[0]) for row in rows if json.loads(row[0]).get('state')=='queued'),key=lambda row:row['created_at'])
        if not queued:
            return {'mode':'idle'}
        value = queued[0]
        value.update(state='running',started_at=stamp())
        _save(value)
        try:
            results = _execute(value,stop)
            if results is None:
                value.update(state='queued')
            else:
                failed = sum(not row.get('ok',row.get('state')=='ok') for row in results)
                state = 'failed' if not results or failed==len(results) else 'partial' if failed else 'completed'
                value.update(state=state,results=results,finished_at=stamp())
        except Exception as exc:
            value.update(state='failed',finished_at=stamp(),
                         error=str(exc) if isinstance(exc,(ValueError,PermissionError)) else type(exc).__name__)
        _save(value)
        return {'request_id':value['id'],'state':value['state']}
