"""Upgrade installed databases for invoice descriptions."""

VERSION = '2026-11-27.2'


def apply(conn):
    conn.execute("ALTER TABLE faktury ADD COLUMN IF NOT EXISTS opis_dodatkowy TEXT")
    conn.execute("""
        INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', %s)
        ON CONFLICT (klucz) DO UPDATE SET wartosc = EXCLUDED.wartosc
    """, (VERSION,))
