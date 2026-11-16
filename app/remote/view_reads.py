"""Keep async desktop reads usable while office server and client are upgraded."""
import logging
from app.remote.state import client

logger = logging.getLogger(__name__)


def _batched(feature):
    connection = client()
    if connection is None:
        return True
    supported = feature in connection.server_features
    if not supported:
        warned = getattr(connection,'_legacy_view_warnings',None)
        if warned is None:
            warned = connection._legacy_view_warnings = set()
        if feature not in warned:
            warned.add(feature)
            logger.warning('office_legacy_view_transport feature=%s; update worker/API for batched reads',feature)
    return supported


def mail_sidebar_snapshot(actor_id):
    if _batched('mail_sidebar_snapshot'):
        from app.remote.screen_data import mail_sidebar_snapshot as read
        return read(actor_id)
    # Existing, authorized RPCs only. This function runs in the view's background job.
    from app.email.email_storage import get_email_accounts_for_employee,get_folders_for_account,get_unread_counts
    from app.db.dao_employees import list_employees
    from app.email.email_labels import resolve_label_numbers
    from app.db.dao_app_settings import get_setting
    accounts = get_email_accounts_for_employee(actor_id)
    employees = [row for row in list_employees() if row.get('aktywny',1)]
    return {'accounts':accounts,
            'folders':{row['id']:get_folders_for_account(row['id']) for row in accounts},
            'unread':{row['id']:get_unread_counts(row['id']) for row in accounts},
            'employees':employees,'labels':resolve_label_numbers(employees),
            'folder_prefix':str(get_setting('email_folder_prefix') or '')}


def calendar_view_snapshot(actor_id,start,end):
    if _batched('calendar_view_snapshot'):
        from app.remote.screen_data import calendar_view_snapshot as read
        return read(actor_id,start,end)
    from app.db.dao_calendar import list_events
    from app.kalendarz import google_sync,caldav_sync
    from urllib.parse import urlparse
    sources = [('Lokalne (import ICS)','lokalne')]
    if google_sync.is_connected(actor_id):
        sources.append(('Google Calendar','google'))
    if caldav_sync.is_configured():
        host = urlparse(caldav_sync.get_config()['url']).hostname or 'CalDAV'
        sources.append(('CalDAV: '+host,'caldav'))
    return {'events':list_events(actor_id,start,end),'sources':sources}
