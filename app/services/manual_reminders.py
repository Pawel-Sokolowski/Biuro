"""Reviewed, one-off reminders from monthly/invoice views; no SMTP retries."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
import calendar

from app.db.dao_conn import get_conn
from app.security.authorization import allowed_tabs, assigned_company_ids, can_manage_invoices
from app.email.email_storage import get_email_accounts_for_employee
from app.invoices.invoice_delivery import parse_recipients
from .email_reminders import FIELDS, load_settings, _candidates
from .reminder_recipients import select_recipient


def prepare(kind, target_id, actor_id, month=None):
    if type(target_id) is not int or target_id<=0:
        raise ValueError('Wybierz firmę albo fakturę.')
    settings = load_settings()
    now = datetime.now(ZoneInfo('Europe/Warsaw'))
    with get_conn() as conn:
        if kind=='documents':
            if 'Widok miesięczny' not in allowed_tabs(actor_id) or target_id not in assigned_company_ids(actor_id):
                raise PermissionError('Brak dostępu do widoku miesięcznego firmy.')
            try:
                period = date.fromisoformat(str(month)+'-01')
            except ValueError:
                raise ValueError('Wybierz konkretny miesiąc.') from None
            if period.strftime('%Y-%m')!=month:
                raise ValueError('Wybierz konkretny miesiąc.')
            company = conn.execute('''SELECT f.nazwa,f.adres_email,s.dokumenty_dostarczone,
                ARRAY(SELECT c.email FROM company_contacts c WHERE c.firma_id=f.id ORDER BY c.id) AS contact_emails
                FROM firmy f LEFT JOIN statusy_miesieczne s ON s.firma_id=f.id AND s.miesiac=%s
                WHERE f.id=%s''', (month,target_id)).fetchone()
            if not company:
                raise ValueError('Firma nie istnieje.')
            if company['dokumenty_dostarczone']:
                raise ValueError('Komplet dokumentów za wybrany miesiąc jest już oznaczony jako dostarczony.')
            first = date(period.year+int(period.month==12),period.month%12+1,1)
            due = first.replace(day=min(settings[kind]['day'],calendar.monthrange(first.year,first.month)[1]))
            candidate = {'target':f'{target_id}:{month}','email':company['adres_email'],'due':due,
                         'firma':company['nazwa'],'miesiac':month,'numer':'','pozostalo':''}
            candidate.update(select_recipient(company_email=company['adres_email'],contact_emails=company['contact_emails']))
        elif kind=='payments':
            if not can_manage_invoices(actor_id):
                raise PermissionError('Brak dostępu do faktur.')
            candidate = next(_candidates(conn,kind,now,settings[kind],target_id),None)
            if not candidate:
                raise ValueError('Wybierz wystawioną, nieanulowaną fakturę lub fakturę zaliczkową z kwotą do zapłaty i terminem płatności.')
            from app.db.dao_invoices import can_access_invoice
            invoice = conn.execute('SELECT firma_id FROM faktury WHERE id=%s',(target_id,)).fetchone()
            if not invoice or not can_access_invoice(actor_id,invoice['firma_id']):
                raise PermissionError('Brak dostępu do faktury.')
        else:
            raise ValueError('Nieprawidłowy rodzaj przypomnienia.')
    try:
        recipients = parse_recipients(candidate['email']) if candidate['email'] else []
    except ValueError:
        raise ValueError(f"Firma {candidate['firma']}: nieprawidłowy adres odbiorcy przypomnienia. Popraw e-mail kontaktowy lub adres do faktur w danych firmy.") from None
    template = next(t for t in settings['templates'] if t['name']==settings[kind]['template'])
    values = {key:str(candidate.get(key,'')) for key in FIELDS}
    values['termin'] = candidate['due'].isoformat()
    return {'kind':kind,'target_id':target_id,'target':candidate['target'],'month':month,
            'recipients':recipients,'recipient_choices':candidate.get('recipient_choices',[]),'context':values,
            'subject':template['subject'].format(**values),'body':template['body'].format(**values)}


def send(plan, actor_id, message):
    # Eligibility, amount, contact details and sender permissions are checked again
    # after the user reviews the message. Never retry an ambiguous SMTP attempt.
    fresh = prepare(plan['kind'],plan['target_id'],actor_id,plan['month'])
    if (fresh['recipients']!=plan['recipients'] or fresh['context']!=plan['context']
            or fresh.get('recipient_choices',[])!=plan.get('recipient_choices',[])):
        raise ValueError('Dane firmy lub faktury zmieniły się. Otwórz ponownie przypomnienie.')
    accounts = get_email_accounts_for_employee(actor_id)
    if message.get('account_id') not in {account['id'] for account in accounts}:
        raise PermissionError('Brak dostępu do wybranego konta pocztowego.')
    recipients = parse_recipients(','.join(message.get('to') or []))
    for field in ('cc','bcc'):
        if message.get(field):
            parse_recipients(','.join(message[field]))
    subject,body = message.get('subject',''),message.get('body','')
    if not subject.strip() or '\n' in subject or '\r' in subject or not body.strip():
        raise ValueError('Uzupełnij temat i treść wiadomości.')
    today = datetime.now(ZoneInfo('Europe/Warsaw')).date()
    with get_conn() as conn:
        lock_day(conn,plan['kind'],plan['target'],today)
        automatic = conn.execute('''SELECT 1 FROM email_reminder_log
            WHERE kind=%s AND target=%s AND (created_at AT TIME ZONE 'Europe/Warsaw')::date=%s''',
            (plan['kind'],plan['target'],today)).fetchone()
        if automatic:
            raise ValueError('Dziś podjęto już automatyczną wysyłkę tego przypomnienia. Sprawdź folder Wysłane.')
        # Committed before SMTP, shared across PCs, including uncertain failures.
        claim = conn.execute('''INSERT INTO email_reminder_log
            (kind,target,scheduled_date,recipient,subject,body) VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id''',
            ('manual_'+plan['kind'],plan['target'],today,', '.join(recipients),subject,body)).fetchone()
        if not claim:
            raise ValueError('Dziś podjęto już ręczną wysyłkę tego przypomnienia. Sprawdź folder Wysłane przed kolejną próbą.')
        conn.execute('''INSERT INTO audit_log(tabela,rekord_id,akcja,uzytkownik_id,szczegoly)
            VALUES ('email_reminder_log',%s,'manual_send',%s,'{}')''',(claim[0],actor_id))
    from app.email.email_actions import send_email_smtp
    try:
        ok,info = send_email_smtp(message['account_id'],recipients,subject,body,
                                 attachments=message.get('attachments') or None,
                                 cc=message.get('cc') or None,bcc=message.get('bcc') or None,
                                 html_body=message.get('html') or None,save_copy=True)
    except Exception:
        ok,info = False,'Nie potwierdzono wyniku wysyłki. Sprawdź folder Wysłane przed ponowieniem.'
    with get_conn() as conn:
        conn.execute('''UPDATE email_reminder_log SET status=%s,error=%s,
            sent_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s''',
            ('sent' if ok else 'failed',None if ok else info,ok,claim[0]))
    return {'ok':ok,'info':info}


def sent_manually_today(conn, kind, target, today):
    return conn.execute('''SELECT 1 FROM email_reminder_log
        WHERE kind=%s AND target=%s AND scheduled_date=%s''',
        ('manual_'+kind,target,today)).fetchone() is not None


def lock_day(conn,kind,target,today):
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                 (f'biuro:reminder:{kind}:{target}:{today}',))
