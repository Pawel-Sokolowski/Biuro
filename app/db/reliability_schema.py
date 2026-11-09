"""Draft revisions, crash-recoverable timers, and indexable search support."""

SCHEMA_VERSION = '2026-11-06.1'


def apply(conn):
    for table, fields in (('faktury', ('kwota_netto', 'kwota_brutto')),
                          ('import_faktury_pozycje', ('wartosc_netto',))):
        for field in fields:
            conn.execute(f'ALTER TABLE {table} ALTER COLUMN {field} TYPE NUMERIC(18,2) USING round({field}::numeric,2)')
            constraint = f'ck_{table}_{field}_finite'
            conn.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}')
            conn.execute(f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK ({field} <> 'NaN'::numeric)")
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
    conn.execute('''CREATE TABLE IF NOT EXISTS account_sync_state (
        konto_id INTEGER PRIMARY KEY REFERENCES konta_email(id) ON DELETE CASCADE,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    # pg_trgm is optional on databases where the application role cannot create
    # extensions. Searches retain their exact semantics without the extension.
    conn.execute('''DO $$ BEGIN
        CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE NOTICE 'pg_trgm unavailable: ask the database administrator to enable it';
        END $$''')
    for column in ('temat', 'nadawca', 'odbiorca'):
        conn.execute(f'''DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_opclass oc JOIN pg_namespace ns ON ns.oid=oc.opcnamespace
                       WHERE oc.opcname='gin_trgm_ops' AND ns.nspname='public') THEN
                CREATE INDEX IF NOT EXISTS ix_mail_search_{column}
                ON wiadomosci_email USING gin ((COALESCE({column}, '')) public.gin_trgm_ops)
                WHERE COALESCE(usunieta, 0)=0;
            END IF;
        END $$''')
    conn.execute("""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', '2026-11-06.1')
                    ON CONFLICT(klucz) DO UPDATE SET wartosc = EXCLUDED.wartosc""")
