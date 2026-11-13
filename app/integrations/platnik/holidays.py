import json
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo
from app.db.dao_app_settings import get_setting, set_setting
from app.utils.crypto import encrypt_password, decrypt_password
from app.integrations.enova.document_counts import normalize_nip, sql_connection_string
from app.integrations.enova.odbc_support import load_odbc, resolve_driver, validate_server, describe_sql_error
from app.integrations.enova.odbc_support import SqlOperationError as PlatnikSqlError

KEY = 'platnik_connection'
FIELDS = ('imiona_nazwiska','kod_ubezpieczenia','wyslane_powiadomienie','skladki_oplacane',
          'miesiac','oswiadczenie','platnosc','kto_wyslal','decyzja')
LABELS = ('Imiona i nazwiska','Kod ubezpieczenia','Wysłane powiadomienie','Składki opłacane',
          'Miesiąc','Oświadczenie','Płatność','Kto wysłał','Decyzja')


@contextmanager
def sql_operation(config,operation):
    try:
        yield
    except (PlatnikSqlError,ValueError,PermissionError):
        raise
    except Exception as error:
        # A native driver error may echo a connection string. Redact both the
        # decrypted password and its ODBC-escaped representation before delivery.
        encrypted = config.get('password_enc','')
        try:
            password = decrypt_password(encrypted)
        except Exception:
            raise ValueError('Nie można odczytać wspólnego klucza Płatnika. Sprawdź klucz workera.') from None
        diagnostic = describe_sql_error(error,secrets=(password,encrypted))
        raise PlatnikSqlError(diagnostic,config,operation) from None


def load():
    return {'enabled':False,'server':'','database':'platnik','username':'','password_enc':'',
            'driver':'auto','trust_server_certificate':False,
            **json.loads(get_setting(KEY) or '{}')}


def save(value, actor_id, password=''):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Tylko administrator konfiguruje Płatnika.')
    current = load()
    value = {key:value.get(key,current[key]) for key in ('enabled','server','database','username','password_enc','driver','trust_server_certificate')}
    from app.integrations.enova.odbc_support import SUPPORTED_DRIVERS
    if value['driver'] not in ('auto',*SUPPORTED_DRIVERS):
        raise ValueError('Nieprawidłowy sterownik SQL Server.')
    validate_server(value['server'])
    if not value['database'].strip() or not value['username'].strip():
        raise ValueError('Uzupełnij bazę i użytkownika SQL Płatnika.')
    value['password_enc'] = encrypt_password(password) if password else load()['password_enc']
    if not value['password_enc']:
        raise ValueError('Uzupełnij hasło SQL Płatnika.')
    if type(value['enabled']) is not bool or type(value['trust_server_certificate']) is not bool:
        raise ValueError('Nieprawidłowe opcje Płatnika.')
    set_setting(KEY,json.dumps(value,ensure_ascii=False))


def connect(config):
    validate_server(config.get('server'))
    if not str(config.get('database') or '').strip() or not str(config.get('username') or '').strip():
        raise ValueError('Uzupełnij bazę i użytkownika SQL Płatnika.')
    if not config.get('password_enc'):
        raise ValueError('Uzupełnij hasło SQL Płatnika. Puste pole korzysta wyłącznie z wcześniej zapisanego hasła.')
    odbc = load_odbc()
    config = {**config,'driver':resolve_driver(config,odbc)}
    password = decrypt_password(config['password_enc'])
    with sql_operation(config,'Połączenie z SQL Płatnika'):
        connection = odbc.connect(sql_connection_string(config,config['database'],config['username'],password),
                                  timeout=15,attrs_before={101:1})
        try:
            connection.timeout = 30
        except Exception:
            connection.close()
            raise
    return connection


def check_connection(config):
    connection = connect(config)
    with sql_operation(config,'Sprawdzenie bazy Płatnika'):
        try:
            row = connection.cursor().execute('SELECT DB_NAME()').fetchone()
            if not row or str(row[0]).casefold()!=config['database'].casefold():
                raise ValueError('Połączenie wskazuje inną bazę niż wybrana baza Płatnika.')
        finally:
            connection.close()


def normalize(rows, year):
    result = []
    for row in rows:
        if str(row.get('rok')) != str(year):
            raise ValueError('Zapytanie Płatnika zwróciło inny rok. Poprzednie dane pozostawiono.')
        issues = []
        try:
            nip = normalize_nip(row.get('nip'))
        except ValueError:
            nip = ''
            issues.append('Brak jednego poprawnego NIP płatnika — wymaga przypisania')
        if not str(row.get('imiona_nazwiska') or '').strip():
            issues.append('Brak jednoznacznych danych osoby w kartotece Płatnika')
        record = {'nip':nip,'rok':year,'issues':issues}
        for key in ('source_id','source_payer_id','source_person_id','status_zrodlowy','bazowy_kod_ubezpieczenia',
                    'skladka_em','skladka_ren','skladka_wyp','skladka_chor','skladka_fpfs'):
            record[key] = str(row[key]) if row.get(key) is not None else None
        if row.get('month_year') is not None and str(row['month_year'])!=str(year):
            issues.append('Miesiąc źródłowy nie odpowiada rokowi wpisu RWS')
        for key in FIELDS:
            value = row.get(key)
            if value is not None:
                value = str(value)
                if len(value)>2000:
                    raise ValueError('Pole raportu Płatnika przekracza limit.')
            record[key] = value
        status = (record['status_zrodlowy'] or '').strip()
        record['status_zrodlowy'] = status
        record['opis_statusu'] = {'A':'Aktualne zwolnienie (A)','N':'Nieaktualny wpis (N)'}.get(status,'Nieznany status: '+(status or 'brak'))
        # The holiday insurance code is not the ordinary/base insurance code.
        record['kod_ulgi'] = record['kod_ubezpieczenia']
        record['kod_ubezpieczenia'] = record['bazowy_kod_ubezpieczenia']
        record['decyzja'] = None
        if status not in ('A','N'):
            issues.append('Nieznany status zwolnienia Płatnika')
        month = record['miesiac']
        if month is not None and month.strip() and (not month.isdigit() or not 1<=int(month)<=12):
            raise ValueError('Miesiąc ulgi musi być numerem od 1 do 12 albo NULL.')
        result.append(record)
    return result


