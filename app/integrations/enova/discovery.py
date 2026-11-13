"""Read-only discovery using the same company identity as the document importer."""
from collections import defaultdict
from app.integrations.enova.document_counts import normalize_nip, sql_connection_string
from app.integrations.enova.odbc_support import (load_odbc, resolve_driver, validate_server, describe_sql_error,
    SqlOperationError,DEFAULT_QUERY_TIMEOUT,validate_query_timeout)


def query_timeout(config):
    return validate_query_timeout(config.get('discovery_query_timeout',DEFAULT_QUERY_TIMEOUT))


def discover(config, *, password='', progress=None):
    validate_server(config.get('server'))
    timeout = query_timeout(config)
    if not config.get('username', '').strip():
        raise ValueError('Uzupełnij login SQL.')
    if not password:
        from app.integrations.enova.connection_settings import credentials
        if not config.get('password_enc'):
            raise ValueError('Uzupełnij hasło SQL.')
        _, password = credentials(config)
    odbc = load_odbc()
    config = {**config, 'driver': resolve_driver(config, odbc)}
    main_database = str(config.get('database') or '').strip() or 'master'
    def connect(database):
        conn = odbc.connect(sql_connection_string(config,database,config['username'],password),
                            timeout=15,attrs_before={101:1})
        try:
            conn.timeout = timeout
        except Exception:
            conn.close()
            raise
        return conn
    root = None
    if progress:
        progress(0,0,main_database)
    try:
        root = connect(main_database)
        cursor = root.cursor()
        # Enumerate metadata only. HAS_DBACCESS(name) can open/check every
        # database inside this one statement, making it slow on large instances.
        # Actual access is checked independently when connecting to each database.
        cursor.execute('''SELECT name FROM sys.databases
            WHERE database_id>4 AND state=0 ORDER BY name''')
        databases = [str(row[0]) for row in cursor.fetchmany(501)]
    except Exception as error:
        diagnostic = describe_sql_error(error,secrets=(password,config.get('password_enc')))
        raise SqlOperationError(diagnostic,{**config,'database':main_database},'Enova — odczyt listy baz') from None
    finally:
        if root is not None:
            root.close()
    if len(databases)>500:
        raise ValueError('Serwer ma ponad 500 dostępnych baz. Ogranicz widoczność baz dla konta integracji lub przypisz je ręcznie.')
    matches = defaultdict(list)
    warnings = []
    errors = []
    records = []
    scanned = 0
    if progress:
        progress(0,len(databases),'')
    for index,database in enumerate(databases,1):
        record = {'database': database, 'nips': [], 'reason': ''}
        records.append(record)
        if progress:
            progress(index-1,len(databases),database)
        conn = None
        try:
            conn = connect(database)
            cursor = conn.cursor()
            cursor.execute("SELECT COL_LENGTH('dbo.OddzialyFirmy','DeklaracjeNIP')")
            if not cursor.fetchone()[0]:
                record['reason'] = 'Brak rozpoznanej struktury Enovy lub dostępu do metadanych.'
                continue  # Other application / inaccessible identity metadata; do not inspect contractors.
            scanned += 1
            cursor.execute('SELECT DISTINCT DeklaracjeNIP FROM dbo.OddzialyFirmy')
            nips = set()
            rows = cursor.fetchmany(1001)
            if len(rows)>1000:
                record['reason'] = 'Zbyt wiele NIP-ów właściciela bazy.'
                warnings.append(database + ': zbyt wiele NIP-ów właściciela bazy; pominięto')
                continue
            for row in rows:
                if row[0] is not None and str(row[0]).strip():
                    try:
                        nips.add(normalize_nip(row[0]))
                    except ValueError:
                        warnings.append(database + ': nieprawidłowy NIP właściciela bazy')
                        nips.add('invalid')
            if len(nips)==1 and 'invalid' not in nips:
                matches[next(iter(nips))].append(database)
            else:
                record['reason'] = 'Brak jednego poprawnego NIP-u właściciela bazy.'
                warnings.append(database + ': brak jednego poprawnego NIP właściciela bazy')
            record['nips'] = sorted(nip for nip in nips if nip != 'invalid')
        except Exception as error:
            # Incomplete scans cannot establish uniqueness; never autofill from partial results.
            diagnostic = describe_sql_error(error,secrets=(password,config.get('password_enc')))
            errors.append(f"{database} [{diagnostic['sqlstate'] or 'brak SQLSTATE'}]: "
                          f"{diagnostic['hint']}\n{diagnostic['details']}")
            record['reason'] = errors[-1]
        finally:
            if conn is not None:
                conn.close()
            if progress:
                progress(index,len(databases),database)
    return {'matches':dict(matches),'warnings':warnings,'errors':errors,'scanned':scanned,'visible':len(databases),
            'databases': records}


def mapping_review(companies, current, result):
    """Explain every unmatched database without treating a partial scan as complete."""
    by_nip = defaultdict(list)
    for company in companies:
        try:
            by_nip[normalize_nip(company['nip'])].append(company['id'])
        except ValueError:
            pass
    assigned = {value.strip().casefold(): key for key, value in current.items() if value.strip()}
    rows = []
    for record in result.get('databases', []):
        database = record['database']
        company_id = assigned.get(database.casefold())
        reason = record['reason']
        if company_id is not None:
            reason = 'Przypisana do firmy w Biuro. ' + reason
        elif not reason:
            nip = record['nips'][0] if len(record['nips']) == 1 else ''
            if not by_nip.get(nip):
                reason = 'Brak firmy z tym NIP-em w Biuro.'
            elif len(by_nip[nip]) > 1:
                reason = 'NIP powielony w Biuro — wybierz firmę ręcznie.'
            elif len(result['matches'].get(nip, [])) > 1:
                reason = 'Kilka baz z tym NIP-em — wybierz właściwą bazę.'
            elif result['errors']:
                reason = 'Niepełny odczyt serwera — wymagane ręczne przypisanie.'
            else:
                reason = 'Firma ma już inne przypisanie lub baza wymaga ręcznego wyboru.'
        rows.append({**record, 'company_id': company_id, 'reason': reason})
    return rows


def propose(companies, current, result):
    """Keep manual mappings; ambiguous company NIPs and database copies need review."""
    by_nip = defaultdict(list)
    for row in companies:
        try:
            by_nip[normalize_nip(row['nip'])].append(row['id'])
        except ValueError:
            pass
    proposals = {}
    conflicts = []
    if result['errors']:
        return proposals, ['Niepełny odczyt serwera — automatyczne przypisania wstrzymane.']
    used = {str(value).strip().casefold() for value in current.values() if value}
    for nip, ids in by_nip.items():
        databases = result['matches'].get(nip,[])
        if len(ids)!=1 or len(databases)!=1:
            if databases:
                conflicts.append(nip + ': ' + ', '.join(databases) + (' (NIP powielony w Biuro)' if len(ids)!=1 else ''))
            continue
        if not current.get(ids[0],'').strip() and databases[0].casefold() not in used:
            proposals[ids[0]] = databases[0]
            used.add(databases[0].casefold())
    return proposals,conflicts
