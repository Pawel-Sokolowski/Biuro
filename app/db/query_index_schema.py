"""Indexes for bank reconciliation, invoice periods and Enova company lookup."""

VERSION = '2026-11-21.3'
# Match Python's whitespace handling for legacy statement accounts across PostgreSQL locales.
BANK_ACCOUNT_SEPARATORS = (
    r"U&'-\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680"
    r"\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000'"
)
NORMALIZED_BANK_ACCOUNT = (
    f"regexp_replace(ltrim(translate(upper(rachunek), {BANK_ACCOUNT_SEPARATORS}, ''), '/'), '^PL', '')"
)
NORMALIZED_COMPANY_NIP = "regexp_replace(upper(nip), '[[:space:]-]', '', 'g')"
BANK_RECEIPTS = "kwota > 0 AND waluta = 'PLN' AND COALESCE(NULLIF(kierunek, ''), 'C') = 'C'"


def apply(conn):
    conn.execute(f"""CREATE INDEX IF NOT EXISTS idx_bank_transakcje_account
                     ON bank_transakcje(({NORMALIZED_BANK_ACCOUNT}))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_bank_transakcje_order
                    ON bank_transakcje(data_operacji DESC, id DESC)""")
    conn.execute(f"""CREATE INDEX IF NOT EXISTS idx_bank_transakcje_receipts
                     ON bank_transakcje(data_operacji DESC, id DESC)
                     WHERE {BANK_RECEIPTS}""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_faktury_miesiac_id ON faktury(miesiac, id)""")
    conn.execute(f"""CREATE INDEX IF NOT EXISTS idx_firmy_normalized_nip
                     ON firmy(({NORMALIZED_COMPANY_NIP}))""")
    conn.execute(f"""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', '{VERSION}')
                     ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""")
