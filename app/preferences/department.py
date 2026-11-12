"""A shared navigation default for payroll staff, independent of access rights."""
import re
from app.db.dao_conn import get_conn


def default_department(actor_id):
    if not actor_id:
        return 'accounting'
    with get_conn() as conn:
        row = conn.execute('''SELECT COALESCE(p.is_admin,0) AS admin,r.nazwa,
            EXISTS(SELECT 1 FROM firmy f WHERE f.kadrowa_id=p.id) AS payroll,
            EXISTS(SELECT 1 FROM firmy f WHERE f.ksiegowa_id=p.id) AS accounting
            FROM pracownicy p LEFT JOIN role r ON r.id=p.rola_id
            WHERE p.id=%s AND COALESCE(p.aktywny,1)=1''', (actor_id,)).fetchone()
    if not row or row['admin']:
        return 'accounting'
    name = str(row['nazwa'] or '').casefold().replace('ł', 'l')
    if name.strip() == 'admin':
        return 'accounting'
    if 'kadr' in name or 'payroll' in name or re.search(r'\b(?:plac|place|placowa|placowy|hr)\b', name):
        return 'payroll'
    if any(part in name for part in ('ksieg', 'księg', 'account')):
        return 'accounting'
    return 'payroll' if row['payroll'] and not row['accounting'] else 'accounting'
