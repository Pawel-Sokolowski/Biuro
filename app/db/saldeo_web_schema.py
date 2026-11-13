def apply(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS saldeo_web_events (
        event_key TEXT PRIMARY KEY, body TEXT NOT NULL, source_time TEXT,
        detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        company_name TEXT, firma_id INTEGER REFERENCES firmy(id) ON DELETE SET NULL,
        miesiac TEXT, state TEXT NOT NULL DEFAULT 'pending', applied_at TIMESTAMPTZ,
        attempts INTEGER NOT NULL DEFAULT 0
    )''')
    conn.execute("CREATE INDEX IF NOT EXISTS saldeo_web_events_pending ON saldeo_web_events(state,detected_at)")
    conn.execute("""INSERT INTO app_settings(klucz,wartosc) VALUES ('schema_hash','2026-11-13.1')
        ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""")
