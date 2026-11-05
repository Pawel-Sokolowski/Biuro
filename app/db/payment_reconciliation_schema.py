"""Preserve manual payments when attaching or removing an MT940 operation."""


def apply(conn):
    conn.execute('ALTER TABLE faktury_platnosci ADD COLUMN IF NOT EXISTS manual_origin BOOLEAN NOT NULL DEFAULT false')
    conn.execute('ALTER TABLE faktury_platnosci ADD COLUMN IF NOT EXISTS bank_linked_by INTEGER REFERENCES pracownicy(id) ON DELETE SET NULL')
    conn.execute('ALTER TABLE faktury_platnosci ADD COLUMN IF NOT EXISTS bank_linked_at TIMESTAMPTZ')
    conn.execute('ALTER TABLE faktury_platnosci ADD COLUMN IF NOT EXISTS source_correction_id INTEGER REFERENCES faktury(id) ON DELETE RESTRICT')
    conn.execute('ALTER TABLE faktury_platnosci DROP CONSTRAINT IF EXISTS faktury_platnosci_sposob_check')
    conn.execute("ALTER TABLE faktury_platnosci ADD CONSTRAINT faktury_platnosci_sposob_check CHECK (sposob IN ('bank', 'gotowka', 'kompensata'))")
    conn.execute('ALTER TABLE faktury_platnosci DROP CONSTRAINT IF EXISTS faktury_platnosci_correction_check')
    conn.execute("ALTER TABLE faktury_platnosci ADD CONSTRAINT faktury_platnosci_correction_check CHECK "
                 "((sposob = 'kompensata' AND source_correction_id IS NOT NULL AND faktura_id IS NOT NULL "
                 "AND source_correction_id <> faktura_id AND transakcja_id IS NULL) "
                 "OR (sposob <> 'kompensata' AND source_correction_id IS NULL))")
    conn.execute('CREATE INDEX IF NOT EXISTS ix_faktury_platnosci_correction ON faktury_platnosci(source_correction_id)')
    conn.execute("UPDATE faktury_platnosci SET manual_origin = true WHERE sposob = 'bank' AND transakcja_id IS NULL AND NOT manual_origin")
    conn.execute("""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', '2026-11-05.2')
                    ON CONFLICT(klucz) DO UPDATE SET wartosc = EXCLUDED.wartosc""")
