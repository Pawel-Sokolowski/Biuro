"""Trwały zapis wysyłki masowej: kto, kiedy, co i do kogo wysłano.

Każda partia jest dopisywana od razu, więc awaria aplikacji w trakcie wysyłki
nie gubi informacji o tym, do kogo wiadomość już poszła.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn
from app.db.service_tables_schema import BULK_MAIL_TABLES

_TABLE = 'bulk_mail_runs'
_BATCHES = 'bulk_mail_batches'


CREATE_STATEMENTS = BULK_MAIL_TABLES


def ensure_schema(conn) -> None:
    for statement in CREATE_STATEMENTS:
        conn.execute(statement)


def _stamp() -> str:
    return datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()


def start_run(plan) -> int:
    """Zapisuje plan wysyłki przed pierwszym e-mailem i zwraca jego id."""
    attachments = [Path(path).name for path in plan.attachments]
    with get_conn() as conn:
        ensure_schema(conn)
        return conn.execute(
            f'''INSERT INTO {_TABLE}(actor_id, account_id, sender, subject, body, attachments,
                                    recipients_total, started_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
            (plan.actor_id, plan.account_id, plan.sender, plan.subject or '', plan.body or '',
             json.dumps(attachments, ensure_ascii=False), len(plan.recipients), _stamp()),
        ).fetchone()[0]


def record_batch(run_id, entry) -> None:
    with get_conn() as conn:
        ensure_schema(conn)
        conn.execute(f'INSERT INTO {_BATCHES}(run_id, recipients, status, info, created_at) '
                     'VALUES (%s, %s, %s, %s, %s)',
                     (run_id, json.dumps(list(entry.get('recipients') or []), ensure_ascii=False),
                      entry.get('status') or '', entry.get('info') or '', _stamp()))


def finish_run(run_id, *, remaining=0, stopped=False) -> None:
    with get_conn() as conn:
        ensure_schema(conn)
        conn.execute(f'UPDATE {_TABLE} SET finished_at=%s, remaining=%s, stopped=%s WHERE id=%s',
                     (_stamp(), int(remaining), 1 if stopped else 0, run_id))


def history(limit: int = 50) -> list[dict]:
    """Ostatnie wysyłki masowe z podsumowaniem partii (dla administratora)."""
    with get_conn() as conn:
        rows = conn.execute(f'SELECT * FROM {_TABLE} ORDER BY id DESC LIMIT %s', (limit,)).fetchall()
        result = []
        for row in rows:
            run = dict(row)
            batches = conn.execute(f'SELECT recipients, status, info, created_at FROM {_BATCHES} '
                                   'WHERE run_id=%s ORDER BY id', (run['id'],)).fetchall()
            run['batches'] = [dict(batch) for batch in batches]
            run['attachments'] = json.loads(run.get('attachments') or '[]')
            result.append(run)
        return result
