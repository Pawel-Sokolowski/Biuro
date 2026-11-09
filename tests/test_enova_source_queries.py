"""Exercise source SQL logic using PostgreSQL fixtures with explicit dialect translation.

These tests do not establish native SQL Server syntax/driver compatibility;
server-side preview is still required before enabling the scheduled import.
"""
from datetime import date
from pathlib import Path

from app.db.dao_conn import get_conn
from app.integrations.enova.document_counts import calculate_total, validate_rows
from tests.base import DbTestCase

QUERY_DIR = Path(__file__).resolve().parents[1] / 'docs' / 'enova' / 'queries'
NIP = '1234567890'


def postgres_query(name):
    sql = (QUERY_DIR / name).read_text()
    assert sql.count('?') == 2
    sql = sql.replace('WITH Parameters AS', 'WITH RECURSIVE Parameters AS', 1)
    sql = sql.replace('?', '%s').replace('dbo.', '').replace('COUNT_BIG(', 'COUNT(')
    sql = sql.replace("nip NOT LIKE N'%[^0-9]%'", "nip ~ '^[0-9]{10}$'")
    sql = sql.replace("N'", "'").replace('LEN(nip)', 'length(nip)')
    sql = sql.replace('DATEADD(month, 1, m.MonthStart)', "(m.MonthStart + interval '1 month')::date")
    for column in ('d.Data', 'd.DataEwidencji'):
        sql = sql.replace(f'DATEFROMPARTS(YEAR({column}), MONTH({column}), 1)',
                          f'make_date(EXTRACT(YEAR FROM {column})::int, EXTRACT(MONTH FROM {column})::int, 1)')
    sql = sql.replace('CONVERT(char(7), m.MonthStart, 120)', "to_char(m.MonthStart, 'YYYY-MM')")
    return sql.replace('OPTION (MAXRECURSION 24)', '')


class EnovaSourceQueryTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            for statement in (
                'CREATE TABLE OddzialyFirmy(DeklaracjeNIP text)',
                'CREATE TABLE Dziennik(ID int, Data timestamp, KPiRLp int, Bufor int DEFAULT 1)',
                'CREATE TABLE DefDokumentow(ID int, Symbol text)',
                'CREATE TABLE DokEwidencja(ID int, DataEwidencji timestamp, Definicja int, Dokument int, DokumentType text)',
                'CREATE TABLE RaportyESP(ID int PRIMARY KEY)',
                'CREATE TABLE Zaplaty(ID int, Raport int, RaportType text)',
                'CREATE TABLE OperacjeBankowe(ID int, WyciagBankowy int)',
            ):
                conn.execute(statement)
            conn.execute("INSERT INTO OddzialyFirmy VALUES ('PL 123-456-78-90'), ('1234567890')")

    def query(self, name, start=date(2026, 6, 1), end=date(2026, 7, 1)):
        with get_conn() as conn:
            return [dict(row) for row in conn.execute(postgres_query(name), (start, end)).fetchall()]

    def make_kh(self):
        definitions = [('DR', 2), ('LPEW', 5), ('PKE', 11), ('RDEW', 1), ('RKVE', 2),
                       ('RMKE', 1), ('SPU', 27), ('ZR', 43)]
        with get_conn() as conn:
            offset = 0
            for definition, (symbol, count) in enumerate(definitions, 1):
                conn.execute('INSERT INTO DefDokumentow VALUES (%s,%s)', (definition, symbol))
                conn.execute("INSERT INTO DokEwidencja SELECT n,'2026-06-15',%s,NULL,NULL "
                             'FROM generate_series(%s::int,%s::int) n', (definition, offset + 1, offset + count))
                offset += count
            conn.execute("INSERT INTO DefDokumentow VALUES (99,'WBEW')")
            conn.execute("INSERT INTO DokEwidencja SELECT 100+n,'2026-06-30',99,n,'RaportyESP' FROM generate_series(1,3) n")
            conn.execute('INSERT INTO RaportyESP SELECT generate_series(1,3)')
            conn.execute("INSERT INTO Zaplaty SELECT n,1,'RaportyESP' FROM generate_series(1,190) n")
            # Same numeric report ID in a different table must never be counted.
            conn.execute("INSERT INTO Zaplaty SELECT 200+n,1,'OtherReport' FROM generate_series(1,27) n")
            # Imported bank rows are a different representation, not extra positions.
            conn.execute('INSERT INTO OperacjeBankowe SELECT n,1 FROM generate_series(1,90) n')

    def test_kpir_counts_numbered_entries_including_observed_buffer_state_and_empty_month(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO Dziennik(ID,Data,KPiRLp) SELECT n,'2026-06-15',n FROM generate_series(141,195) n")
            conn.execute("INSERT INTO Dziennik VALUES (196,'2026-06-15',0,1),(197,'2026-05-31',196,1)")
        rows = self.query('kpir.sql', end=date(2026, 8, 1))
        self.assertEqual(rows, [{'nip': NIP, 'miesiac': '2026-06', 'dokumenty': 55, 'pozycje_bankowe': 0},
                                {'nip': NIP, 'miesiac': '2026-07', 'dokumenty': 0, 'pozycje_bankowe': 0}])

    def test_kh_keeps_all_nonbank_definitions_and_uses_payment_positions_once(self):
        self.make_kh()
        rows = self.query('kh.sql')
        self.assertEqual(rows, [{'nip': NIP, 'miesiac': '2026-06', 'dokumenty': 92, 'pozycje_bankowe': 190}])
        self.assertEqual(calculate_total('KH', rows[0]['dokumenty'], rows[0]['pozycje_bankowe']), 155)
        with get_conn() as conn:
            conn.execute("INSERT INTO DokEwidencja VALUES (999,'2026-06-30',99,1,'RaportyESP')")
        self.assertEqual(self.query('kh.sql')[0]['pozycje_bankowe'], 190)

    def test_kh_empty_month_is_explicit_zero_and_bad_statement_link_is_rejected(self):
        self.make_kh()
        rows = self.query('kh.sql', end=date(2026, 8, 1))
        self.assertEqual(rows[1]['dokumenty'], 0)
        self.assertEqual(rows[1]['pozycje_bankowe'], 0)
        with get_conn() as conn:
            conn.execute("INSERT INTO DokEwidencja VALUES (999,'2026-06-30',99,500,'RaportyESP')")
        rows = self.query('kh.sql')
        self.assertIsNone(rows[0]['pozycje_bankowe'])
        with self.assertRaises(ValueError):
            validate_rows(rows, {'nip': NIP, 'accounting_type': 'KH'}, ['2026-06'])

    def test_missing_or_ambiguous_source_nip_cannot_become_a_zero_import(self):
        with get_conn() as conn:
            conn.execute('DELETE FROM OddzialyFirmy')
        self.assertEqual(self.query('kpir.sql'), [])
        with get_conn() as conn:
            conn.execute("INSERT INTO OddzialyFirmy VALUES ('1234567890'), ('9999999999')")
        rows = self.query('kpir.sql')
        with self.assertRaises(ValueError):
            validate_rows(rows, {'nip': NIP, 'accounting_type': 'KPIR'}, ['2026-06'])
