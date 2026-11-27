"""Named data operations formerly embedded in desktop widget constructors."""
from app.remote.boundary import endpoint as office_endpoint
from app.db.company_labels import company_label_sql
from app.db.dao_conn import get_conn


@office_endpoint('app.remote.screen_data.activity_rows')
def activity_rows(firma_id,miesiac):
    with get_conn() as conn:
        return [dict(row) for row in conn.execute('''SELECT c.typ,c.rozpoczecie,c.zakonczenie,c.sekundy,
            p.imie || ' ' || p.nazwisko AS pracownik FROM czasomierze c
            LEFT JOIN pracownicy p ON p.id=c.pracownik_id
            WHERE c.firma_id=%s AND c.miesiac=%s ORDER BY c.rozpoczecie DESC''',(firma_id,miesiac)).fetchall()]


@office_endpoint('app.remote.screen_data.invoice_entities')
def invoice_entities():
    with get_conn() as conn:
        companies = [dict(row) for row in conn.execute(
            f"SELECT f.id,f.nazwa,f.nazwa_skrocona,f.nip,{company_label_sql('f')} AS nazwa_wyswietlana "
            "FROM firmy f ORDER BY nazwa_wyswietlana,f.id").fetchall()]
        contractors = [dict(row) for row in conn.execute('SELECT id,nazwa,nip FROM kontrahenci ORDER BY nazwa').fetchall()]
    return companies,contractors


@office_endpoint('app.remote.screen_data.invoice_filter_entities')
def invoice_filter_entities():
    with get_conn() as conn:
        companies = conn.execute(f"SELECT f.id,{company_label_sql('f')} AS nazwa FROM firmy f ORDER BY nazwa,f.id").fetchall()
        contractors = conn.execute('SELECT id,nazwa FROM kontrahenci ORDER BY LOWER(nazwa),id').fetchall()
    return companies,contractors


@office_endpoint('app.remote.screen_data.reportable_employees')
def reportable_employees(actor_id):
    from app.security.authorization import can,is_administrator
    see_all = is_administrator(actor_id) or can(actor_id,'can_view_finances')
    with get_conn() as conn:
        rows = conn.execute("SELECT id,TRIM(COALESCE(imie,'') || ' ' || COALESCE(nazwisko,'')) AS label FROM pracownicy ORDER BY nazwisko,imie").fetchall()
    return [(row['id'],row['label'] or f"#{row['id']}") for row in rows if see_all or row['id']==actor_id]


@office_endpoint('app.remote.screen_data.search_company_buyers')
def search_company_buyers(term):
    with get_conn() as conn:
        pattern = '%'+str(term)+'%'
        name = company_label_sql('f')
        return conn.execute(f'''SELECT f.id,{name} AS nazwa,f.nip
            FROM firmy f WHERE f.nazwa ILIKE %s OR {name} ILIKE %s OR f.nip ILIKE %s
            ORDER BY lower({name}),f.id LIMIT 30''',(pattern,pattern,pattern)).fetchall()


@office_endpoint('app.remote.screen_data.company_identities')
def company_identities(short_names=False):
    with get_conn() as conn:
        name = company_label_sql('f') if short_names else 'f.nazwa'
        return conn.execute(f'''SELECT f.id,{name} AS nazwa,f.nazwa_skrocona,f.nip,f.forma_dzialalnosci,
            f.forma_opodatkowania,f.status_vat FROM firmy f ORDER BY lower({name}),f.id''').fetchall()


@office_endpoint('app.remote.screen_data.reminder_preview_jobs')
def reminder_preview_jobs(settings,actor_id):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Brak uprawnień do przypomnień.')
    from app.services.email_reminders import _candidates
    from datetime import datetime
    from zoneinfo import ZoneInfo
    with get_conn() as conn:
        now = datetime.now(ZoneInfo('Europe/Warsaw'))
        return {kind:list(_candidates(conn,kind,now,settings[kind])) for kind in ('documents','payments')}


@office_endpoint('app.remote.screen_data.check_ezla_service')
def check_ezla_service(nip,login,password='',zip_password=''):
    from app.integrations.ezla.settings import load_settings,credentials
    from app.integrations.ezla.service import fetch_reports
    from datetime import datetime,timedelta
    from zoneinfo import ZoneInfo
    old = load_settings()
    old_login,old_password,old_zip = credentials(old)
    if not password and login.strip()!=old_login:
        raise ValueError('Dla nowego loginu wpisz hasło usługi.')
    today = datetime.now(ZoneInfo('Europe/Warsaw')).date()
    return len(fetch_reports(nip,login,password or old_password,zip_password or old_zip,today-timedelta(days=30),today=today))


