"""Execute real shipped T-SQL on SQL Server, using entirely synthetic records."""
from datetime import date, datetime
import os
from pathlib import Path
import unittest
import uuid

DSN = os.environ.get('BIURO_SQLSERVER_TEST_DSN')
QUERIES = Path(__file__).resolve().parents[2] / 'docs/enova/queries'


def receipt(reference='REF-CI'):
    # Real-world XML declarations declare UTF-8 even though Enova stores Unicode ntext.
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<Potwierdzenie xmlns="urn:synthetic-mf"><NumerReferencyjny>' + reference +
            '</NumerReferencyjny><DataWytworzeniaPotwierdzenia>2026-11-05T12:30:00+01:00'
            '</DataWytworzeniaPotwierdzenia></Potwierdzenie>')


@unittest.skipUnless(DSN, 'Requires the isolated GitHub SQL Server service')
class ShippedEnovaSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pyodbc
        pyodbc.pooling = False
        cls.database = 'biuro_ci_' + uuid.uuid4().hex
        cls.master = pyodbc.connect(DSN, autocommit=True, timeout=15)
        cls.master.execute(f'CREATE DATABASE [{cls.database}]')
        cls.writer = pyodbc.connect(DSN + ';DATABASE=' + cls.database, autocommit=True, timeout=15)
        cls.writer.execute('''
            CREATE TABLE dbo.OddzialyFirmy (DeklaracjeNIP nvarchar(64));
            CREATE TABLE dbo.DefDokumentow (ID int PRIMARY KEY, Symbol nvarchar(64));
            CREATE TABLE dbo.Deklaracje (ID int PRIMARY KEY, OkresTo datetime, NumerNumer int,
                Bufor bit, Definicja int, NumerSymbol nvarchar(64), Deklaracja int);
            CREATE TABLE dbo.EDeklaracje (ID int PRIMARY KEY, Deklaracja int, StatusEDeklaracji int,
                Dokument ntext, Potwierdzenie ntext);
            CREATE TABLE dbo.ETransmisje (ID int PRIMARY KEY, EDeklaracja int, Typ int,
                Czas datetime, StatusETransmisji int);
            CREATE TABLE dbo.JednolitePK (ID int PRIMARY KEY, OkresTo datetime, NumerKorekty int,
                StatusJPK int, BramkaJPK int, UPO ntext, FormCode nvarchar(24));
            CREATE TABLE dbo.DefListPlac (ID int PRIMARY KEY, Symbol nvarchar(64));
            CREATE TABLE dbo.ListyPlac (ID int PRIMARY KEY, Data datetime, Definicja int);
            CREATE TABLE dbo.Wyplaty (ID int PRIMARY KEY, Data datetime, Definicja int, Bufor bit);
            CREATE USER import_reader WITHOUT LOGIN;
            GRANT SELECT ON SCHEMA::dbo TO import_reader;
        ''')
        cls.reader = pyodbc.connect(DSN + ';DATABASE=' + cls.database, autocommit=True, timeout=15)
        cls.reader.execute("EXECUTE AS USER = 'import_reader'")

    @classmethod
    def tearDownClass(cls):
        cls.reader.close()
        cls.writer.close()
        cls.master.execute(f'DROP DATABASE [{cls.database}]')
        cls.master.close()

    def setUp(self):
        for table in ('OddzialyFirmy', 'DefDokumentow', 'Deklaracje', 'EDeklaracje', 'ETransmisje',
                      'JednolitePK', 'DefListPlac', 'ListyPlac', 'Wyplaty'):
            self.writer.execute('DELETE FROM dbo.' + table)
        self.writer.execute("INSERT INTO dbo.OddzialyFirmy VALUES (N'PL 111-222-33-44')")
        self.writer.execute("INSERT INTO dbo.DefDokumentow VALUES (1, N'custom')")

    def query(self, name, start=date(2026, 11, 1), end=date(2026, 12, 1)):
        cursor = self.reader.cursor()
        cursor.execute((QUERIES / name).read_text(encoding='utf-8-sig'), start, end)
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def declaration(self, identity=1, status=4, confirmation=None, form='VAT-UE', number=1):
        self.writer.execute("INSERT INTO dbo.Deklaracje VALUES (?, '2026-11-30', ?, 0, 1, 'custom', 0)", identity, number)
        xml = '<?xml version="1.0" encoding="utf-8"?><Deklaracja xmlns="urn:synthetic-mf"><KodFormularza>' + form + '</KodFormularza></Deklaracja>'
        self.writer.execute('INSERT INTO dbo.EDeklaracje VALUES (?, ?, ?, ?, ?)', identity, identity, status, xml, confirmation)

    def jpk(self, identity=1, status=100, gateway=3, correction=0, confirmation=None, form='JPK_V7M'):
        self.writer.execute("INSERT INTO dbo.JednolitePK VALUES (?, '2026-11-30', ?, ?, ?, ?, ?)",
                            identity, correction, status, gateway, confirmation, form)

    def test_namespaced_unicode_xml_receipt_and_date_are_read_without_exposing_xml(self):
        self.declaration(confirmation=receipt())
        self.writer.execute("INSERT INTO dbo.ETransmisje VALUES (1, 1, 2, '2026-11-05', 200)")
        row, = self.query('declarations.sql')
        self.assertEqual((row['nip'], row['form'], row['status'], row['upo']), ('1112223344', 'VAT-UE', 'accepted', 'REF-CI'))
        self.assertEqual(datetime.fromisoformat(row['upo_at']), datetime.fromisoformat('2026-11-05T12:30:00+01:00'))
        self.assertIsNone(row['sent_at'])
        self.assertNotIn('<Potwierdzenie', str(row))

    def test_latest_pending_correction_does_not_inherit_an_older_upo(self):
        self.declaration(identity=1, confirmation=receipt(), number=1)
        self.declaration(identity=2, status=2, number=2, form='VAT-UEK')
        row, = self.query('declarations.sql')
        self.assertEqual((row['form'], row['status'], row['upo']), ('VAT-UE', 'not_sent', ''))

    def test_cancelled_declaration_never_becomes_accepted_from_old_confirmation(self):
        self.declaration(status=100, confirmation=receipt())
        self.writer.execute("INSERT INTO dbo.ETransmisje VALUES (1, 1, 2, '2026-11-05', 200)")
        self.assertEqual(self.query('declarations.sql')[0]['status'], 'cancelled')

    def test_transmission_types_are_not_confused_with_confirmation_responses(self):
        self.declaration(status=3)
        self.writer.execute("INSERT INTO dbo.ETransmisje VALUES (1, 1, 1, '2026-11-05', 400)")
        self.assertEqual(self.query('declarations.sql')[0]['status'], 'sent')
        self.writer.execute("INSERT INTO dbo.ETransmisje VALUES (2, 1, 2, '2026-11-06', 400)")
        self.assertEqual(self.query('declarations.sql')[0]['status'], 'rejected')

    def test_electronic_declaration_codes_and_old_receipts_cannot_fabricate_acceptance(self):
        for status, expected in ((0, 'unknown'), (1, 'prepared'), (2, 'not_sent'),
                                 (3, 'sent'), (4, 'unknown'), (99, 'failed'),
                                 (100, 'cancelled'), (200, 'unknown'), (999, 'unknown')):
            with self.subTest(status=status):
                self.writer.execute('DELETE FROM dbo.EDeklaracje')
                self.writer.execute('DELETE FROM dbo.Deklaracje')
                self.declaration(status=status)
                self.assertEqual(self.query('declarations.sql')[0]['status'], expected)
        self.writer.execute('DELETE FROM dbo.EDeklaracje')
        self.writer.execute('DELETE FROM dbo.Deklaracje')
        self.declaration(status=99, confirmation=receipt())
        self.writer.execute("INSERT INTO dbo.ETransmisje VALUES (1, 1, 2, '2026-11-05', 200)")
        self.assertEqual(self.query('declarations.sql')[0]['status'], 'failed')

    def test_jpk_prepared_processing_and_failed_states_require_no_guessing(self):
        for status, expected in ((10, 'prepared'), (15, 'prepared'), (20, 'not_sent'),
                                 (30, 'processing'), (40, 'processing'), (100, 'unknown'),
                                 (200, 'failed'), (999, 'unknown')):
            with self.subTest(status=status):
                self.writer.execute('DELETE FROM dbo.JednolitePK')
                self.jpk(status=status)
                self.assertEqual(self.query('jpk.sql')[0]['status'], expected)

    def test_test_gateway_upo_is_not_a_production_filing_and_does_not_mask_production(self):
        self.jpk(gateway=2, confirmation=receipt('TEST-REF'), correction=3)
        row, = self.query('jpk.sql')
        self.assertEqual((row['status'], row['upo']), ('unknown', ''))
        self.assertIn('testowa', row['raw_status'])
        self.jpk(identity=2, gateway=3, confirmation=receipt('PRODUCTION-REF'))
        self.assertEqual(self.query('jpk.sql')[0]['upo'], 'PRODUCTION-REF')

    def test_jpk_latest_correction_and_complete_empty_months(self):
        self.jpk(confirmation=receipt('OLD-REF'))
        self.jpk(identity=2, status=20, correction=1)
        self.jpk(identity=3, status=100, correction=9, form='JPK_KR', confirmation=receipt('OTHER-TYPE'))
        rows = self.query('jpk.sql', date(2026, 10, 1))
        self.assertEqual([(row['miesiac'], row['status'], row['upo']) for row in rows],
                         [('2026-10', 'unknown', ''), ('2026-11', 'not_sent', '')])

    def test_malformed_upo_never_means_accepted(self):
        self.jpk(confirmation='<broken')
        row, = self.query('jpk.sql')
        self.assertEqual((row['status'], row['upo']), ('unknown', ''))

    def test_payroll_documents_buffers_and_zero_months(self):
        self.writer.execute("INSERT INTO dbo.DefListPlac VALUES (1, 'LP')")
        self.writer.execute("INSERT INTO dbo.ListyPlac VALUES (1, '2026-11-05', 1)")
        self.writer.execute("INSERT INTO dbo.Wyplaty VALUES (1, '2026-11-05', 1, 1), (2, '2026-11-05', 1, 0)")
        rows = self.query('payroll.sql', date(2026, 10, 1))
        self.assertEqual((rows[0]['miesiac'], rows[0]['liczba']), ('2026-10', 0))
        self.assertEqual(sum(row['liczba'] for row in rows[1:]), 3)
        self.assertTrue(any('bufor' in (row['rodzaj'] or '') for row in rows))

    def test_reader_cannot_write(self):
        import pyodbc
        with self.assertRaises(pyodbc.Error):
            self.reader.execute('DELETE FROM dbo.JednolitePK')
