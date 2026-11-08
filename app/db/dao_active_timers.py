"""Monotonic timers with recoverable checkpoints, owned by one app instance."""
from dataclasses import dataclass, field
from datetime import datetime
import time
from uuid import uuid4

from psycopg.types.json import Jsonb
from app.db.dao_conn import get_conn


@dataclass
class ActiveTimer:
    session_id: str
    owner: str
    started_at: datetime
    base_seconds: int = 0
    monotonic_start: float = field(default_factory=time.monotonic)
    month: str = ''

    def elapsed(self):
        return self.base_seconds + max(0, int(time.monotonic() - self.monotonic_start))

    def checkpoint(self):
        return self.session_id, self.owner, self.elapsed()


def start(conn, employee, company, month, kind, activity, started_at):
    session = ActiveTimer(str(uuid4()), str(uuid4()), started_at, month=month)
    conn.execute('''INSERT INTO active_timer_sessions
        (session_id, owner_token, pracownik_id, firma_id, miesiac, typ, activity, started_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
        (session.session_id, session.owner, employee, company, month, kind, Jsonb(activity or {}), started_at.astimezone()))
    return session


def finish(conn, session):
    if session is not None:
        row = conn.execute('DELETE FROM active_timer_sessions WHERE session_id=%s AND owner_token=%s RETURNING session_id',
                           (session.session_id, session.owner)).fetchone()
        if row is None:
            raise ValueError('Ten stoper został odzyskany w innym oknie. Odśwież widok.')


def checkpoint(snapshots):
    if not snapshots:
        return
    with get_conn() as conn:
        for session_id, owner, elapsed in snapshots:
            conn.execute('''UPDATE active_timer_sessions SET elapsed_seconds=GREATEST(elapsed_seconds,%s), checkpoint_at=now()
                WHERE session_id=%s AND owner_token=%s''', (elapsed, session_id, owner))


def recoverable(employee):
    with get_conn() as conn:
        return [dict(row) for row in conn.execute('''SELECT a.*, COALESCE(NULLIF(f.nazwa_skrocona,''),f.nazwa,'Czynność ogólna') AS company
            FROM active_timer_sessions a LEFT JOIN firmy f ON f.id=a.firma_id
            WHERE a.pracownik_id=%s AND a.checkpoint_at < now() - interval '90 seconds'
            ORDER BY a.started_at''', (employee,)).fetchall()]


def claim(employee, session_id):
    owner = str(uuid4())
    with get_conn() as conn:
        row = conn.execute('''UPDATE active_timer_sessions SET owner_token=%s, checkpoint_at=now(), recovered_at=now()
            WHERE session_id=%s AND pracownik_id=%s AND checkpoint_at < now() - interval '90 seconds'
            RETURNING *''', (owner, session_id, employee)).fetchone()
        if row is None:
            raise ValueError('Stoper jest aktywny albo został już odzyskany na innym komputerze.')
    row = dict(row)
    started = row['started_at'].astimezone().replace(tzinfo=None)
    session = ActiveTimer(str(row['session_id']), owner, started, row['elapsed_seconds'], month=row['miesiac'])
    return row, session
