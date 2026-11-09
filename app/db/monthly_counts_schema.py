"""Mark direct XLSX counts as authoritative, including explicit zero."""


def apply(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS monthly_document_imports (
        firma_id INTEGER NOT NULL REFERENCES firmy(id) ON DELETE CASCADE,
        miesiac TEXT NOT NULL,
        source_name TEXT NOT NULL,
        imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        imported_by INTEGER REFERENCES pracownicy(id) ON DELETE SET NULL,
        PRIMARY KEY(firma_id,miesiac)
    )''')
    conn.execute("""INSERT INTO app_settings(klucz,wartosc) VALUES ('schema_hash','2026-11-09.2')
        ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""")
