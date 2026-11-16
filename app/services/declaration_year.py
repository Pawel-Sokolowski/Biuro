"""Worker-owned calendar-year filing reads. No amounts or workflow stages."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn
from app.companies.checking_obligations import add_imported
from app.integrations.enova.checking import validate_declarations,CheckingBusy
from app.integrations.enova.document_counts import fetch_counts,normalize_nip
from app.integrations.enova.jpk import validate_jpk
from app.services.worker_activity import record


def read_enova(config, source, company_id, months):
    if not source.get('declarations_query'):
        raise ValueError('Brak adaptera deklaracji Enovy dla firmy.')
    with get_conn() as conn:
        if not conn.execute('SELECT pg_try_advisory_xact_lock(748210)').fetchone()[0]:
            raise CheckingBusy('Trwa inny odczyt Enovy.')
        declarations = validate_declarations(fetch_counts(config,{**source,'count_query':source['declarations_query']},
                                                         months,result_kind='declarations'),source,months)
        jpk = None
        if source.get('jpk_query'):
            jpk = validate_jpk(fetch_counts(config,{**source,'count_query':source['jpk_query']},
                                           months,result_kind='jpk'),source,months)
        stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
        for month in months:
            key = f'enova_checking_{company_id}_{month}'
            row = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s FOR UPDATE',(key,)).fetchone()
            snapshot = json.loads(row[0]) if row else {}
            snapshot.update(declarations=declarations[month],declarations_imported_at=stamp,database=source['database'])
            snapshot.setdefault('imported_at',stamp)
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                         'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',(key,json.dumps(snapshot)))
            add_imported(conn,company_id,month,declarations[month],'enova')
            if jpk is not None:
                conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                             'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                             (f'enova_jpk_{company_id}_{month}',json.dumps({**jpk[month],'imported_at':stamp,'database':source['database']})))
                if jpk[month].get('form_variant') or jpk[month]['status'] not in ('unknown','not_applicable'):
                    add_imported(conn,company_id,month,[{'form':'JPK_VAT',**jpk[month]}],'enova-jpk')
    return {'company_id':company_id,'source':'enova','ok':True,'months':months,
            'documents':sum(len(entries) for entries in declarations.values())}


def run(value, stop):
    from app.services.checking_refresh import scope,_save
    from app.integrations.platnik.declarations import import_months,import_year
    from app.integrations.enova.connection_settings import load_connection
    companies = scope(value['actor_id'],value['company_ids'])
    months = [f"{value['period']}-{number:02d}" for number in range(1,13)]
    config = load_connection()
    results = []
    for index,company in enumerate(companies):
        if stop is not None and stop.is_set():
            return None
        scope(value['actor_id'],[company['id']])
        value.update(current_company_id=company['id'],processed=index)
        _save(value)
        for source in ('platnik','enova'):
            if stop is not None and stop.is_set():
                return None
            record(source,'reading',company_id=company['id'])
            try:
                if source=='platnik':
                    result = import_months(months,company_ids=[company['id']])
                    if result.get('mode')=='busy':
                        return None
                    if result.get('mode')=='disabled':
                        raise ValueError('Odczyt formularzy ZUS jest wyłączony.')
                    yearly = import_year(value['period'],company_ids=[company['id']])
                    if yearly.get('mode')=='busy':
                        return None
                    results.append({'company_id':company['id'],'source':source,'ok':True,'months':months,
                                    'documents':result.get('documents',0),'annual_documents':yearly.get('documents',0)})
                else:
                    sources = [s for s in config['sources'] if normalize_nip(s['nip'])==normalize_nip(company['nip'])]
                    if len(sources)!=1:
                        raise ValueError('Brak jednoznacznego powiązania firmy z bazą Enovy.')
                    results.append(read_enova(config,sources[0],company['id'],months))
                record(source,'saved',company_id=company['id'])
            except CheckingBusy:
                return None
            except Exception as error:
                record(source,'failed',company_id=company['id'],error_type=type(error).__name__)
                results.append({'company_id':company['id'],'source':source,'ok':False,
                                'error':str(error) if isinstance(error,ValueError) else type(error).__name__})
    return results
