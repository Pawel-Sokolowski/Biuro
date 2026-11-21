"""Worker-owned calendar-year filing reads. No amounts or workflow stages."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn
from app.db.checking_storage import read_snapshot, save_snapshot
from app.db.import_audit import log_import
from app.companies.checking_obligations import add_imported
from app.integrations.enova.checking import validate_declarations,CheckingBusy
from app.integrations.enova.document_counts import fetch_counts,normalize_nip,sources_by_nip
from app.integrations.enova.jpk import save_jpk, validate_jpk
from app.services.worker_activity import record


def fetch_enova(config, source, months):
    if not source.get('declarations_query'):
        raise ValueError('Brak adaptera deklaracji Enovy dla firmy.')
    declarations = validate_declarations(fetch_counts(config,{**source,'count_query':source['declarations_query']},
                                                     months,result_kind='declarations'),source,months)
    jpk = None
    if source.get('jpk_query'):
        jpk = validate_jpk(fetch_counts(config,{**source,'count_query':source['jpk_query']},
                                       months,result_kind='jpk'),source,months)
    return declarations, jpk


def read_enova(config, source, company_id, months):
    with get_conn() as conn:
        if not conn.execute('SELECT pg_try_advisory_xact_lock(748210)').fetchone()[0]:
            raise CheckingBusy('Trwa inny odczyt Enovy.')
        declarations, jpk = fetch_enova(config, source, months)
        stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
        for month in months:
            snapshot = read_snapshot(conn, 'enova_checking', company_id, month, lock=True) or {}
            snapshot.update(declarations=declarations[month],declarations_imported_at=stamp,database=source['database'])
            snapshot.setdefault('imported_at',stamp)
            save_snapshot(conn, 'enova_checking', company_id, month, snapshot)
            add_imported(conn,company_id,month,declarations[month],'enova')
            if jpk is not None:
                save_jpk(conn, company_id, month, {**jpk[month], 'imported_at': stamp, 'database': source['database']})
        documents = sum(len(entries) for entries in declarations.values())
        log_import(conn, 'enova_declarations', source=source['database'], updated=documents,
                   details={'company_id': company_id, 'months': months, 'documents': documents,
                            'jpk': jpk is not None})
    return {'company_id':company_id,'source':'enova','ok':True,'months':months,
            'documents':documents}


def run(value, stop):
    from app.services.checking_refresh import scope,_save
    from app.integrations.platnik.declarations import import_months,import_year
    from app.integrations.platnik.insurance import company_registry_error
    from app.integrations.platnik.links import company_declaration_error
    from app.integrations.platnik.holidays import PlatnikSqlError
    from app.integrations.enova.connection_settings import load_connection
    companies = scope(value['actor_id'],value['company_ids'])
    months = [f"{value['period']}-{number:02d}" for number in range(1,13)]
    results = []
    if stop is not None and stop.is_set():
        return None
    sources = set(value.get('sources') or ('platnik','enova'))
    if 'platnik' in sources:
        # Płatnik is one shared database. Read the year once for the whole selection.
        value.update(current_source='platnik',processed=0)
        _save(value)
        monthly_applied = False
        try:
            result = import_months(months,company_ids=value['company_ids'])
            if result.get('mode')=='busy':
                return None
            if result.get('mode')=='disabled':
                raise ValueError('Odczyt formularzy ZUS jest wyłączony.')
            monthly_applied = result.get('mode') in ('applied', 'partial')
            if stop is not None and stop.is_set():
                return None
            yearly = import_year(value['period'],company_ids=value['company_ids'])
            if yearly.get('mode')=='busy':
                if value.get('execution')=='direct':
                    raise ValueError('Odczyt miesięczny zapisano, ale źródło IWA/ZSWA jest zajęte. Ponów pobranie raportów rocznych.')
                return None
            if yearly.get('mode')=='disabled':
                raise ValueError('Odczyt IWA/ZSWA jest wyłączony.')
            for company in companies:
                entry = {'company_id':company['id'],'source':'platnik','ok':True,'months':months,
                         'batch_documents':result.get('documents',0),'batch_annual_documents':yearly.get('documents',0),
                         'batch_companies':len(companies)}
                monthly_error = company_declaration_error(result, company['id'])
                error = (monthly_error or company_declaration_error(yearly, company['id'])
                         or company_registry_error(result, company['id']))
                if error:
                    entry.update(ok=False, monthly_imported=monthly_applied and not monthly_error, error=error)
                results.append(entry)
        except Exception as error:
            for company in companies:
                record('platnik','failed',company_id=company['id'],error_type=type(error).__name__)
                results.append({'company_id':company['id'],'source':'platnik','ok':False,
                                'monthly_imported':monthly_applied and not company_declaration_error(result, company['id']),
                                'batch_documents':result.get('documents',0) if monthly_applied else 0,
                                'batch_annual_documents':0,
                                'error':str(error) if isinstance(error,(ValueError,PlatnikSqlError)) else type(error).__name__})
    # A broken Enova configuration must not suppress a successful Płatnik read.
    if 'enova' not in sources:
        return results
    config,config_error = None,None
    try:
        config = load_connection()
        indexed_sources = sources_by_nip(config['sources'])
    except Exception as error:
        config_error = error
    for index,company in enumerate(companies):
        if stop is not None and stop.is_set():
            return None
        scope(value['actor_id'],[company['id']])
        value.update(current_source='enova',current_company_id=company['id'],processed=index)
        _save(value)
        record('enova','reading',company_id=company['id'])
        try:
            if config_error is not None:
                raise config_error
            sources = indexed_sources.get(normalize_nip(company['nip']), [])
            if len(sources)!=1:
                raise ValueError('Brak jednoznacznego powiązania firmy z bazą Enovy.')
            results.append(read_enova(config,sources[0],company['id'],months))
            record('enova','saved',company_id=company['id'])
        except CheckingBusy:
            return None
        except Exception as error:
            record('enova','failed',company_id=company['id'],error_type=type(error).__name__)
            results.append({'company_id':company['id'],'source':'enova','ok':False,
                            'error':str(error) if isinstance(error,ValueError) else type(error).__name__})
    return results
