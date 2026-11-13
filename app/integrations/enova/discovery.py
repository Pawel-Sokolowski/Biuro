"""Read-only discovery using the same company identity as the document importer."""
from collections import defaultdict
from app.integrations.enova.document_counts import normalize_nip, sql_connection_string
from app.integrations.enova.odbc_support import load_odbc, resolve_driver, validate_server


def discover(config, *, password=''):
    validate_server(config.get('server'))
    if not config.get('username', '').strip():
        raise ValueError('Uzupełnij login SQL.')
    if not password:
        from app.integrations.enova.connection_settings import credentials
        if not config.get('password_enc'):
            raise ValueError('Uzupełnij hasło SQL.')
        _, password = credentials(config)
    odbc = load_odbc()
    config = {**config, 'driver': resolve_driver(config, odbc)}
    def connect(database):
        conn = odbc.connect(sql_connection_string(config,database,config['username'],password),
                            timeout=10,attrs_before={101:1})
        conn.timeout = 10
        return conn
    root = connect('master')
    try:
        cursor = root.cursor()
        cursor.execute('''SELECT name FROM sys.databases
            WHERE database_id>4 AND state=0 AND HAS_DBACCESS(name)=1 ORDER BY name''')
        databases = [str(row[0]) for row in cursor.fetchmany(501)]
    finally:
        root.close()
    if len(databases)>500:
        raise ValueError('Serwer ma ponad 500 dostępnych baz. Ogranicz widoczność baz dla konta integracji lub przypisz je ręcznie.')
    matches = defaultdict(list)
    warnings = []
    errors = []
    scanned = 0
    for database in databases:
        conn = None
        try:
            conn = connect(database)
            cursor = conn.cursor()
            cursor.execute("SELECT COL_LENGTH('dbo.OddzialyFirmy','DeklaracjeNIP')")
            if not cursor.fetchone()[0]:
                continue  # Other application / inaccessible identity metadata; do not inspect contractors.
            scanned += 1
            cursor.execute('SELECT DISTINCT DeklaracjeNIP FROM dbo.OddzialyFirmy')
            nips = set()
            rows = cursor.fetchmany(1001)
            if len(rows)>1000:
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
                warnings.append(database + ': brak jednego poprawnego NIP właściciela bazy')
        except Exception:
            # Incomplete scans cannot establish uniqueness; never autofill from partial results.
            errors.append(database + ': nie udało się odczytać bazy (dostęp, limit czasu lub schemat)')
        finally:
            if conn is not None:
                conn.close()
    return {'matches':dict(matches),'warnings':warnings,'errors':errors,'scanned':scanned,'visible':len(databases)}


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
