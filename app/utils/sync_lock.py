"""Cross-workstation IMAP leadership, released automatically on DB disconnect."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib

from app.config import get_schema
from app.db.dao_conn import get_coordination_conn as get_conn

_held = ContextVar('account_sync_locks', default=frozenset())


@contextmanager
def account_sync_lock(account_id, cooldown=0):
    key = int.from_bytes(hashlib.blake2b(f'imap:{get_schema()}:{account_id}'.encode(), digest_size=8).digest(), 'big', signed=True)
    held = _held.get()
    if key in held:
        yield True
        return
    with get_conn() as conn:
        acquired = conn.execute('SELECT pg_try_advisory_lock(%s)', (key,)).fetchone()[0]
        conn.commit()
        if not acquired:
            yield False
            return
        token = _held.set(held | {key})
        try:
            recent = cooldown and conn.execute('SELECT 1 FROM account_sync_state WHERE konto_id=%s '
                "AND checked_at > now() - (%s * interval '1 second')", (account_id, cooldown)).fetchone()
            conn.commit()  # no idle transaction while waiting on an IMAP server
            if recent:
                yield False
                return
            yield True
            if cooldown:
                conn.execute('INSERT INTO account_sync_state(konto_id, checked_at) VALUES (%s, now()) '
                             'ON CONFLICT(konto_id) DO UPDATE SET checked_at=EXCLUDED.checked_at', (account_id,))
                conn.commit()
        finally:
            _held.reset(token)
            # A failed cooldown write leaves the transaction aborted. Roll it
            # back before releasing the session lock to avoid leaking it to
            # the next borrower of this pooled connection.
            try:
                conn.rollback()
                conn.execute('SELECT pg_advisory_unlock(%s)', (key,))
                conn.commit()
            except Exception:
                conn._conn.close()
                raise
