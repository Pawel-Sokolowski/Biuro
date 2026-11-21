"""Trwały zapis wysyłki masowej: kto, kiedy, co i do kogo wysłano.

Każda partia jest dopisywana od razu, więc awaria aplikacji w trakcie wysyłki
nie gubi informacji o tym, do kogo wiadomość już poszła.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db.dao_conn import get_conn
from app.remote.boundary import endpoint as office_endpoint

_TABLE = 'bulk_mail_runs'
_BATCHES = 'bulk_mail_batches'


def _stamp() -> str:
    return datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()


def _plan_json(plan):
    values = asdict(plan)
    values.pop('attachments')
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


@office_endpoint('app.services.bulk_mail_log.start_run')
def start_run(plan, attachment_names=()) -> int:
    """Zapisuje plan wysyłki przed pierwszym e-mailem i zwraca jego id."""
    from app.email.email_storage import get_email_accounts_for_employee
    if not any(row['id'] == plan.account_id and (row.get('email') or '').casefold() == plan.sender.casefold()
               for row in get_email_accounts_for_employee(plan.actor_id)):
        raise PermissionError('Brak dostępu do konta nadawcy.')
    attachments = [Path(path).name for path in (attachment_names or plan.attachments)]
    with get_conn() as conn:
        return conn.execute(
            f'''INSERT INTO {_TABLE}(actor_id, account_id, sender, subject, body, attachments,
                                    recipients_total, started_at, plan_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
            (plan.actor_id, plan.account_id, plan.sender, plan.subject or '', plan.body or '',
             json.dumps(attachments, ensure_ascii=False), len(plan.recipients), _stamp(), _plan_json(plan)),
        ).fetchone()[0]


def _claim_batch(run_id, plan, batch_no, recipients):
    with get_conn() as conn:
        run = conn.execute(f'SELECT plan_json, finished_at, attachments FROM {_TABLE} WHERE id=%s AND actor_id=%s FOR UPDATE',
                           (run_id, plan.actor_id)).fetchone()
        if not run or run['plan_json'] != _plan_json(plan):
            raise PermissionError('Brak dostępu do tej kampanii.')
        if len(plan.attachments) != len(json.loads(run['attachments'])):
            raise ValueError('Zmieniono zestaw załączników kampanii.')
        previous = conn.execute(f'SELECT status, info FROM {_BATCHES} WHERE run_id=%s AND batch_no=%s',
                                (run_id, batch_no)).fetchone()
        if previous:
            state = previous['status']
            return {'ok': state == 'sent', 'blocked': state == 'blocked',
                    'info': previous['info'] or 'Wynik tej partii wymaga sprawdzenia.'}
        if run['finished_at'] or (batch_no and not conn.execute(
                f"SELECT 1 FROM {_BATCHES} WHERE run_id=%s AND batch_no=%s AND status='sent'",
                (run_id, batch_no - 1)).fetchone()):
            raise ValueError('Kampania jest zakończona albo poprzednia partia nie została wysłana.')
        conn.execute(f"INSERT INTO {_BATCHES}(run_id, batch_no, recipients, status, created_at) VALUES (%s,%s,%s,'sending',%s)",
                     (run_id, batch_no, json.dumps(recipients, ensure_ascii=False), _stamp()))


@office_endpoint('app.services.bulk_mail_log.send_campaign_batch')
def send_campaign_batch(run_id, plan, batch_no):
    from app.email.bulk_mail import send_bulk_batch
    recipients = plan.recipients
    if type(batch_no) is not int or not 0 <= batch_no < (len(recipients) + plan.batch_size - 1) // plan.batch_size:
        raise ValueError('Nieprawidłowy numer partii.')
    offset = batch_no * plan.batch_size
    recipients = recipients[offset:offset + plan.batch_size]
    previous = _claim_batch(run_id, plan, batch_no, recipients)
    if previous is not None:
        return previous
    try:
        result = send_bulk_batch(plan, recipients)
    except Exception:
        result = {'ok': False, 'blocked': False, 'info': 'Nie udało się potwierdzić wyniku wysyłki.'}
    state = 'sent' if result['ok'] else 'blocked' if result['blocked'] else 'uncertain'
    with get_conn() as conn:
        conn.execute(f'UPDATE {_BATCHES} SET status=%s, info=%s WHERE run_id=%s AND batch_no=%s',
                     (state, result['info'], run_id, batch_no))
    return result


@office_endpoint('app.services.bulk_mail_log.finish_run')
def finish_run(run_id, actor_id, *, stopped=False) -> bool:
    with get_conn() as conn:
        run = conn.execute(f'SELECT recipients_total, finished_at FROM {_TABLE} WHERE id=%s AND actor_id=%s FOR UPDATE',
                           (run_id, actor_id)).fetchone()
        if not run:
            raise PermissionError('Brak dostępu do tej kampanii.')
        batches = conn.execute(f'SELECT recipients, status FROM {_BATCHES} WHERE run_id=%s', (run_id,)).fetchall()
        if not run['finished_at']:
            handled = sum(len(json.loads(row['recipients'])) for row in batches if row['status'] != 'blocked')
            conn.execute(f'UPDATE {_TABLE} SET finished_at=%s, remaining=%s, stopped=%s WHERE id=%s',
                         (_stamp(), max(0, run['recipients_total'] - handled), int(stopped), run_id))
        return all(row['status'] != 'sending' for row in batches)


@office_endpoint('app.services.bulk_mail_log.history')
def history(limit: int = 50) -> list[dict]:
    """Ostatnie wysyłki masowe z podsumowaniem partii (dla administratora)."""
    with get_conn() as conn:
        rows = [dict(row) for row in conn.execute(
            f'SELECT * FROM {_TABLE} ORDER BY id DESC LIMIT %s', (max(1, min(limit, 100)),))]
        grouped = {run['id']: [] for run in rows}
        if grouped:
            for batch in conn.execute(f'''SELECT run_id, recipients, status, info, created_at
                                           FROM {_BATCHES} WHERE run_id=ANY(%s) ORDER BY id''', (list(grouped),)):
                item = dict(batch)
                grouped[item.pop('run_id')].append(item)
        result = []
        for run in rows:
            run['batches'] = grouped[run['id']]
            run.pop('plan_json', None)
            run['attachments'] = json.loads(run.get('attachments') or '[]')
            result.append(run)
        return result
