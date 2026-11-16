"""Remove the obsolete flat-fee label; prices and document limits stay separate."""
def apply(conn):
    conn.execute('ALTER TABLE firmy DROP COLUMN IF EXISTS typ_rozliczenia')
    conn.execute("""INSERT INTO app_settings(klucz,wartosc) VALUES ('schema_hash','2026-11-16.1')
        ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""")
