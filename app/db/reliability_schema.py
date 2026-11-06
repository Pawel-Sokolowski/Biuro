"""Draft revisions, crash-recoverable timers, and indexable search support."""

SCHEMA_VERSION = '2026-11-06.1'


def apply(conn):
    conn.execute('ALTER TABLE faktury ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 1')
    conn.execute('''CREATE OR REPLACE FUNCTION bump_invoice_revision() RETURNS trigger AS $$
        BEGIN NEW.revision := OLD.revision + 1; RETURN NEW; END;
        $$ LANGUAGE plpgsql''')
    conn.execute('DROP TRIGGER IF EXISTS invoice_revision ON faktury')
    conn.execute('CREATE TRIGGER invoice_revision BEFORE UPDATE ON faktury FOR EACH ROW EXECUTE FUNCTION bump_invoice_revision()')
    conn.execute('''CREATE TABLE IF NOT EXISTS active_timer_sessions (
        session_id UUID PRIMARY KEY, pracownik_id INTEGER NOT NULL REFERENCES pracownicy(id) ON DELETE CASCADE,
        firma_id INTEGER REFERENCES firmy(id) ON DELETE CASCADE, miesiac TEXT NOT NULL,
        typ TEXT NOT NULL, activity JSONB NOT NULL DEFAULT '{}'::jsonb,
        started_at TIMESTAMPTZ NOT NULL, checkpoint_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        elapsed_seconds INTEGER NOT NULL DEFAULT 0 CHECK(elapsed_seconds >= 0),
        owner_token UUID NOT NULL, recovered_at TIMESTAMPTZ)''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_active_timer_employee ON active_timer_sessions(pracownik_id)')
    conn.execute("""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', '2026-11-06.1')
                    ON CONFLICT(klucz) DO UPDATE SET wartosc = EXCLUDED.wartosc""")
