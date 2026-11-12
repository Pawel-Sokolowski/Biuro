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
            a.wartosc AS snapshot,j.wartosc AS jpk,c.wartosc AS checking,h.wartosc AS hr,
            trim(COALESCE(pk.imie,'') || ' ' || COALESCE(pk.nazwisko,'')) AS ksiegowa,
            trim(COALESCE(ph.imie,'') || ' ' || COALESCE(ph.nazwisko,'')) AS kadrowa
            FROM firmy f LEFT JOIN pracownicy pk ON pk.id=f.ksiegowa_id
            LEFT JOIN pracownicy ph ON ph.id=f.kadrowa_id
            LEFT JOIN statusy_miesieczne s ON s.firma_id=f.id AND s.miesiac=%s
            LEFT JOIN app_settings a ON a.klucz='enova_snapshot_' || f.id::text || '_' || %s
            LEFT JOIN app_settings j ON j.klucz='enova_jpk_' || f.id::text || '_' || %s
            LEFT JOIN app_settings c ON c.klucz='enova_checking_' || f.id::text || '_' || %s
            LEFT JOIN app_settings h ON h.klucz='enova_hr_' || f.id::text
            WHERE f.id=ANY(%s) ORDER BY lower(f.nazwa)''', (month, month, month, month, ids)).fetchall()
        history = conn.execute('''SELECT f.id,a.klucz,a.wartosc FROM firmy f JOIN app_settings a
            ON a.klucz='enova_checking_' || f.id::text || '_' || right(a.klucz,7)
            WHERE f.id=ANY(%s) AND right(a.klucz,7)=ANY(%s)''', (ids, prior)).fetchall()
    by_company = {}
    for record in history:
        by_company.setdefault(record['id'], []).append({**decoded(record['wartosc']), 'month': record['klucz'][-7:]})
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


def declaration_entries(row, department):
    report = decoded(row.get('checking'))
    result = [entry for entry in report.get('declarations') or [] if entry['department'] == department]
    if department == 'accounting' and not any(entry['form'] == 'JPK_VAT' for entry in result):
        legacy = decoded(row.get('jpk'))
        if legacy:
            result.append({**legacy, 'form': 'JPK_VAT', 'employee_id': '', 'employee_name': '',
                           'source_system': 'enova', 'sent_at': None, 'upo_at': None})
    return result


def declarations(row, department):
    groups = {}
    for entry in declaration_entries(row, department):
        groups.setdefault(entry['form'], []).append(entry)
    result = {}
    for form, entries in groups.items():
        if len(entries) == 1:
            result[form] = entries[0]
        else:
            accepted = sum(entry.get('status') == 'accepted' and bool(entry.get('upo')) for entry in entries)
            receipts = sum(bool(entry.get('upo')) for entry in entries)
            result[form] = {'form': form, 'count': len(entries), 'entries': entries,
                            'status_text': f'Przyjęte: {accepted}/{len(entries)}', 'upo_count': receipts,
                            'sent_at': None, 'upo_at': None}
    return result
