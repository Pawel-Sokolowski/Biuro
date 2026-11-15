"""Versioned, company-bound e-ZLA export for the enova add-on (server only)."""
import base64
import hashlib
import json
import re

from app.companies.identities import payer_nips
from app.db.dao_companies import get_partners
from app.db.dao_conn import get_conn
from app.integrations.enova.accounting import require_access
from app.integrations.enova.document_counts import normalize_nip
from app.integrations.ezla.report import parse_report
from app.security.authorization import assigned_company_ids, can
from app.utils.crypto import decrypt_password
from app.utils.months import month_bounds

PAGE_BYTES = 16 * 1024 * 1024


def export_reports(actor_id, value):
    require_access(actor_id)
    if not can(actor_id, 'can_export_data'):
        raise PermissionError('Brak uprawnienia do eksportu e-ZLA.')
    nip, month, after = value.get('company_nip'), value.get('month'), value.get('after', '')
    if not isinstance(nip, str) or not re.fullmatch(r'[0-9]{10}', nip):
        raise ValueError('Podaj NIP firmy z bieżącej bazy enova.')
    if not isinstance(month, str):
        raise ValueError('Podaj miesiąc YYYY-MM.')
    month_bounds(month)
    if not isinstance(after, str) or (after and not re.fullmatch(r'ezla_report_[0-9]+_[a-f0-9]{64}', after)):
        raise ValueError('Nieprawidłowy znacznik strony.')
    with get_conn() as conn:
        companies = [row for row in conn.execute(
            'SELECT id,nip,forma_dzialalnosci FROM firmy WHERE id=ANY(%s)',
            (list(assigned_company_ids(actor_id)),)).fetchall() if normalize_nip(row['nip']) == nip]
        if len(companies) != 1:
            raise PermissionError('NIP nie wskazuje jednej dostępnej firmy w Biuro.')
        company = companies[0]
        prefix = f"ezla_report_{company['id']}_"
        # Materialize the exact company's subset before casting mixed settings to JSON.
        rows = conn.execute('''WITH reports AS MATERIALIZED (
            SELECT klucz,wartosc FROM app_settings WHERE starts_with(klucz,%s)
        ) SELECT klucz,wartosc FROM reports WHERE klucz>%s AND
            ((wartosc::jsonb->'months') ? %s OR wartosc::jsonb->>'needs_review'='true')
            ORDER BY klucz LIMIT 51''', (prefix, after, month)).fetchall()
    allowed = payer_nips(company, get_partners(company['id']))
    reports, size, cursor = [], 0, ''
    for row in rows[:50]:
        metadata = json.loads(row['wartosc'])
        raw = base64.b64decode(decrypt_password(metadata['xml_enc']), validate=True)
        parsed = parse_report(raw)
        checksum = hashlib.sha256(raw).hexdigest()
        if parsed.nip not in allowed or row['klucz'] != prefix + checksum:
            raise ValueError('Raport e-ZLA nie zgadza się z firmą lub sumą kontrolną. Sprawdź serwer Biuro.')
        if reports and size + len(raw) > PAGE_BYTES:
            cursor = reports[-1]['id']
            break
        reports.append({'id': row['klucz'], 'sha256': checksum, 'payer_nip': parsed.nip,
                        'xml': base64.b64encode(raw).decode('ascii')})
        size += len(raw)
    else:
        if len(rows) > 50:
            cursor = reports[-1]['id']
    # No credentials, SQL connection information, or office encryption key belong here.
    return {'schema': 1, 'company_nip': nip, 'month': month, 'reports': reports, 'next': cursor}
