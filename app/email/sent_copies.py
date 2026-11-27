"""Stage full MIME messages until IMAP and the local index confirm a copy."""
import hashlib
import logging
from email import policy

from app.config import get_schema
from app.db.dao_conn import advisory_lock, get_conn
from app.utils.crypto import decrypt_bytes, encrypt_bytes

logger = logging.getLogger(__name__)


def stage_copy(account_id, message, folder=None, *, confirmed=False):
    payload = encrypt_bytes(message.as_bytes(policy=policy.SMTP))
    with get_conn() as conn:
        row = conn.execute("""
            INSERT INTO email_sent_copies (konto_id, account_email, message_id, target, payload, state)
            SELECT id, email, %s, %s, %s, %s FROM konta_email WHERE id = %s
            RETURNING id
        """, (str(message['Message-ID']), folder or '', payload, 'pending' if confirmed else 'reserved', account_id)).fetchone()
        if not row:
            raise ValueError('Nie znaleziono konta do zapisania kopii wiadomości.')
        return row[0]


def confirm_copy(copy_id, message):
    payload = encrypt_bytes(message.as_bytes(policy=policy.SMTP))
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE email_sent_copies SET state = 'pending', payload = %s WHERE id = %s AND state = 'reserved' RETURNING id",
            (payload, copy_id),
        ).fetchone()
        if not row:
            raise ValueError('Nie znaleziono rezerwacji kopii wiadomości.')


def discard_reservation(copy_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM email_sent_copies WHERE id = %s AND state = 'reserved'", (copy_id,))


def _sent_folder(account_id):
    from app.email.email_storage import find_special_folder
    from app.email.email_sync import list_folders_from_imap_and_store

    target = find_special_folder(account_id, 'sent')
    if not target:
        list_folders_from_imap_and_store(account_id)
        target = find_special_folder(account_id, 'sent')
    return target


def flush_copy(copy_id):
    from app.email.email_sync import append_sent_copy, sync_folder

    lock_key = hashlib.blake2b(f'biuro-sent-copy:{get_schema()}:{copy_id}'.encode(), digest_size=8).digest()
    with advisory_lock(int.from_bytes(lock_key, 'big', signed=True)) as acquired:
        if not acquired:
            return False
        with get_conn() as conn:
            row = conn.execute("""
                SELECT c.*, (lower(c.account_email) = lower(k.email)) AS same_mailbox
                FROM email_sent_copies c JOIN konta_email k ON k.id = c.konto_id
                WHERE c.id = %s AND c.state IN ('pending', 'appended')
            """, (copy_id,)).fetchone()
            if not row:
                return True
            # Claim the next retry time before contacting IMAP, even if this process stops.
            conn.execute("UPDATE email_sent_copies SET retry_at = now() + interval '2 minutes' WHERE id = %s", (copy_id,))
        account_id = row['konto_id']
        try:
            if not row['same_mailbox']:
                return False
            target = row['target'] or _sent_folder(account_id)
            if not target:
                return False
            if row['state'] == 'pending':
                # Freeze the resolved mailbox before APPEND, so an interrupted attempt
                # can check the same folder by Message-ID.
                if not row['target']:
                    with get_conn() as conn:
                        conn.execute('UPDATE email_sent_copies SET target = %s WHERE id = %s', (target, copy_id))
                if not append_sent_copy(account_id, target, decrypt_bytes(row['payload']), row['message_id']):
                    return False
                with get_conn() as conn:
                    conn.execute("UPDATE email_sent_copies SET state = 'appended' WHERE id = %s", (copy_id,))
            sync_folder(account_id, target)
            with get_conn() as conn:
                conn.execute('DELETE FROM email_sent_copies WHERE id = %s', (copy_id,))
            return True
        except Exception:
            logger.warning('sent_copy_retry_deferred copy_id=%s', copy_id, exc_info=True)
            return False


def retry_due_copies(account_id, stop=None):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id FROM email_sent_copies
            WHERE konto_id = %s AND state IN ('pending', 'appended') AND retry_at <= now()
            ORDER BY retry_at, id LIMIT 5
        """, (account_id,)).fetchall()
    for row in rows:
        if stop is not None and stop.is_set():
            break
        flush_copy(row[0])
