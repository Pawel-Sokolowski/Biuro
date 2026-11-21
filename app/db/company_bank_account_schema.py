"""Index the normalized company accounts used during bank reconciliation."""
from app.db.query_index_schema import BANK_ACCOUNT_SEPARATORS

VERSION = '2026-11-21.7'
NORMALIZED_COMPANY_BANK_ACCOUNT = (
    f"regexp_replace(ltrim(translate(upper(konto_bankowe), {BANK_ACCOUNT_SEPARATORS}, ''), '/'), '^PL', '')"
)


def apply(conn):
    conn.execute(f"""CREATE INDEX IF NOT EXISTS idx_firmy_bank_account
                    ON firmy(({NORMALIZED_COMPANY_BANK_ACCOUNT}))""")
    conn.execute("""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', %s)
                    ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""", (VERSION,))
