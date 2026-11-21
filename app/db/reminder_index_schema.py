"""Indexes for recipient and invoice eligibility checks before reminder delivery."""

VERSION = '2026-11-21.4'


def apply(conn):
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_company_contacts_firma_id
                    ON company_contacts(firma_id, id)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_udzialowcy_firma_id
                    ON udzialowcy(firma_id, id)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_faktury_kosz_active_invoice
                    ON faktury_kosz(faktura_id) WHERE restored_at IS NULL""")
    conn.execute("""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', %s)
                    ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""", (VERSION,))
