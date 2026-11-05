"""Transactional calendar outbox and stable remote identities.

Only a matching revision may be acknowledged. Network operations serialize per
calendar with a session advisory lock; local edits never wait for network I/O.
"""
from contextlib import contextmanager
import hashlib
import json
import threading
import uuid

from psycopg.types.json import Jsonb

from app.config import get_schema
from app.db.dao_conn import get_conn

_held = threading.local()


def caldav_scope(conn=None):
    if conn is None:
        with get_conn() as db:
            return caldav_scope(db)
    settings = dict(conn.execute(
        "SELECT klucz,wartosc FROM app_settings WHERE klucz = ANY(%s)",
        (["caldav_url", "caldav_login", "caldav_kalendarz"],),
    ).fetchall())
    return caldav_scope_values(*(settings.get(k, "") for k in
                                ("caldav_url", "caldav_login", "caldav_kalendarz")))


def caldav_scope_values(url, login, calendar):
    if not url or not login:
        return None
    return json.dumps([url.strip(), login.strip(), calendar.strip()], separators=(",", ":"))


def enqueue(conn, actor, provider, scope, remote_id, event_id, action, payload):
    return dict(conn.execute("""
        INSERT INTO calendar_outbox(pracownik_id,provider,calendar_id,remote_id,event_id,action,payload)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(pracownik_id,provider,calendar_id,remote_id) DO UPDATE SET
          revision=nextval('calendar_outbox_revision'),event_id=EXCLUDED.event_id,
          action=EXCLUDED.action,payload=EXCLUDED.payload
        RETURNING *""", (actor, provider, scope, remote_id, event_id, action, Jsonb(payload))).fetchone())


def bind(conn, actor, provider, scope, event_id, known=None):
    remote_id = known or (uuid.uuid4().hex if provider == "google" else f"{uuid.uuid4()}@biuro")
    return conn.execute("""INSERT INTO calendar_sync_keys
        (pracownik_id,provider,calendar_id,event_id,remote_id) VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT(pracownik_id,provider,calendar_id,event_id) DO UPDATE SET event_id=EXCLUDED.event_id
        RETURNING remote_id""", (actor, provider, scope, event_id, remote_id)).fetchone()[0]


def queue_event(conn, actor, event):
    """Called in the same transaction as the local save, including first creation."""
    targets = []
    if event.get("google_calendar_id"):
        targets.append(("google", event["google_calendar_id"], event.get("google_event_id")))
    scope = event.get("caldav_calendar_id") or caldav_scope(conn)
    if scope and not event.get("google_calendar_id"):
        targets.append(("caldav", scope, event.get("caldav_uid")))
    for provider, scope, known in targets:
        for previous in conn.execute("SELECT * FROM calendar_sync_keys WHERE event_id=%s AND provider=%s AND calendar_id<>%s",
                                     (event["id"], provider, scope)).fetchall():
            enqueue(conn, actor, provider, previous["calendar_id"], previous["remote_id"], event["id"], "delete", {})
        rid = bind(conn, actor, provider, scope, event["id"], known)
        enqueue(conn, actor, provider, scope, rid, event["id"], "upsert", event)


def queue_deletion(conn, actor, event):
    # Bind legacy events before deleting them, so their remote object is retained.
    if event.get("google_event_id") and event.get("google_calendar_id"):
        bind(conn, actor, "google", event["google_calendar_id"], event["id"], event["google_event_id"])
    scope = event.get("caldav_calendar_id") or caldav_scope(conn)
    if scope and event.get("caldav_uid"):
        bind(conn, actor, "caldav", scope, event["id"], event["caldav_uid"])
    for key in conn.execute("SELECT * FROM calendar_sync_keys WHERE event_id=%s", (event["id"],)).fetchall():
        enqueue(conn, actor, key["provider"], key["calendar_id"], key["remote_id"], event["id"], "delete", {})


