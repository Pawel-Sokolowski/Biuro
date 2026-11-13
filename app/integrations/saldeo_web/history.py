"""Idempotent inbox: persist first, apply atomically, retry unresolved entries."""
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from app.db.dao_conn import get_conn

COMPLETE = re.compile(r'(?:Klient\s*\((?P<wrapped>[^)]+)\)|(?P<plain>[^\n]+?))\s+informuje\s+o\s+dostarczeniu\s+(?:kompletu\s+)?dokument\w*\s+za\s+(?P<month>\d{1,2})\s*[/.-]\s*(?P<year>\d{4})', re.I)


def fold(text):
    return re.sub(r'\s+', ' ', text.strip()).casefold()


def parse(text):
    match = COMPLETE.search(text)
    if not match or not 1 <= int(match['month']) <= 12 or not 2000 <= int(match['year']) <= 2100:
        return None
    return (match['wrapped'] or match['plain']).strip(), f"{match['year']}-{int(match['month']):02d}"


def ingest(items, source):
    """A failed page/batch rolls back its records, allowing a full retry next poll."""
    inserted = 0
    with get_conn() as conn:
        for item in items:
            body = str(item['text']).strip()[:8000]
            if not body:
                continue
            stamp = str(item.get('time') or '')[:100]
            identity = str(item.get('id') or '') + '\n' + fold(body) + '\n' + stamp
            key = hashlib.sha256((source + '\n' + identity).encode()).hexdigest()
            parsed = parse(body)
            result = conn.execute('''INSERT INTO saldeo_web_events
                (event_key,body,source_time,company_name,miesiac,state) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(event_key) DO NOTHING''',
                (key, body, stamp, parsed[0] if parsed else None, parsed[1] if parsed else None,
                 'pending' if parsed else 'ignored'))
            inserted += result.rowcount
    return inserted


def apply_pending():
    applied = 0
    with get_conn() as conn:
        companies = conn.execute("SELECT id,nazwa_saldeo_firmy FROM firmy WHERE NULLIF(trim(nazwa_saldeo_firmy),'') IS NOT NULL").fetchall()
        by_name = {}
        for company in companies:
            by_name.setdefault(fold(company[1]), []).append(company[0])
        events = conn.execute("""SELECT event_key,company_name,miesiac FROM saldeo_web_events
            WHERE state IN ('pending','needs_mapping') ORDER BY detected_at LIMIT 500
            FOR UPDATE SKIP LOCKED""").fetchall()
        for key, name, month in events:
            ids = by_name.get(fold(name), [])
            if len(ids) != 1:
                conn.execute("UPDATE saldeo_web_events SET state='needs_mapping',attempts=attempts+1 WHERE event_key=%s", (key,))
                continue
            today = datetime.now(ZoneInfo('Europe/Warsaw')).date().isoformat()
            conn.execute('''INSERT INTO statusy_miesieczne(firma_id,miesiac,dokumenty_dostarczone,data_dostarczenia)
                VALUES (%s,%s,1,%s) ON CONFLICT(firma_id,miesiac) DO UPDATE SET
                dokumenty_dostarczone=1,data_dostarczenia=EXCLUDED.data_dostarczenia
                WHERE COALESCE(statusy_miesieczne.dokumenty_dostarczone,0)=0''', (ids[0], month, today))
            conn.execute("UPDATE saldeo_web_events SET state='applied',firma_id=%s,applied_at=now(),attempts=attempts+1 WHERE event_key=%s", (ids[0], key))
            applied += 1
    return applied


def list_history(actor_id, limit=500):
    from app.security.authorization import is_administrator, assigned_company_ids, allowed_tabs
    # Use the same company visibility rules as the rest of Biuro.
    if is_administrator(actor_id):
        with get_conn() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM saldeo_web_events ORDER BY detected_at DESC LIMIT %s', (limit,)).fetchall()]
    if not {'Widok miesięczny', 'Sprawdzanie'}.intersection(allowed_tabs(actor_id)):
        return []
    ids = assigned_company_ids(actor_id)
    with get_conn() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM saldeo_web_events WHERE firma_id=ANY(%s) ORDER BY detected_at DESC LIMIT %s', (list(ids), limit)).fetchall()]


def notification_batches(actor_id):
    """Group one applied transaction into one inbox notification instead of a popup per firm."""
    rows = list_history(actor_id, 200)
    batches = {}
    for row in rows:
        if row['state'] != 'applied':
            continue
        batches.setdefault(str(row['applied_at']), []).append(row)
    return [{'key': 'saldeo:' + hashlib.sha256('|'.join(sorted(r['event_key'] for r in batch)).encode()).hexdigest(),
             'kind': 'saldeo', 'title': f"Saldeo: komplet dokumentów — {len(batch)} zgłoszeń: " + ', '.join(
                 f"{r['company_name']} ({r['miesiac']})" for r in batch[:5]),
             'target': {'kind': 'saldeo'}} for batch in list(batches.values())[:20]]
