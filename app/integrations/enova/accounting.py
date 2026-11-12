"""Accounting overview with imported ENOVA JPK status."""
from app.db.dao_conn import get_conn
from app.security.authorization import allowed_tabs, assigned_company_ids


def require_access(actor_id):
    if 'Księgowość' not in allowed_tabs(actor_id):
        raise PermissionError('Brak dostępu do księgowości.')


def list_accounting(actor_id, month):
    require_access(actor_id)
    ids = list(assigned_company_ids(actor_id))
    with get_conn() as conn:
        rows = conn.execute('''SELECT f.id,f.nazwa,f.nip,f.forma_opodatkowania,s.ilosc_dok,
            a.wartosc AS snapshot,j.wartosc AS jpk
            FROM firmy f LEFT JOIN statusy_miesieczne s ON s.firma_id=f.id AND s.miesiac=%s
            LEFT JOIN app_settings a ON a.klucz='enova_snapshot_' || f.id::text || '_' || %s
            LEFT JOIN app_settings j ON j.klucz='enova_jpk_' || f.id::text || '_' || %s
            WHERE f.id=ANY(%s) ORDER BY lower(f.nazwa)''', (month, month, month, ids)).fetchall()
    return [dict(row) for row in rows]