@office_endpoint('app.remote.screen_data.mail_view_snapshot')
def mail_view_snapshot(konto_id,folder,query='',employee=None,completed=False,before=None,existing=(),through=None,ensure_row=None):
    from app.email.email_storage import mail_read_snapshot,get_mail_page,get_folders_for_account,get_assignment_map,get_completed_map
    from app.email.email_helpers import folder_role
    ensure_ids = [ensure_row['id']] if ensure_row and ensure_row.get('konto_id')==konto_id else []
    with mail_read_snapshot() as connection:
        rows,more,cursor = get_mail_page(konto_id,folder,phrase=query,before=before,employee_id=employee,
            completed=completed,include_ids=ensure_ids,connection=connection)
        if existing:
            tail = through or min((row.get('data') or '',row['id']) for row in existing)
            extra_ids = [row['id'] for row in existing if (row.get('data') or '',row['id'])<tail]
            retained,retained_more,retained_cursor = get_mail_page(konto_id,folder,phrase=query,employee_id=employee,
                completed=completed,through=tail,include_ids=extra_ids,connection=connection)
            if retained_cursor is not None and (cursor is None or retained_cursor<cursor):
                more,cursor = retained_more,retained_cursor
            rows = [*rows,*retained]
        rows = sorted({row['id']:row for row in rows}.values(),key=lambda row:(row.get('data') or '',row['id']),reverse=True)
        ids = [row['id'] for row in rows]
        roles = {f['server_name']:folder_role(f['server_name'],f.get('attributes'),f.get('delimiter'))
                 for f in get_folders_for_account(konto_id,connection=connection)}
        return rows,more,cursor,roles,get_assignment_map(ids,connection=connection),get_completed_map(ids,connection=connection)


@office_endpoint('app.remote.screen_data.reminder_history')
def reminder_history():
    with get_conn() as conn:
        return conn.execute('SELECT created_at,kind,recipient,subject,status,error FROM email_reminder_log ORDER BY id DESC LIMIT 100').fetchall()


@office_endpoint('app.remote.screen_data.catalogue_count')
def catalogue_count():
    with get_conn() as conn:
        return conn.execute('SELECT COUNT(*) FROM cenniki').fetchone()[0]


@office_endpoint('app.remote.screen_data.release_preparation')
def release_preparation(reference):
    # The service resolves and closes the opaque reference before invocation.
    return None


@office_endpoint('app.remote.screen_data.google_client_metadata')
def google_client_metadata():
    from app.kalendarz.google_sync import get_client_credentials
    client_id, secret = get_client_credentials()
    return client_id, bool(secret)


@office_endpoint('app.remote.screen_data.mail_sidebar_snapshot')
def mail_sidebar_snapshot(actor_id):
    from app.email.email_storage import get_email_accounts_for_employee
    from app.db.dao_employees import list_employees
    from app.email.email_labels import resolve_label_numbers
    accounts = get_email_accounts_for_employee(actor_id)
    ids = [account['id'] for account in accounts]
    with get_conn() as conn:
        folders = [dict(row) for row in conn.execute(
            'SELECT * FROM foldery_email WHERE konto_id=ANY(%s) ORDER BY display_name,server_name',(ids,)).fetchall()]
        counts = conn.execute('SELECT konto_id,folder_id,COUNT(*) AS n FROM wiadomosci_email '
            'WHERE konto_id=ANY(%s) AND COALESCE(usunieta,0)=0 AND COALESCE(przeczytana,0)=0 '
            'GROUP BY konto_id,folder_id',(ids,)).fetchall()
    grouped,unread = {},{}
    for folder in folders:
        grouped.setdefault(folder['konto_id'],[]).append(folder)
    for row in counts:
        unread.setdefault(row['konto_id'],{})[row['folder_id']] = row['n']
    employees = [row for row in list_employees() if row.get('aktywny',1)]
    labels = resolve_label_numbers(employees)
    from app.db.dao_app_settings import get_setting
    return {'accounts':accounts,'folders':grouped,'unread':unread,'labels':labels,
            'folder_prefix':str(get_setting('email_folder_prefix') or ''),
            'employees':[{key:row.get(key) for key in ('id','email','imie','nazwisko','aktywny')} for row in employees]}


@office_endpoint('app.remote.screen_data.calendar_view_snapshot')
def calendar_view_snapshot(actor_id,start,end):
    from datetime import date
    from app.db.dao_calendar import list_events
    from app.kalendarz import google_sync,caldav_sync
    from urllib.parse import urlparse
    first,last = date.fromisoformat(start),date.fromisoformat(end)
    if not 0<(last-first).days<=400:
        raise ValueError('Nieprawidłowy zakres kalendarza.')
    sources = [('Lokalne (import ICS)','lokalne')]
    if google_sync.is_connected(actor_id):
        sources.append(('Google Calendar','google'))
    if caldav_sync.is_configured():
        sources.append(('CalDAV: '+(urlparse(caldav_sync.get_config()['url']).hostname or 'CalDAV'),'caldav'))
    return {'events':list_events(actor_id,start,end),'sources':sources}
