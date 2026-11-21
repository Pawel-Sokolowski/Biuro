"""Invoice reference lookups and persistent retry times for Enova stage reads."""

VERSION = '2026-11-21.5'
INVOICE_REFERENCE = "regexp_replace(upper(numer), '[^A-Z0-9]', '', 'g')"
BANK_INVOICE_ELIGIBLE = "rodzaj IN ('faktura', 'zaliczkowa', 'proforma', 'korekta') AND COALESCE(eksportowana, 0) = 1 AND cancelled_at IS NULL"


def apply(conn):
    conn.execute(f"""CREATE INDEX IF NOT EXISTS idx_faktury_bank_reference
                     ON faktury(({INVOICE_REFERENCE})) WHERE {BANK_INVOICE_ELIGIBLE}""")
    conn.execute("""ALTER TABLE enova_stage_requests ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS last_error TEXT""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_enova_stage_requests_retry
                    ON enova_stage_requests(next_attempt_at)""")
    conn.execute("""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', %s)
                    ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""", (VERSION,))
