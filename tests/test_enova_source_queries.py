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
    sql = sql.replace("'{' + STRING_AGG(CAST('\"' + STRING_ESCAPE(t.symbol, 'json') + '\":' + CAST(t.cnt AS varchar(20)) AS nvarchar(max)), ',') + '}'",
                      'json_object_agg(t.symbol,t.cnt)::text')
    sql = sql.replace('DATEADD(month,1,m.MonthStart)', "(m.MonthStart + interval '1 month')::date")
    sql = sql.replace('?', '%s').replace('dbo.', '').replace('COUNT_BIG(', 'COUNT(')
    sql = sql.replace("nip NOT LIKE N'%[^0-9]%'", "nip ~ '^[0-9]{10}$'")
    sql = sql.replace("N'", "'").replace('LEN(nip)', 'length(nip)')
    sql = sql.replace('DATEADD(month, 1, m.MonthStart)', "(m.MonthStart + interval '1 month')::date")
    for column in ('d.Data', 'd.DataEwidencji'):
        sql = sql.replace(f'DATEFROMPARTS(YEAR({column}), MONTH({column}), 1)',
                          f'make_date(EXTRACT(YEAR FROM {column})::int, EXTRACT(MONTH FROM {column})::int, 1)')
    sql = sql.replace('CONVERT(char(7), m.MonthStart, 120)', "to_char(m.MonthStart, 'YYYY-MM')")
    return sql.replace('OPTION (MAXRECURSION 24)', '').replace('OPTION (MAXRECURSION 36)', '')


class EnovaSourceQueryTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            for statement in (
                'CREATE TABLE OddzialyFirmy(DeklaracjeNIP text)',
                'CREATE TABLE Dziennik(ID int, Data timestamp, KPiRLp int, Bufor int DEFAULT 1, Ewidencja int)',
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
            rows = [dict(row) for row in conn.execute(postgres_query(name), (start, end)).fetchall()]
            for row in rows:
                import json
                kinds = json.loads(row.pop('rodzaje'))
                if row['dokumenty'] is not None:
                    self.assertEqual(sum(kinds.values()), row['dokumenty'])
            return rows

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
            conn.execute("INSERT INTO Dziennik(ID,Data,KPiRLp,Bufor) VALUES (196,'2026-06-15',0,1),(197,'2026-05-31',196,1)")
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

    def test_ryczalt_counts_purchases_sales_and_cash_method_invoices_only(self):
        definitions = [('DR', 3), ('LPEW', 1), ('RKVE', 1), ('SPU', 2), ('STE', 1), ('ZR', 18)]
        with get_conn() as conn:
            offset = 0
            for definition, (symbol, count) in enumerate(definitions, 1):
                conn.execute('INSERT INTO DefDokumentow VALUES (%s,%s)', (definition, symbol))
                conn.execute("INSERT INTO DokEwidencja SELECT n,'2026-06-15',%s,NULL,NULL "
                             'FROM generate_series(%s::int,%s::int) n', (definition, offset + 1, offset + count))
                offset += count
            conn.execute("INSERT INTO DokEwidencja VALUES (999,'2026-05-31',6,NULL,NULL)")
        rows = self.query('ryczalt.sql', end=date(2026, 8, 1))
        self.assertEqual(rows, [{'nip': NIP, 'miesiac': '2026-06', 'dokumenty': 21, 'pozycje_bankowe': 0},
                                {'nip': NIP, 'miesiac': '2026-07', 'dokumenty': 0, 'pozycje_bankowe': 0}])
        self.assertEqual(validate_rows(rows, {'nip': NIP, 'accounting_type': 'RYCZALT'},
                                       ['2026-06', '2026-07'])[0].total, 21)

    def test_checking_includes_all_definitions_without_pricing_exclusions(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO DefDokumentow VALUES (1,'SPU'),(2,'ZR'),(3,'DW'),(4,'AMO'),(5,'WBEW')")
            conn.execute("INSERT INTO DokEwidencja VALUES (1,'2026-06-15',1,NULL,NULL),(2,'2026-06-15',2,NULL,NULL),(3,'2026-06-15',3,NULL,NULL),(4,'2026-06-15',4,NULL,NULL),(5,'2026-06-15',5,NULL,NULL)")
        rows = self.query('checking.sql')
        self.assertEqual(rows[0]['dokumenty'], 5)
        self.assertEqual(rows[0]['pozycje_bankowe'], 0)
