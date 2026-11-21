"""Administrator-approved links to the configured Płatnik database."""

VERSION = '2026-11-21.6'


def apply(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS platnik_approved_links (
            company_id INTEGER NOT NULL REFERENCES firmy(id) ON DELETE CASCADE,
            payer_nip TEXT NOT NULL CHECK (payer_nip ~ '^[0-9]{10}$'),
            source_key TEXT NOT NULL,
            payer_id BIGINT NOT NULL CHECK (payer_id > 0),
            person_id BIGINT CHECK (person_id > 0),
            revision TEXT NOT NULL,
            approved_by INTEGER REFERENCES pracownicy(id) ON DELETE SET NULL,
            approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (company_id, payer_nip)
        )
    """)
    conn.execute("""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', %s)
                    ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""", (VERSION,))
