"""Authorization belongs to the service, independently of desktop visibility."""
import inspect
from dataclasses import is_dataclass, replace


def check(capability,actor_id):
    from app.security.authorization import allowed_tabs,can,is_administrator
    if not actor_id or not allowed_tabs(actor_id):
        raise PermissionError('Konto nie jest aktywne.')
    if is_administrator(actor_id) or capability=='authenticated':
        return
    if capability.startswith('permission:') and can(actor_id,capability.partition(':')[2]):
        return
    if capability.startswith('tab:') and set(capability.partition(':')[2].split('|')) & set(allowed_tabs(actor_id)):
        return
    raise PermissionError('Brak uprawnień do tej operacji.')


def bind(function,values,spec,actor_id):
    signature = inspect.signature(function)
    arguments = dict(values)
    if any(arguments.get(key) is not None for key in ('conn','connection','cur','cursor','client','calendar')):
        raise PermissionError('Połączenia z bazą pozostają na serwerze.')
    # Even an authenticated desktop cannot select the acting identity.
    for name in spec.get('actors',[]):
        arguments[name] = actor_id
    for name in spec.get('self_only',[]):
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            arguments[name] = actor_id
    for name,value in list(arguments.items()):
        if is_dataclass(value) and hasattr(value,'actor_id'):
            arguments[name] = replace(value,actor_id=actor_id)
    if spec['module']=='app.email.email_sync' and 'accounts' in arguments:
        from app.email.email_storage import get_account_info,get_email_accounts_for_employee
        allowed = {row['id'] for row in get_email_accounts_for_employee(actor_id)}
        from app.security.authorization import is_administrator
        ids = [item['id'] for item in arguments['accounts']]
        if not is_administrator(actor_id) and not set(ids)<=allowed:
            raise PermissionError('Brak dostępu do konta pocztowego.')
        arguments['accounts'] = [get_account_info(key) for key in ids]
    bound = signature.bind(**arguments)
    bound.apply_defaults()
    return bound


