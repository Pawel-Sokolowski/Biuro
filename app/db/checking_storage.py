"""Read and write checking snapshots within the caller's transaction."""

from psycopg.types.json import Jsonb


def read_snapshot(conn, source, company_id, period='', *, lock=False):
    sql = """SELECT payload FROM company_checking_snapshots
             WHERE source=%s AND company_id=%s AND period=%s"""
    if lock:
        sql += ' FOR UPDATE'
    row = conn.execute(sql, (source, company_id, period)).fetchone()
    return row['payload'] if row else None


def save_snapshot(conn, source, company_id, period, payload):
    conn.execute("""
        INSERT INTO company_checking_snapshots(source, company_id, period, payload)
        VALUES (%s,%s,%s,%s) ON CONFLICT(source, company_id, period)
        DO UPDATE SET payload=EXCLUDED.payload
    """, (source, company_id, period, Jsonb(payload)))