def pending(conn, event_id):
    return conn.execute("SELECT 1 FROM calendar_outbox WHERE event_id=%s LIMIT 1", (event_id,)).fetchone() is not None


def remote_pending(actor, provider, scope, remote_id):
    with get_conn() as conn:
        return conn.execute("""SELECT 1 FROM calendar_outbox WHERE
            pracownik_id=%s AND provider=%s AND calendar_id=%s AND remote_id=%s""",
            (actor, provider, scope, remote_id)).fetchone() is not None


def operations(actor, provider, scope=None):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""SELECT * FROM calendar_outbox WHERE
            pracownik_id=%s AND provider=%s AND (%s::text IS NULL OR calendar_id=%s)
            ORDER BY created_at,remote_id""", (actor, provider, scope, scope)).fetchall()]


def stage(actor, provider, scope, event_id):
    """Use the latest persisted edit, never a caller's stale event snapshot."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wydarzenia WHERE id=%s AND pracownik_id=%s FOR UPDATE",
                           (event_id, actor)).fetchone()
        if not row:
            return None
        event = dict(row)
        target = event.get("google_calendar_id" if provider == "google" else "caldav_calendar_id")
        if target is not None and target != scope:
            return None
        known = event.get("google_event_id" if provider == "google" else "caldav_uid")
        rid = bind(conn, actor, provider, scope, event_id, known)
        op = conn.execute("""SELECT * FROM calendar_outbox WHERE pracownik_id=%s AND provider=%s
            AND calendar_id=%s AND remote_id=%s""", (actor, provider, scope, rid)).fetchone()
        return dict(op) if op else enqueue(conn, actor, provider, scope, rid, event_id, "upsert", event)


def acknowledge(op):
    with get_conn() as conn:
        return conn.execute("""DELETE FROM calendar_outbox WHERE pracownik_id=%s AND provider=%s
            AND calendar_id=%s AND remote_id=%s AND revision=%s""",
            (op["pracownik_id"], op["provider"], op["calendar_id"], op["remote_id"], op["revision"])).rowcount == 1


def migrate_google(actor):
    """Consume the old JSON queue atomically without replacing newer outbox rows."""
    with get_conn() as conn:
        key = f"google_pending_changes_{actor}"
        row = conn.execute("SELECT wartosc FROM app_settings WHERE klucz=%s FOR UPDATE", (key,)).fetchone()
        if row is None:
            return
        changes = json.loads(row[0])
        if not isinstance(changes, dict):
            raise ValueError("Nieprawidłowa kolejka zmian Google Calendar.")
        for rid, change in changes.items():
            if not isinstance(change, dict) or not change.get("kalendarz") or change.get("akcja") not in {"upsert", "delete"}:
                continue
            scope = change["kalendarz"]
            event = conn.execute("""SELECT * FROM wydarzenia WHERE pracownik_id=%s
                AND google_calendar_id=%s AND google_event_id=%s""", (actor, scope, rid)).fetchone()
            conn.execute("""INSERT INTO calendar_outbox(pracownik_id,provider,calendar_id,remote_id,event_id,action,payload)
                VALUES (%s,'google',%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (actor, scope, rid, event["id"] if event else None,
                 change["akcja"] if event else "delete", Jsonb(dict(event) if event else {})))
        conn.execute("DELETE FROM app_settings WHERE klucz=%s", (key,))


@contextmanager
def calendar_lock(actor, provider, scope):
    name = json.dumps([get_schema(), actor, provider, scope])
    held = getattr(_held, "keys", set())
    if name in held:
        yield
        return
    key = int.from_bytes(hashlib.blake2b(name.encode(), digest_size=8).digest(), "big", signed=True)
    conn = get_conn()
    try:
        conn.execute("SELECT pg_advisory_lock(%s)", (key,))
        conn.commit()
        _held.keys = held | {name}
        try:
            yield
        finally:
            _held.keys = held
            conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
            conn.commit()
    finally:
        conn.close()