def resource_access(operation,values,actor_id):
    from app.security.authorization import is_administrator
    if values.get('from_remote'):
        raise PermissionError('Tę operację wykonuje synchronizacja serwera.')
    # Remote calendar mutations must refer to a local event or a queued deletion.
    if operation.startswith('app.kalendarz.') and operation.endswith(('.push_event','.delete_remote','.mark_event_pending')):
        from app.db.dao_conn import get_conn
        with get_conn() as conn:
            event = values.get('event')
            if event is not None:
                saved = conn.execute('SELECT * FROM wydarzenia WHERE id=%s AND pracownik_id=%s',
                                     (event.get('id'),actor_id)).fetchone()
                if not saved:
                    raise PermissionError('Brak dostępu do wydarzenia.')
                values['event'] = dict(saved)
            else:
                provider = 'google' if '.google_sync.' in operation else 'caldav'
                remote_id = values.get('google_event_id',values.get('uid'))
                queued = conn.execute("SELECT 1 FROM calendar_outbox WHERE pracownik_id=%s AND provider=%s AND remote_id=%s AND action='delete'",
                                      (actor_id,provider,remote_id)).fetchone()
                if not queued:
                    raise PermissionError('Brak zatwierdzonego usunięcia wydarzenia.')
    if operation=='app.db.dao_calendar.save_event':
        from app.db.dao_calendar import get_event
        from app.kalendarz.google_sync import has_calendar_access
        data = dict(values.get('data') or {})
        existing = get_event(data['id']) if data.get('id') else None
        if data.get('id') and (not existing or existing['pracownik_id']!=actor_id):
            raise PermissionError('Brak dostępu do wydarzenia.')
        if data.get('google_calendar_id') and not has_calendar_access(actor_id,data['google_calendar_id']):
            raise PermissionError('Brak dostępu do kalendarza Google.')
        # Remote identities belong to server synchronization, never to caller-supplied data.
        for key in ('google_event_id','caldav_uid','caldav_calendar_id'):
            data[key] = existing.get(key) if existing else None
        data['zrodlo'] = existing.get('zrodlo') if existing else 'lokalne'
        values['data'] = data
    if is_administrator(actor_id):
        return
    if operation.startswith(('app.email.','app.db.dao_email')) or operation=='app.remote.screen_data.mail_view_snapshot':
        from app.email.email_storage import get_email_accounts_for_employee
        from app.db.dao_conn import get_conn
        allowed = {row['id'] for row in get_email_accounts_for_employee(actor_id)}
        if any(name in values and values[name] is None for name in ('konto_id','account_id','acc_id')):
            raise PermissionError('Wybierz dostępne konto pocztowe.')
        ids = {values[name] for name in ('konto_id','account_id','acc_id') if values.get(name) is not None}
        plan = values.get('plan')
        if plan is not None and hasattr(plan,'account_id'):
            ids.add(plan.account_id)
        account_values = values.get('accounts') or []
        ids.update(item['id'] if isinstance(item,dict) else item for item in account_values)
        if isinstance(values.get('target'),dict) and values['target'].get('konto_id') is not None:
            ids.add(values['target']['konto_id'])
        with get_conn() as conn:
            row_ids = values.get('row_ids') or []
            if row_ids:
                owned = conn.execute('SELECT id,konto_id FROM wiadomosci_email WHERE id=ANY(%s)',(list(row_ids),)).fetchall()
                if {row[0] for row in owned}!=set(row_ids):
                    raise PermissionError('Wiadomość jest niedostępna.')
                ids.update(row[1] for row in owned)
            if values.get('filter_id') is not None:
                rule = conn.execute('SELECT konto_id FROM reguly_filtrow WHERE id=%s',(values['filter_id'],)).fetchone()
                if not rule:
                    raise PermissionError('Reguła poczty jest niedostępna.')
                ids.add(rule[0])
            for key in ('folder_id','src_folder_id','dest_folder_id','source_folder_id','target_folder_id'):
                if values.get(key) is not None:
                    row = conn.execute('SELECT konto_id FROM foldery_email WHERE id=%s',(values[key],)).fetchone()
                    if not row:
                        raise PermissionError('Folder jest niedostępny.')
                    ids.add(row[0])
            for key in ('wiadomosc_id','message_id','message_row_id'):
                if values.get(key) is not None:
                    row = conn.execute('SELECT konto_id FROM wiadomosci_email WHERE id=%s',(values[key],)).fetchone()
                    if not row:
                        raise PermissionError('Wiadomość jest niedostępna.')
                    ids.add(row[0])
        if not ids <= allowed:
            raise PermissionError('Brak dostępu do konta pocztowego.')
    if operation.startswith('app.chat.chat_model.'):
        from app.chat.chat_model import is_participant
        conversation = values.get('conversation_id',values.get('konwersacja_id'))
        if operation.endswith('.edit_message'):
            from app.db.dao_conn import get_conn
            with get_conn() as conn:
                message = conn.execute('SELECT pracownik_id,konwersacja_id FROM wiadomosci_chat WHERE id=%s',(values.get('message_id'),)).fetchone()
            if not message or message[0]!=actor_id:
                raise PermissionError('Możesz edytować tylko własne wiadomości.')
            conversation = message[1]
        if conversation is not None and not is_participant(conversation,actor_id):
            raise PermissionError('Brak dostępu do rozmowy.')
    if operation=='app.db.dao_active_timers.checkpoint':
        from app.db.dao_conn import get_conn
        with get_conn() as conn:
            for session_id,owner,elapsed in values.get('snapshots') or []:
                row = conn.execute('SELECT 1 FROM active_timer_sessions WHERE session_id=%s AND owner_token=%s AND pracownik_id=%s',
                                   (session_id,owner,actor_id)).fetchone()
                if not row or not isinstance(elapsed,(int,float)) or elapsed<0 or elapsed>999*3600:
                    raise PermissionError('Stoper nie należy do zalogowanego pracownika.')
    if operation.startswith('app.db.dao_calendar.'):
        if values.get('from_remote'):
            raise PermissionError('Tę operację wykonuje synchronizacja serwera.')
        event_id = values.get('event_id') or (values.get('data') or {}).get('id')
        if event_id:
            from app.db.dao_calendar import get_event_access,get_event
            event = get_event(event_id)
            read = operation.rsplit('.',1)[1].startswith(('get_','list_'))
            if not event or (event['pracownik_id']!=actor_id and (not read or actor_id not in get_event_access(event_id))):
                raise PermissionError('Brak dostępu do wydarzenia.')


def public_result(operation,result,actor_id):
    from app.remote.secrets import public
    if operation.endswith('.get_all_email_accounts'):
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            from app.email.email_storage import get_email_accounts_for_employee
            result = get_email_accounts_for_employee(actor_id)
    if operation.endswith('.get_email_filters_for_company'):
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            from app.email.email_storage import get_email_accounts_for_employee
            allowed = {row['id'] for row in get_email_accounts_for_employee(actor_id)}
            result = [row for row in result if row.get('konto_id') in allowed]
    return public(result)
