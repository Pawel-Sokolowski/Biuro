"""Native SQL Server checks for standard Płatnik forms, with synthetic records only."""
from datetime import date
import os
import unittest
import uuid

from app.integrations.platnik.declarations import REQUIRED, normalize, source_query
from app.integrations.platnik.form_catalog import MONTHLY, ANNUAL

DSN = os.environ.get('BIURO_SQLSERVER_TEST_DSN')
NIP = '1112223344'


@unittest.skipUnless(DSN, 'Requires the isolated GitHub SQL Server service')
class PlatnikSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pyodbc
        pyodbc.pooling = False
        cls.database = 'platnik_ci_' + uuid.uuid4().hex
        cls.master = pyodbc.connect(DSN, autocommit=True, timeout=15)
        cls.master.execute(f'CREATE DATABASE [{cls.database}]')
        cls.writer = pyodbc.connect(DSN + ';DATABASE=' + cls.database, autocommit=True, timeout=15)
        for table, names in REQUIRED.items():
            names = set(names)
            if table == 'DOKUMENT':
                names.update(('ID_DOKUMENT', 'STATUSPT'))
            columns = []
            for name in sorted(names):
                kind = 'int' if name.startswith('ID_') else 'datetime' if name.endswith('DATAWYPEL') else 'nvarchar(64)'
                columns.append('[' + name + '] ' + kind)
            cls.writer.execute('CREATE TABLE dbo.' + table + '(' + ','.join(columns) + ')')
        cls.writer.execute('CREATE USER import_reader WITHOUT LOGIN; GRANT SELECT ON SCHEMA::dbo TO import_reader;')
        cls.reader = pyodbc.connect(DSN + ';DATABASE=' + cls.database, autocommit=True, timeout=15)
        cls.reader.execute("EXECUTE AS USER='import_reader'")

    @classmethod
    def tearDownClass(cls):
        cls.reader.close()
        cls.writer.close()
        cls.master.execute(f'DROP DATABASE [{cls.database}]')
        cls.master.close()

    def setUp(self):
        for table in REQUIRED:
            self.writer.execute('DELETE FROM dbo.' + table)

    def query(self, annual=False):
        cursor = self.reader.cursor()
        sql = source_query(cursor, {}, annual=annual)
        cursor.execute(sql, date(2025, 1, 1) if annual else date(2026, 11, 1),
                       date(2026, 1, 1) if annual else date(2026, 12, 1))
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def document(self, identity, parent=None, payer=7, status='N'):
        self.writer.execute('INSERT INTO dbo.DOKUMENT (ID_DOKUMENT, ID_PLATNIK, ID_DOK_NAD, STATUSPT) VALUES (?, ?, ?, ?)',
                            identity, payer, parent, status)

    def form(self, code, identity, period=None):
        spec = (MONTHLY | ANNUAL)[code]
        period = period or ('2025' if code in ANNUAL else '2026-11-12' if spec.get('kind') == 'filled_date' else '11/2026')
        values = {'ID_DOKUMENT': identity, 'ID_PLATNIK': 7, 'II_1_NIP': NIP, spec['period']: period}
        if spec.get('report'):
            values[spec['report']] = '01'
        if spec.get('employee'):
            values.update(ID_UBEZPIECZONY=20)
            values.update({spec['first']: 'Anna', spec['last']: 'Synthetic'})
        self.writer.execute('INSERT INTO dbo.' + spec['table'] + '(' + ','.join(values) + ') VALUES (' +
                            ','.join('?' for _ in values) + ')', *values.values())

    def child(self, code, identity, parent):
        spec = (MONTHLY | ANNUAL)[code]
        values = {'ID_DOKUMENT': identity, 'ID_DOK_NAD': parent, 'ID_PLATNIK': 7, 'ID_UBEZPIECZONY': 20,
                  spec['first']: 'Anna', spec['last']: 'Synthetic'}
        self.writer.execute('INSERT INTO dbo.' + spec['child'] + '(' + ','.join(values) + ') VALUES (' +
                            ','.join('?' for _ in values) + ')', *values.values())

    def test_all_monthly_forms_and_registration_dates_use_actual_tsql(self):
        for identity, code in enumerate(MONTHLY, 1):
            self.document(identity)
            self.form(code, identity)
        rows = self.query()
        entries = normalize(rows, ['2026-11'], [NIP])[NIP]['2026-11']
        self.assertEqual({entry['form'] for entry in entries}, {'ZUS ' + code for code in MONTHLY})
        self.assertTrue(all(entry['status'] == 'unknown' for entry in entries))
        self.writer.execute("UPDATE dbo.ZUSZUA SET XV_1_DATAWYPEL='2026-12-01'")
        self.assertNotIn('ZUS ZUA', {row['form'] for row in self.query()})

    def test_employee_rows_use_their_own_document_status(self):
        self.document(1, status='P')
        self.form('RCA', 1)
        self.document(2, parent=1, status='N')
        self.child('RCA', 2, 1)
        rows = self.query()
        parent = next(row for row in rows if not row['employee_id'])
        child = next(row for row in rows if row['employee_id'])
        self.assertEqual((parent['raw_status'], child['raw_status']), ('P', 'N'))
        self.assertEqual(child['valid_link'], 1)

    def test_orphan_cross_payer_and_wrong_parent_document_links_are_rejected(self):
        self.document(1)
        self.form('RSA', 1)
        self.child('RSA', 2, 1)
        with self.assertRaises(ValueError):
            normalize(self.query(), ['2026-11'], [NIP])
        self.document(2, parent=1, payer=8)
        with self.assertRaises(ValueError):
            normalize(self.query(), ['2026-11'], [NIP])
        self.writer.execute('UPDATE dbo.DOKUMENT SET ID_PLATNIK=7, ID_DOK_NAD=99 WHERE ID_DOKUMENT=2')
        with self.assertRaises(ValueError):
            normalize(self.query(), ['2026-11'], [NIP])

    def test_annual_year_and_employee_links_are_independent_of_monthly_reports(self):
        self.document(1)
        self.form('IWA', 1)
        self.document(2)
        self.form('ZSWA', 2)
        self.document(3, parent=2)
        self.child('ZSWA', 3, 2)
        entries = normalize(self.query(annual=True), ['2025'], [NIP], annual=True)[NIP]['2025']
        self.assertEqual(len(entries), 3)
        self.assertTrue(all(entry['reporting_year'] == '2025' for entry in entries))
        self.assertEqual(self.query(), [])

    def test_invalid_reporting_period_is_returned_for_validation_instead_of_hidden(self):
        self.document(1)
        self.form('DRA', 1, period='13/2026')
        with self.assertRaises(ValueError):
            normalize(self.query(), ['2026-11'], [NIP])

    def test_legacy_document_key_and_missing_optional_status_are_supported(self):
        self.document(1)
        self.form('DRA', 1)
        self.writer.execute("EXEC sp_rename 'dbo.DOKUMENT.ID_DOKUMENT', 'ID', 'COLUMN'")
        self.writer.execute('ALTER TABLE dbo.DOKUMENT DROP COLUMN STATUSPT')
        try:
            row, = self.query()
            self.assertEqual(row['valid_link'], 1)
            self.assertIsNone(row['raw_status'])
        finally:
            self.writer.execute('ALTER TABLE dbo.DOKUMENT ADD STATUSPT nvarchar(64)')
            self.writer.execute("EXEC sp_rename 'dbo.DOKUMENT.ID', 'ID_DOKUMENT', 'COLUMN'")
