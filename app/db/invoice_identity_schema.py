"""Immutable seller details for invoices issued after this upgrade."""


def apply(conn):
    conn.execute('ALTER TABLE faktury ADD COLUMN IF NOT EXISTS seller_snapshot JSONB')
    conn.execute("""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', '2026-11-08.2')
                    ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""")
