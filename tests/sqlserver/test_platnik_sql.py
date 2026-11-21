"""Native SQL Server checks for standard Płatnik forms, with synthetic records only."""
from datetime import date
import os
import unittest
import uuid

from app.integrations.platnik.declarations import normalize, source_query
from app.integrations.platnik.form_catalog import ADDITIONAL_MONTHLY, ANNUAL, MONTHLY, requirements
from app.integrations.platnik.insurance import QUERY as INSURANCE_QUERY, REQUIRED as REGISTRY_TABLES, normalize_periods

DSN = os.environ.get('BIURO_SQLSERVER_TEST_DSN')
NIP = '1112223344'
FORMS = MONTHLY | ADDITIONAL_MONTHLY | ANNUAL
ALIASES = {
    ('ZUSRCA', 'I_1_2OKRROZL'): 'I_1_2OKKROZL',
    ('ZUSRSA', 'I_1_2OKRROZL'): 'I_1_2OKKROZL',
    ('ZUSRZA', 'I_1_2OKRROZL'): 'I_1_2OKKROZL',
    ('ZUSRPA', 'I_1_1IDRAPS'): 'I_1_11DRAPS',
}
TABLES = requirements(FORMS) | REGISTRY_TABLES


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
        for table, names in TABLES.items():
            names = {ALIASES.get((table, name), name) for name in names}
            if table == 'DOKUMENT':
                names.update(('ID_DOKUMENT', 'STATUSPT'))
            if table == 'ZUSDRA':
                names.update(('XI_1_1KODTYTUB','XI_1_2PRDOEM','XI_1_3STNIEP'))
            if table == 'UBEZP_ZUSRCA':
                names.update(('III_B_1_1KODTYTUB','III_B_1_2PRDOEM','III_B_1_3STNIEP'))
            if table == 'UBEZP_ZUSRPA':
                names.update(('III_A_5_1KODTYTUB', 'III_A_5_2PRDOEM', 'III_A_5_3STNIEP'))
            if table in ('ZUSZUA', 'ZUSZZA', 'ZUSZWUA'):
                prefix = 'IV_1_' if table == 'ZUSZWUA' else 'V_1_'
                names.update(prefix + field for field in ('1KODTYTUB', '2PRDOEM', '3STNIEP'))
            columns = []
            for name in sorted(names):
                kind = ('int' if name == 'ID' or name.startswith('ID_') else
                        'datetime' if name.endswith('DATAWYPEL') or name in ('DATAOD','DATADO') else 'nvarchar(64)')
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
        for table in TABLES:
            self.writer.execute('DELETE FROM dbo.' + table)

    def query(self, annual=False, selected=None):
        cursor = self.reader.cursor()
        sql = source_query(cursor, {}, annual=annual, scoped=selected is not None)
        parameters = [date(2025, 1, 1) if annual else date(2026, 11, 1),
                      date(2026, 1, 1) if annual else date(2026, 12, 1)]
        if selected is not None:
            parameters.append('<p>' + ''.join(f'<n>{nip}</n>' for nip in selected) + '</p>')
        cursor.execute(sql, *parameters)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def document(self, identity, parent=None, payer=7, status='N'):
        self.writer.execute('INSERT INTO dbo.DOKUMENT (ID_DOKUMENT, ID_PLATNIK, ID_DOK_NAD, STATUSPT) VALUES (?, ?, ?, ?)',
                            identity, payer, parent, status)

    def form(self, code, identity, period=None, *, payer=7, nip=NIP):
        spec = FORMS[code]
        period = period or ('2025' if code in ANNUAL else '2026-11-12' if spec.get('kind') == 'filled_date' else '11/2026')
        values = {'ID_DOKUMENT': identity, 'ID_PLATNIK': payer, spec.get('nip', 'II_1_NIP'): nip,
                  spec['period']: period}
        if spec.get('report'):
            values[spec['report']] = '01'
        if spec.get('employee'):
            values.update(ID_UBEZPIECZONY=20)
            values.update({spec['first']: 'Anna', spec['last']: 'Synthetic'})
        values = {ALIASES.get((spec['table'], column), column): value for column, value in values.items()}
        self.writer.execute('INSERT INTO dbo.' + spec['table'] + '(' + ','.join(values) + ') VALUES (' +
                            ','.join('?' for _ in values) + ')', *values.values())

    def child(self, code, identity, parent):
        spec = FORMS[code]
        values = {'ID_DOKUMENT': identity, 'ID_DOK_NAD': parent, 'ID_PLATNIK': 7, 'ID_UBEZPIECZONY': 20,
                  spec['first']: 'Anna', spec['last']: 'Synthetic'}
        self.writer.execute('INSERT INTO dbo.' + spec['child'] + '(' + ','.join(values) + ') VALUES (' +
                            ','.join('?' for _ in values) + ')', *values.values())

    def test_all_monthly_forms_and_registration_dates_use_actual_tsql(self):
        forms = MONTHLY | ADDITIONAL_MONTHLY
        for identity, code in enumerate(forms, 1):
            self.document(identity)
            self.form(code, identity)
        rows = self.query()
        entries = normalize(rows, ['2026-11'], [NIP])[NIP]['2026-11']
        self.assertEqual({entry['form'] for entry in entries}, {'ZUS ' + code for code in forms})
        self.assertTrue(all(entry['status'] == 'unknown' for entry in entries))
        self.writer.execute("UPDATE dbo.ZUSZUA SET XV_1_DATAWYPEL='2026-12-01'")
        self.assertNotIn('ZUS ZUA', {row['form'] for row in self.query()})

    def test_employee_rows_use_their_own_document_status(self):
        self.document(1, status='P')
        self.form('RCA', 1)
        self.document(2, parent=1, status='N')
        self.child('RCA', 2, 1)
        self.writer.execute("UPDATE UBEZP_ZUSRCA SET III_B_1_1KODTYTUB='0510',III_B_1_2PRDOEM='0',III_B_1_3STNIEP='0'")
        rows = self.query()
        parent = next(row for row in rows if not row['employee_id'])
        child = next(row for row in rows if row['employee_id'])
        self.assertEqual((parent['raw_status'], child['raw_status']), ('P', 'N'))
        self.assertEqual(child['valid_link'], 1)
        self.assertEqual(child['insurance_code'],'051000')

    def test_scope_filters_on_sql_server_and_retains_invalid_links_for_selected_payers(self):
        self.document(1)
        self.form('RPA', 1, nip='PL 111-222-33-44')
        self.document(2, parent=1)
        self.child('RPA', 2, 1)
        self.writer.execute("UPDATE UBEZP_ZUSRPA SET III_A_5_1KODTYTUB='0510',III_A_5_2PRDOEM='0',III_A_5_3STNIEP='0'")
        self.document(3, payer=8)
        self.form('DRA', 3, period='99/2026', payer=8, nip='9998887766')
        self.document(4, parent=42, payer=8)
        self.child('RPA', 4, 42)
        self.writer.execute('UPDATE UBEZP_ZUSRPA SET ID_PLATNIK=8 WHERE ID_DOKUMENT=4')
        self.writer.execute("INSERT INTO PLATN_IDENT(ID_PLATNIK,NIP) VALUES (8,'9998887766')")
        rows = self.query(selected=[NIP] + [f'{number:010}' for number in range(1000)])
        entries = normalize(rows, ['2026-11'], [NIP])[NIP]['2026-11']
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[1]['insurance_code'], '051000')

        selected = self.query(selected=['9998887766'])
        self.assertEqual({row['source_id'] for row in selected}, {3, 4})
        self.assertEqual(next(row['valid_link'] for row in selected if row['source_id'] == 4), 0)
        with self.assertRaises(ValueError):
            normalize(selected, ['2026-11'], ['9998887766'])

    def test_zipa_is_matched_by_updated_nip(self):
        self.document(1)
        self.form('ZIPA', 1, nip='9998887766')
        self.writer.execute('UPDATE ZUSZIPA SET III_1_NIP=?', NIP)
        row, = self.query(selected=[NIP])
        self.assertEqual(row['nip'], NIP)
        self.assertEqual(row['form'], 'ZUS ZIPA')

    def test_owner_insurance_periods_match_payer_and_person_without_exporting_pesel(self):
        self.writer.execute("""
            INSERT INTO PLATN_IDENT(ID_PLATNIK,NIP,PESEL) VALUES (7,'PL 111-222-33-44','12345678901');
            INSERT INTO UBEZPIECZONY(ID,ID_PLATNIK) VALUES (20,7),(21,7),(22,8);
            INSERT INTO UBEZP_IDENT(ID_UBEZPIECZONY,NIP,PESEL) VALUES
                (20,NULL,'12345678901'),(21,'1112223344','12345678902'),(22,NULL,'12345678901');
            INSERT INTO UBEZP_PODLEGANIE(ID_UBEZPIECZONY,KODTYTUB,PRDOEM,STNIEP,DATAOD,DATADO) VALUES
                (20,'0590','0','0','2026-01-01',NULL),(21,'0570','0','0','2026-01-01',NULL),
                (22,'0510','0','0','2026-01-01',NULL);
        """)
        cursor = self.reader.cursor()
        cursor.execute(INSURANCE_QUERY, '<p><n>1112223344</n></p>')
        self.assertNotIn('PESEL', {column[0].upper() for column in cursor.description})
        self.assertEqual(normalize_periods(cursor.fetchall(), {NIP: 1}), {
            1: [{'nip': NIP, 'code': '059000', 'from': '2026-01-01', 'until': None}],
        })

    def test_registration_and_deregistration_use_their_document_insurance_codes(self):
        for identity, (code, prefix) in enumerate((('ZUA','V_1_'), ('ZZA','V_1_'), ('ZWUA','IV_1_')), 1):
            self.document(identity)
            self.form(code, identity)
            self.writer.execute(f"UPDATE ZUS{code} SET {prefix}1KODTYTUB='0510',"
                                f"{prefix}2PRDOEM='0',{prefix}3STNIEP='0'")
        entries = normalize(self.query(selected=[NIP]), ['2026-11'], [NIP])[NIP]['2026-11']
        self.assertEqual({entry['form']: entry['insurance_code'] for entry in entries},
                         {'ZUS ZUA': '051000', 'ZUS ZZA': '051000', 'ZUS ZWUA': '051000'})

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
