"""Read-only checking data with a rolling per-company document history."""
import json
from app.db.dao_conn import get_conn
from app.security.authorization import allowed_tabs, assigned_company_ids
from app.integrations.enova.checking import history_months


def require_access(actor_id):
    if 'Sprawdzanie' not in allowed_tabs(actor_id):
        raise PermissionError('Brak dostępu do sprawdzania.')


def decoded(value):
    return json.loads(value) if isinstance(value, str) and value else value or {}


def list_accounting(actor_id, month):
    require_access(actor_id)
    prior = history_months(month)
    ids = list(assigned_company_ids(actor_id))
    with get_conn() as conn:
        rows = conn.execute('''SELECT f.id,f.nazwa,f.nip,f.forma_opodatkowania,s.ilosc_dok,
            a.wartosc AS snapshot,j.wartosc AS jpk,c.wartosc AS checking
            FROM firmy f LEFT JOIN statusy_miesieczne s ON s.firma_id=f.id AND s.miesiac=%s
            LEFT JOIN app_settings a ON a.klucz='enova_snapshot_' || f.id::text || '_' || %s
            LEFT JOIN app_settings j ON j.klucz='enova_jpk_' || f.id::text || '_' || %s
            LEFT JOIN app_settings c ON c.klucz='enova_checking_' || f.id::text || '_' || %s
            WHERE f.id=ANY(%s) ORDER BY lower(f.nazwa)''', (month, month, month, month, ids)).fetchall()
        history = conn.execute('''SELECT f.id,a.klucz,a.wartosc FROM firmy f JOIN app_settings a
            ON a.klucz='enova_checking_' || f.id::text || '_' || right(a.klucz,7)
            WHERE f.id=ANY(%s) AND right(a.klucz,7)=ANY(%s)''', (ids, prior)).fetchall()
    by_company = {}
    for record in history:
        by_company.setdefault(record['id'], []).append(decoded(record['wartosc']))
    result = []
    for record in rows:
        row = dict(record)
        row['history'] = by_company.get(row['id'], [])
        result.append(row)
    return result


def documents(row, department):
    report = decoded(row.get('checking'))
    if report:
        return report.get('documents', {}).get(department)
    # Legacy totals can be shown, but missing types must not be invented as zero.
    if department == 'accounting':
        return decoded(row.get('snapshot')).get('by_type')
    return None


def active_types(row, department):
    return {kind for report in row.get('history', [])
            for kind, count in (report.get('documents', {}).get(department) or {}).items() if count > 0}


def declarations(row, department):
    report = decoded(row.get('checking'))
    result = {entry['form']: entry for entry in report.get('declarations') or []
              if entry['department'] == department}
    if department == 'accounting' and 'JPK_VAT' not in result:
        legacy = decoded(row.get('jpk'))
        if legacy:
            result['JPK_VAT'] = {**legacy, 'sent_at': None, 'upo_at': None}
    return result
