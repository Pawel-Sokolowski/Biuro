"""Durable staging for sent mail that still needs to reach IMAP."""

VERSION = '2026-11-27.1'


def apply(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_sent_copies (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            konto_id INTEGER NOT NULL REFERENCES konta_email(id) ON DELETE CASCADE,
            account_email TEXT NOT NULL,
            message_id TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            payload BYTEA NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('reserved', 'pending', 'appended')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            retry_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_email_sent_copies_due
        ON email_sent_copies (konto_id, retry_at, id) WHERE state IN ('pending', 'appended')
    """)
    conn.execute("""
        INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', %s)
        ON CONFLICT (klucz) DO UPDATE SET wartosc = EXCLUDED.wartosc
    """, (VERSION,))
