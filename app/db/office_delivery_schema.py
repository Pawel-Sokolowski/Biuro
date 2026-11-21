"""Versioned tables and scheduling fields used by the office delivery workflows."""

VERSION = '2026-11-21.1'


def apply(conn):
    from app.db.service_tables_schema import apply_bulk_mail

    apply_bulk_mail(conn)
    conn.execute("ALTER TABLE bulk_mail_runs ADD COLUMN IF NOT EXISTS plan_json TEXT")
    conn.execute("ALTER TABLE bulk_mail_batches ADD COLUMN IF NOT EXISTS batch_no INTEGER")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_bulk_mail_batch_no ON bulk_mail_batches(run_id, batch_no)")
    conn.execute("ALTER TABLE faktury ADD COLUMN IF NOT EXISTS invoice_email_attempts INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE faktury ADD COLUMN IF NOT EXISTS invoice_email_next_attempt_at TIMESTAMPTZ")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_invoice_email_due ON faktury(invoice_email_next_attempt_at NULLS FIRST, id)
                    WHERE invoice_email_status IN ('queued', 'failed_before_send')""")
    conn.execute(f"""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', '{VERSION}')
                    ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""")
