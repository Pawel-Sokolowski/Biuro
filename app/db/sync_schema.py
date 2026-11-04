"""Incremental sync schema, shared by the revision and explicit repair bootstrap."""


def apply(conn):
    conn.execute("ALTER TABLE konta_email ADD COLUMN IF NOT EXISTS connection_revision BIGINT NOT NULL DEFAULT 1")
    conn.execute("""CREATE TABLE IF NOT EXISTS calendar_sync_keys (
        pracownik_id INTEGER NOT NULL REFERENCES pracownicy(id) ON DELETE CASCADE,
        provider TEXT NOT NULL CHECK(provider IN ('google','caldav')),
        calendar_id TEXT NOT NULL,
        event_id INTEGER NOT NULL REFERENCES wydarzenia(id) ON DELETE CASCADE,
        remote_id TEXT NOT NULL,
        PRIMARY KEY(pracownik_id,provider,calendar_id,event_id),
        UNIQUE(pracownik_id,provider,calendar_id,remote_id))""")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS calendar_outbox_revision")
    conn.execute("""CREATE TABLE IF NOT EXISTS calendar_outbox (
        pracownik_id INTEGER NOT NULL REFERENCES pracownicy(id) ON DELETE CASCADE,
        provider TEXT NOT NULL CHECK(provider IN ('google','caldav')),
        calendar_id TEXT NOT NULL,
        remote_id TEXT NOT NULL,
        event_id INTEGER REFERENCES wydarzenia(id) ON DELETE SET NULL,
        revision BIGINT NOT NULL DEFAULT nextval('calendar_outbox_revision'),
        action TEXT NOT NULL CHECK(action IN ('upsert','delete')),
        payload JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(pracownik_id,provider,calendar_id,remote_id))""")
    conn.execute("ALTER TABLE wydarzenia ADD COLUMN IF NOT EXISTS caldav_calendar_id TEXT")
    conn.execute("DROP INDEX IF EXISTS idx_wydarzenia_google")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_wydarzenia_google_scope ON wydarzenia "
                 "(pracownik_id,COALESCE(google_calendar_id,''),google_event_id) WHERE google_event_id IS NOT NULL")
    conn.execute("DROP INDEX IF EXISTS idx_wydarzenia_caldav")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_wydarzenia_caldav_scope ON wydarzenia "
                 "(pracownik_id,COALESCE(caldav_calendar_id,''),caldav_uid) WHERE caldav_uid IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_calendar_outbox_event ON calendar_outbox(event_id)")
    conn.execute("""INSERT INTO app_settings(klucz,wartosc) VALUES ('schema_hash','2026-11-04.2')
                    ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""")