def _import_year(year, actor_id=None):
    if type(year) is not int or not 2024<=year<=2100:
        raise ValueError('Nieprawidłowy rok zestawienia.')
    if actor_id is not None:
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Tylko administrator uruchamia odczyt Płatnika.')
    config = load()
    if not config['enabled']:
        return {'mode':'disabled'}
    from .holiday_query import QUERY
    query = QUERY
    connection = connect(config)
    with sql_operation(config,f'Odczyt wakacji składkowych ({year})'):
        try:
            cursor = connection.cursor()
            cursor.execute(query,year)
            columns = [column[0].lower() for column in cursor.description or []]
            if not {'source_id','nip','rok','imiona_nazwiska'}.issubset(columns) or len(columns)!=len(set(columns)):
                raise ValueError('Adapter wymaga unikalnych kolumn nip, rok, imiona_nazwiska.')
            values = cursor.fetchmany(10001)
            if len(values)>10000:
                raise ValueError('Raport przekracza 10000 pozycji. Poprzednie dane pozostawiono.')
            rows = normalize([dict(zip(columns,row)) for row in values],year)
        finally:
            connection.close()
    snapshot = {'imported_at':datetime.now(ZoneInfo('Europe/Warsaw')).isoformat(),
                'source':'Płatnik SQL — RWS_ZWOLNIENIE','rows_enc':encrypt_password(json.dumps(rows,ensure_ascii=False))}
    # Atomic replacement only after the entire source result was read and validated.
    set_setting(f'platnik_holidays_{year}',json.dumps(snapshot))
    return {'year':year,'rows':len(rows)}


def import_year(year, actor_id=None):
    from app.db.dao_conn import get_coordination_conn
    from app.security.authorization import is_administrator
    if actor_id is not None and not is_administrator(actor_id):
        raise PermissionError('Tylko administrator uruchamia odczyt Płatnika.')
    if type(year) is not int or not 2024<=year<=2100:
        raise ValueError('Nieprawidłowy rok zestawienia.')
    with get_coordination_conn() as lock:
        if not lock.execute('SELECT pg_try_advisory_xact_lock(748215,%s)',(year,)).fetchone()[0]:
            return {'mode':'busy','year':year}
        try:
            result = _import_year(year,actor_id)
        except Exception:
            set_setting(f'platnik_holidays_status_{year}',json.dumps({'state':'failed',
                        'checked_at':datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}))
            raise
        if result.get('mode')!='disabled':
            set_setting(f'platnik_holidays_status_{year}',json.dumps({'state':'ok',
                        'checked_at':datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}))
        return result


def read_year(actor_id,year):
    from app.integrations.enova.accounting import require_access
    from app.security.authorization import assigned_company_ids,is_administrator
    from app.db.dao_conn import get_conn
    if type(year) is not int or not 2024<=year<=2100:
        raise ValueError('Nieprawidłowy rok zestawienia.')
    require_access(actor_id)
    value = json.loads(get_setting(f'platnik_holidays_{int(year)}') or '{}')
    ids = list(assigned_company_ids(actor_id))
    with get_conn() as conn:
        companies = conn.execute('SELECT id,nazwa,nip FROM firmy WHERE id=ANY(%s)',(ids,)).fetchall()
    mapping = {}
    for row in companies:
        try:
            mapping.setdefault(normalize_nip(row['nip']),[]).append(dict(row))
        except ValueError:
            pass
    rows = json.loads(decrypt_password(value['rows_enc'])) if value.get('rows_enc') else []
    visible = []
    for row in rows:
        matches = mapping.get(row['nip'],[])
        if len(matches)==1:
            visible.append({**row,'firma':matches[0]['nazwa'],'firma_id':matches[0]['id']})
        elif is_administrator(actor_id):
            visible.append({**row,'firma':'Do przypisania: '+(row['nip'] or ('Płatnik #'+str(row.get('source_payer_id') or '—')))})
    return {'rows':visible,'companies':[dict(row) for row in companies],
            'imported_at':value.get('imported_at'),'configured':bool(load()['enabled']),
            'status':json.loads(get_setting(f'platnik_holidays_status_{int(year)}') or '{}')}


def worker_job():
    year = datetime.now(ZoneInfo('Europe/Warsaw')).year
    results = []
    for period in (year-1,year):
        if period<2024:
            continue
        try:
            results.append(import_year(period))
        except Exception as error:
            results.append({'year':period,'failed':True,
                            'error':str(error) if isinstance(error,PlatnikSqlError) else 'Odczyt nie powiódł się'})
    if any(value.get('failed') for value in results):
        raise RuntimeError('Odczyt wakacji składkowych nieukończony. '+
                           '; '.join(f"{value['year']}: {value['error']}" for value in results if value.get('failed')))
    return {'results':results}
