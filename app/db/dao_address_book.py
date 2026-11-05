"""Shared address-book storage; company records are not modified."""
import psycopg
from psycopg.types.json import Jsonb

from app.db.dao_conn import get_conn
from app.email.address_book import CORE_FIELDS, merge_missing, normalize_contact, normalize_email
from app.security.authorization import allowed_tabs


def _authorize(actor_id, conn):
    if (not actor_id or 'E-mail' not in allowed_tabs(actor_id)
            or not conn.execute('SELECT 1 FROM pracownicy WHERE id = %s AND COALESCE(aktywny, 1) = 1', (actor_id,)).fetchone()):
        raise PermissionError('Brak dostępu do wspólnej książki adresowej.')


def _public(row):
    if row is None:
        return None
    contact = dict(row.get('details') or {})
    contact.update({field: row[field] for field in CORE_FIELDS})
    contact.update(id=row['id'], version=row['revision'])
    if row.get('total_count') is not None:
        contact['total_count'] = row['total_count']
    return contact


def _details(contact):
    return {key: value for key, value in contact.items() if key not in CORE_FIELDS and key not in ('id', 'version', 'total_count')}


def list_contacts(actor_id, query='', limit=200):
    needle = str(query or '').strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    conn = get_conn()
    try:
        _authorize(actor_id, conn)
        rows = conn.execute(
            "SELECT *, count(*) OVER() AS total_count FROM global_contacts "
            "WHERE concat_ws(' ', email, additional_email, first_name, last_name, display_name, nickname, company) ILIKE %s "
            "ORDER BY lower(COALESCE(NULLIF(display_name, ''), NULLIF(btrim(first_name || ' ' || last_name), ''), email)), id LIMIT %s",
            (f'%{needle}%', max(1, min(int(limit), 500))),
        ).fetchall()
        return [_public(row) for row in rows]
    finally:
        conn.close()


def find_contact(actor_id, address):
    address = normalize_email(address)
    conn = get_conn()
    try:
        _authorize(actor_id, conn)
        return _public(conn.execute(
            'SELECT * FROM global_contacts WHERE lower(email) = lower(%s) OR lower(additional_email) = lower(%s) '
            'ORDER BY (lower(email) = lower(%s)) DESC, id LIMIT 1', (address, address, address),
        ).fetchone())
    finally:
        conn.close()


def save_contact(actor_id, values, contact_id=None, expected_version=None):
    contact = normalize_contact(values)
    conn = get_conn()
    try:
        _authorize(actor_id, conn)
        params = [contact[field] for field in CORE_FIELDS] + [Jsonb(_details(contact)), actor_id]
        if contact_id is None:
            row = conn.execute(
                f'INSERT INTO global_contacts ({", ".join(CORE_FIELDS)}, details, updated_by, created_by) '
                f'VALUES ({", ".join(["%s"] * (len(CORE_FIELDS) + 3))}) RETURNING *',
                (*params, actor_id),
            ).fetchone()
        else:
            if expected_version is None:
                raise ValueError('Wczytaj aktualny kontakt przed zapisaniem zmian.')
            row = conn.execute(
                f'UPDATE global_contacts SET {", ".join(field + " = %s" for field in CORE_FIELDS)}, '
                'details = %s, updated_by = %s, updated_at = clock_timestamp(), revision = revision + 1 '
                'WHERE id = %s AND revision = %s RETURNING *',
                (*params, contact_id, expected_version),
            ).fetchone()
            if row is None:
                raise ValueError('Kontakt zmienił się lub został usunięty przez innego pracownika. Wczytaj go ponownie.')
        conn.commit()
        return _public(row)
    except psycopg.errors.UniqueViolation as exc:
        conn.rollback()
        raise ValueError('Kontakt z tym podstawowym adresem e-mail już istnieje. Otwórz go w książce adresowej.') from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_contact(actor_id, contact_id, expected_version):
    conn = get_conn()
    try:
        _authorize(actor_id, conn)
        deleted = conn.execute('DELETE FROM global_contacts WHERE id = %s AND revision = %s RETURNING id',
                               (contact_id, expected_version)).fetchone()
        if deleted is None:
            raise ValueError('Kontakt został zmieniony lub usunięty. Odśwież listę przed usunięciem.')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def import_contacts(actor_id, values):
    # Lock contacts in the same order in concurrent imports. Fill missing values
    # against the current database row, not the earlier preview snapshot.
    contacts = sorted((normalize_contact(value) for value in values), key=lambda contact: contact['email'].casefold())
    counts = {'created': 0, 'updated': 0, 'unchanged': 0}
    conn = get_conn()
    try:
        _authorize(actor_id, conn)
        for contact in contacts:
            row = conn.execute(
                f'INSERT INTO global_contacts ({", ".join(CORE_FIELDS)}, details, created_by, updated_by) '
                f'VALUES ({", ".join(["%s"] * (len(CORE_FIELDS) + 3))}) '
                'ON CONFLICT (lower(email)) DO NOTHING RETURNING *',
                (*[contact[field] for field in CORE_FIELDS], Jsonb(_details(contact)), actor_id, actor_id),
            ).fetchone()
            if row is not None:
                counts['created'] += 1
                continue
            row = conn.execute('SELECT * FROM global_contacts WHERE lower(email) = lower(%s) FOR UPDATE', (contact['email'],)).fetchone()
            # A concurrent deletion after the INSERT conflict is a retryable
            # import error, never a reason to commit a partial address book.
            if row is None:
                raise ValueError('Kontakt usunięto podczas importu. Uruchom import ponownie.')
            existing = normalize_contact(_public(row))
            merged = merge_missing(existing, contact)
            if merged == existing:
                counts['unchanged'] += 1
                continue
            conn.execute(
                f'UPDATE global_contacts SET {", ".join(field + " = %s" for field in CORE_FIELDS)}, '
                'details = %s, updated_by = %s, updated_at = clock_timestamp(), revision = revision + 1 WHERE id = %s',
                (*[merged[field] for field in CORE_FIELDS], Jsonb(_details(merged)), actor_id, row['id']),
            )
            counts['updated'] += 1
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
