"""Execute real shipped T-SQL on SQL Server, using entirely synthetic records."""
from datetime import date, datetime
import os
from pathlib import Path
import unittest
import uuid
import json

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
            CREATE TABLE dbo.HstDanychFirmy (Termin datetime, NIP nvarchar(64));
            CREATE TABLE dbo.DokEwidencja (ID int PRIMARY KEY, Definicja int, DataEwidencji datetime,
                Dokument int, DokumentType nvarchar(64));
            CREATE TABLE dbo.Dziennik (ID int PRIMARY KEY, Data datetime, KPiRLp int, Ewidencja int);
            CREATE TABLE dbo.DefDokumentow (ID int PRIMARY KEY, Symbol nvarchar(64), Typ int);
            CREATE TABLE dbo.DokumentyST (ID int PRIMARY KEY, Typ int);
            CREATE TABLE dbo.RaportyESP (ID int PRIMARY KEY);
            CREATE TABLE dbo.Zaplaty (ID int PRIMARY KEY, Raport int, RaportType nvarchar(64));
            CREATE TABLE dbo.Deklaracje (ID int PRIMARY KEY, OkresTo datetime, NumerNumer int,
                Bufor bit, Definicja int, NumerSymbol nvarchar(64), Deklaracja int);
            CREATE TABLE dbo.EDeklaracje (ID int PRIMARY KEY, Deklaracja int, StatusEDeklaracji int,
                Dokument ntext, Potwierdzenie ntext);
            CREATE TABLE dbo.ETransmisje (ID int PRIMARY KEY, EDeklaracja int, Typ int,
                Czas datetime, StatusETransmisji int);
            CREATE TABLE dbo.JednolitePK (ID int PRIMARY KEY, OkresTo datetime, NumerKorekty int,
                StatusJPK int, BramkaJPK int, UPO ntext, FormCode nvarchar(24), ZakresDanych int);
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
        self.reader.execute("EXEC sys.sp_set_session_context @key=N'biuro_company_nip', @value=NULL")
        for table in ('OddzialyFirmy', 'HstDanychFirmy', 'DefDokumentow', 'Deklaracje', 'EDeklaracje', 'ETransmisje',
                      'JednolitePK', 'DefListPlac', 'ListyPlac', 'Wyplaty', 'Dziennik', 'DokEwidencja',
                      'DokumentyST', 'RaportyESP', 'Zaplaty'):
            self.writer.execute('DELETE FROM dbo.' + table)
        self.writer.execute("INSERT INTO dbo.OddzialyFirmy VALUES (N'999-888-77-66')")
        self.writer.execute("INSERT INTO dbo.HstDanychFirmy VALUES ('2000-01-01',N'PL 111-222-33-44')")
        self.writer.execute("INSERT INTO dbo.DefDokumentow (ID,Symbol) VALUES (1, N'custom')")

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
        self.writer.execute("INSERT INTO dbo.JednolitePK (ID,OkresTo,NumerKorekty,StatusJPK,BramkaJPK,UPO,FormCode) "
                            "VALUES (?, '2026-11-30', ?, ?, ?, ?, ?)",
                            identity, correction, status, gateway, confirmation, form)

    def monthly_documents(self, ledger, vat=None, **kwargs):
        self.reader.execute("EXEC sys.sp_set_session_context @key=N'biuro_accounting_type', @value=?", ledger)
        self.reader.execute("EXEC sys.sp_set_session_context @key=N'biuro_vat_czynny', @value=?", vat)
        return self.query('monthly-documents.sql', **kwargs)

    def test_kh_weights_positions_excludes_ot_lt_and_counts_cit_and_vat_once(self):
        self.writer.execute('''
            INSERT INTO dbo.DefDokumentow VALUES (2,N'WBEW',0),(3,N'RKEW',0),(4,N'RKVE',1203),(5,N'STE',1306),
                (6,N'CIT-8',1023),(7,N'VAT7',207),(8,N'SPR',1201),(9,N'CIT-2',1012);
            INSERT INTO dbo.DokumentyST VALUES (101,1301),(102,1302),(103,1303);
            INSERT INTO dbo.RaportyESP VALUES (1),(2);
            INSERT INTO dbo.Zaplaty VALUES (1,1,N'RaportyESP'),(2,1,N'RaportyESP'),(3,2,N'RaportyESP'),(4,1,N'NiewlasciwaTabela');
            INSERT INTO dbo.DokEwidencja VALUES
                (2,2,'2026-11-10',1,N'RaportyESP'),(3,3,'2026-11-10',2,N'RaportyESP'),
                (4,4,'2026-11-10',2,N'RaportyESP'),(5,4,'2026-11-10',NULL,NULL),
                (6,5,'2026-11-10',101,N'DokumentyST'),(7,5,'2026-11-10',102,N'DokumentyST'),
                (8,5,'2026-11-10',103,N'DokumentyST'),(9,8,'2026-11-10',NULL,NULL),
                (10,7,'2026-11-10',101,N'Deklaracje');
            INSERT INTO dbo.Deklaracje VALUES
                (101,'2026-11-30',1,0,7,N'VAT7',0),(102,'2026-11-30',2,0,7,N'VAT7',0),
                (103,'2026-11-30',1,0,6,N'CIT-8',0),(104,'2026-11-30',1,1,9,N'CIT-2',0);
            INSERT INTO dbo.JednolitePK VALUES (1,'2026-11-30',0,100,3,NULL,N'JPK_VAT',1);
        ''')
        counts, = self.monthly_documents('KH')
        self.assertEqual(json.loads(counts['rodzaje']), {'AMO': 1, 'SPR': 1, 'VAT': 1, 'CIT-8': 1})
        self.assertEqual((counts['dokumenty'], counts['pozycje_bankowe'], counts['pozycje_kasowe']), (4, 2, 2))
        self.writer.execute('UPDATE dbo.DokEwidencja SET Dokument=999 WHERE ID=3')
        self.assertIsNone(self.monthly_documents('KH')[0]['pozycje_kasowe'])

    def test_kpir_counts_numbered_entries_and_dra_but_not_pit5_or_vat(self):
        self.writer.execute('''
            INSERT INTO dbo.DefDokumentow VALUES (2,N'SPR',1201),(3,N'ZP',1202),(4,N'DWNNC',1206),
                (5,N'DRA',1004),(6,N'PIT-5',1014),(7,N'VAT7',207);
            INSERT INTO dbo.DokEwidencja VALUES (2,2,'2026-10-01',NULL,NULL),(3,3,'2026-11-05',NULL,NULL),
                (4,4,'2026-11-05',NULL,NULL),(5,5,'2026-11-05',101,N'Deklaracje'),
                (6,6,'2026-11-05',102,N'Deklaracje'),(7,7,'2026-11-05',103,N'Deklaracje');
            INSERT INTO dbo.Dziennik VALUES (2,'2026-11-05',1,2),(3,'2026-11-05',2,3),(4,'2026-11-05',3,4),
                (5,'2026-11-05',4,5),(6,'2026-11-05',5,6),(7,'2026-11-05',6,7),(8,'2026-11-05',0,NULL);
            INSERT INTO dbo.Deklaracje VALUES (101,'2026-11-30',1,0,5,N'DRA',0),
                (102,'2026-11-30',1,0,6,N'PIT-5',0),(103,'2026-11-30',1,0,7,N'VAT7',0);
        ''')
        counts, empty = self.monthly_documents('KPIR', end=date(2027, 1, 1))
        self.assertEqual(json.loads(counts['rodzaje']), {'SPR': 1, 'ZP': 1, 'DWNNC': 1, 'DRA': 1})
        self.assertEqual((counts['dokumenty'], counts['sprzedaz'], counts['zakup']), (4, 1, 1))
        self.assertEqual(empty['dokumenty'], 0)

    def test_ryczalt_counts_sales_and_adds_purchases_only_with_vat(self):
        self.writer.execute('''
            INSERT INTO dbo.DefDokumentow VALUES (2,N'SPR',NULL),(3,N'ZP',NULL),(4,N'DWNNC',1202),
                (5,N'InnaSprzedaż',1201),(6,N'RKVE',1203);
            INSERT INTO dbo.DokEwidencja VALUES (2,2,'2026-11-05',NULL,NULL),(3,3,'2026-11-05',NULL,NULL),
                (4,4,'2026-11-05',NULL,NULL),(5,5,'2026-11-05',NULL,NULL),(6,6,'2026-11-05',NULL,NULL);
        ''')
        for vat, expected in ((False, {'SPR': 1, 'InnaSprzedaż': 1}), (True, {'SPR': 1, 'InnaSprzedaż': 1, 'ZP': 1})):
            with self.subTest(vat=vat):
                counts, = self.monthly_documents('RYCZALT', vat)
                self.assertEqual(json.loads(counts['rodzaje']), expected)
                self.assertEqual((counts['sprzedaz'], counts['zakup'], counts['dokumenty']), (2, int(vat), 2 + int(vat)))
        self.assertEqual(self.monthly_documents('RYCZALT'), [])

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
        self.assertEqual((row['sent_at'], row['upo_at']), (None, None))
        self.assertIn('testowa', row['raw_status'])
        self.jpk(identity=2, gateway=3, confirmation=receipt('PRODUCTION-REF'))
        self.assertEqual(self.query('jpk.sql')[0]['upo'], 'PRODUCTION-REF')

    def test_jpk_reads_receipt_dates_from_the_signed_upo(self):
        confirmation = ('<?xml version="1.0" encoding="UTF-8"?>'
                        '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#" '
                        'xmlns:xades="http://uri.etsi.org/01903/v1.3.2#">'
                        '<ds:Object><xades:QualifyingProperties><xades:SignedProperties>'
                        '<xades:SignedSignatureProperties><xades:SigningTime>2026-11-05T00:15:00+01:00'
                        '</xades:SigningTime></xades:SignedSignatureProperties></xades:SignedProperties>'
                        '</xades:QualifyingProperties></ds:Object>'
                        '<ds:Object><Potwierdzenie><NumerReferencyjny>REF-CI</NumerReferencyjny>'
                        '<DataWplyniecia>2026-11-04T23:55:00.1234567+01:00</DataWplyniecia>'
                        '</Potwierdzenie></ds:Object></ds:Signature>')
        self.jpk(confirmation=confirmation)
        row, = self.query('jpk.sql')
        self.assertEqual((row['status'], row['upo']), ('accepted', 'REF-CI'))
        self.assertEqual(datetime.fromisoformat(row['sent_at']), datetime.fromisoformat('2026-11-04T23:55:00.123456+01:00'))
        self.assertEqual(datetime.fromisoformat(row['upo_at']), datetime.fromisoformat('2026-11-05T00:15:00+01:00'))
        self.assertNotIn('<ds:', str(row))

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
        self.assertEqual((row['sent_at'], row['upo_at']), (None, None))

    def test_payroll_documents_buffers_and_zero_months(self):
        self.writer.execute("INSERT INTO dbo.DefListPlac VALUES (1, 'LP')")
        self.writer.execute("INSERT INTO dbo.ListyPlac VALUES (1, '2026-11-05', 1)")
        self.writer.execute("INSERT INTO dbo.Wyplaty VALUES (1, '2026-11-05', 1, 1), (2, '2026-11-05', 1, 0)")
        rows = self.query('payroll.sql', date(2026, 10, 1))
        self.assertEqual((rows[0]['miesiac'], rows[0]['liczba']), ('2026-10', 0))
        self.assertEqual(sum(row['liczba'] for row in rows[1:]), 3)
        self.assertTrue(any('bufor' in (row['rodzaj'] or '') for row in rows))

    def test_saved_mapping_restores_counts_and_declarations_with_missing_or_pesel_identity(self):
        self.declaration(confirmation=receipt())
        self.jpk(confirmation=receipt())
        self.writer.execute("INSERT INTO dbo.DokEwidencja (ID,Definicja) VALUES (91,1)")
        self.writer.execute("INSERT INTO dbo.Dziennik VALUES (91,'2026-11-05',1,91)")
        for identity in (None,'','90010112345'):
            with self.subTest(identity=identity):
                self.writer.execute('UPDATE dbo.HstDanychFirmy SET NIP=?',identity)
                self.reader.execute("EXEC sys.sp_set_session_context @key=N'biuro_company_nip', @value=?",'1112223344')
                for name in ('kpir.sql','declarations.sql','jpk.sql','payroll.sql'):
                    rows = self.query(name)
                    self.assertTrue(rows,name)
                    self.assertEqual({row['nip'] for row in rows},{'1112223344'})
                self.assertEqual(self.query('kpir.sql')[0]['dokumenty'],1)

    def test_missing_identity_without_explicit_mapping_is_not_invented(self):
        self.writer.execute('UPDATE dbo.HstDanychFirmy SET NIP=NULL')
        self.assertEqual(self.query('jpk.sql'),[])

    def test_mapping_never_replaces_a_different_valid_database_nip(self):
        self.reader.execute("EXEC sys.sp_set_session_context @key=N'biuro_company_nip', @value=?",'9998887766')
        self.assertEqual(self.query('jpk.sql')[0]['nip'],'1112223344')

    def test_company_history_ignores_old_future_and_branch_nips(self):
        self.writer.execute("INSERT INTO dbo.HstDanychFirmy VALUES ('1999-01-01',N'2223334455'),('2099-01-01',N'3334445566')")
        # The seeded branch has a different NIP; it must never identify this company.
        self.assertEqual(self.query('jpk.sql')[0]['nip'],'1112223344')

    def test_reader_cannot_write(self):
        import pyodbc
        with self.assertRaises(pyodbc.Error):
            self.reader.execute('DELETE FROM dbo.JednolitePK')
