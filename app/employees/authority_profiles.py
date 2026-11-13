"""Encrypted personal identity fields for repeatable PEL/UPL preparation."""
import json
import re
from app.db.dao_conn import get_conn
from app.utils.crypto import encrypt_password, decrypt_password

FIELDS = {'pesel': 'PESEL', 'nip': 'NIP', 'birth_date': 'Data urodzenia (RRRR-MM-DD)',
          'identity_number': 'Seria i numer dokumentu tożsamości', 'identity_country': 'Kraj wydania (np. PL)',
          'identity_type': 'Typ dokumentu: 1 — dowód, 2 — paszport'}


def load(employee_id, actor_id):
    from app.security.authorization import is_administrator, allowed_tabs
    if not is_administrator(actor_id) and not set(allowed_tabs(actor_id)) & {'Klienci', 'Widok miesięczny', 'Ustawienia'}:
        raise PermissionError('Brak dostępu do danych pełnomocnika.')
    with get_conn() as conn:
        row = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (f'employee_authority_{employee_id}',)).fetchone()
    return json.loads(decrypt_password(row[0])) if row else {}


def save(employee_id, values, actor_id):
    from app.security.authorization import can
    if not can(actor_id, 'can_manage_users'):
        raise PermissionError('Brak uprawnień do edycji danych pracowników.')
    values = {key: str(values.get(key) or '').strip() for key in FIELDS}
    for key, size in (('pesel', 11), ('nip', 10)):
        if values[key] and not re.fullmatch('[0-9]{' + str(size) + '}', values[key]):
            raise ValueError(f'{FIELDS[key]} musi mieć {size} cyfr.')
    if values['birth_date']:
        from datetime import date
        date.fromisoformat(values['birth_date'])
    if values['identity_type'] not in ('', '1', '2'):
        raise ValueError('Nieprawidłowy typ dokumentu.')
    if any(len(v) > 128 for v in values.values()):
        raise ValueError('Dane pełnomocnika są zbyt długie.')
    with get_conn() as conn:
        if not conn.execute('SELECT 1 FROM pracownicy WHERE id=%s', (employee_id,)).fetchone():
            raise ValueError('Pracownik nie istnieje.')
        conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc',
                     (f'employee_authority_{employee_id}', encrypt_password(json.dumps(values))))
        conn.execute("INSERT INTO audit_log(tabela,rekord_id,akcja,uzytkownik_id,szczegoly) VALUES ('pracownicy',%s,'edycja',%s,%s)",
                     (employee_id, actor_id, json.dumps({'authority_profile_changed': True})))
