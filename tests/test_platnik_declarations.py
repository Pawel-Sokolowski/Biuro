import json
import re
from unittest import TestCase
from unittest.mock import Mock, patch

from app.db.dao_conn import get_conn
from app.integrations.platnik.declarations import REQUIRED, source_query, normalize, import_months, import_year, decoded_snapshot, read_year
from app.integrations.platnik.form_catalog import MONTHLY, ANNUAL
from app.integrations.enova.accounting import list_accounting, declaration_entries
from tests.base import DbTestCase

NIP = '1234567890'
ROW = {'form': 'ZUS DRA', 'source_id': 1, 'payer_id': 7, 'nip': NIP, 'report_id': '01',
       'miesiac': '2026-11', 'employee_id': None, 'employee_name': None, 'raw_status': 'P', 'valid_link': 1}


class PlatnikDeclarationRulesTests(TestCase):
    def test_source_metadata_validation_and_optional_status_do_not_guess_columns(self):
        cursor = Mock()
        metadata = [(table, col) for table, cols in REQUIRED.items() for col in cols] + [('DOKUMENT', 'ID_DOKUMENT')]
        cursor.fetchmany.return_value = metadata
        query = source_query(cursor, {})
        self.assertNotIn('d.STATUSPT', query)
        self.assertNotIn('__DRA_STATUS__', query)
        self.assertEqual(query.count('?'), 2)
        self.assertNotIn('__DOCUMENT_KEY__', query)
        self.assertNotIn('__RECORDS__', query)
        self.assertNotIn('__PERIOD_RESULT__', query)
        self.assertIn('d.ID_DOKUMENT', query)
        for code in MONTHLY:
            self.assertIn("N'ZUS " + code + "'", query)
        self.assertIn('a.XV_1_DATAWYPEL', query)
        self.assertNotIn('ZUSIWA', query)
        annual = source_query(cursor, {}, annual=True)
        self.assertIn('ZUSIWA', annual)
        self.assertIn('ZUSZSWA', annual)
        self.assertIn('AS rok', annual)
        self.assertNotIn('ZUSZUA', annual)
        cursor.fetchmany.return_value = metadata + [('DOKUMENT', 'STATUSPT')]
        self.assertIn('d.STATUSPT', source_query(cursor, {}))
        cursor.fetchmany.return_value = [(table, col) for table, col in metadata if (table, col) != ('DOKUMENT', 'ID_DOKUMENT')] + [('DOKUMENT', 'ID')]
        self.assertIn('d.ID = a.ID_DOKUMENT', source_query(cursor, {}))
        cursor.fetchmany.return_value = []
        with self.assertRaises(ValueError):
            source_query(cursor, {})

    def test_corrections_are_preserved_and_source_code_is_not_proof_of_acceptance(self):
        records = normalize([ROW, {**ROW, 'source_id': 2, 'report_id': '02'}], ['2026-11'], [NIP])
        entries = records[NIP]['2026-11']
        self.assertEqual([entry['report_id'] for entry in entries], ['01', '02'])
        self.assertEqual(entries[0]['raw_status'], 'P')
        self.assertEqual(entries[0]['status'], 'unknown')
        self.assertIsNone(entries[0]['sent_at'])
        self.assertEqual(entries[0]['upo'], '')
        self.assertIsNone(entries[0]['upo_at'])
        for changed in ({'miesiac': None}, {'valid_link': 0}, {'payer_id': 8},
                        {'source_id': 2, 'employee_id': 2, 'employee_name': ''}):
            with self.assertRaises(ValueError):
                normalize([ROW, {**ROW, **changed}], ['2026-11'], [NIP])

    def test_every_standard_form_and_registration_identity_is_supported(self):
        for index, code in enumerate(MONTHLY, 1):
            row = {**ROW, 'form': 'ZUS ' + code, 'source_id': index,
                   'employee_id': 12, 'employee_name': 'Test Person'}
            entry = normalize([row], ['2026-11'], [NIP])[NIP]['2026-11'][0]
            self.assertEqual(entry['form'], 'ZUS ' + code)
            self.assertEqual(entry['employee_id'], '12')
        for code in ANNUAL:
            row = {**ROW, 'form': 'ZUS ' + code, 'rok': '2025'}
            entry = normalize([row], ['2025'], [NIP], annual=True)[NIP]['2025'][0]
            self.assertEqual(entry['reporting_year'], '2025')
            with self.assertRaises(ValueError):
                normalize([row], ['2026'], [NIP], annual=True)


class PlatnikDeclarationSnapshotTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company('Company A', nip=NIP)
        self.other = self.make_company('Company B', nip='9999999999')

    def import_rows(self, rows):
        with patch('app.integrations.platnik.declarations.load', return_value={'enabled': False}), \
             patch('app.integrations.platnik.declarations.fetch', return_value=rows):
            return import_months(['2026-10', '2026-11'], company_ids=[self.company])

    def test_encrypted_company_snapshot_does_not_replace_enova_or_unselected_company(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)",
                         (f'enova_checking_{self.company}_2026-11', json.dumps({'declarations': [
                             {'department': 'payroll', 'form': 'PIT-11', 'status': 'prepared'}]})))
            conn.execute("INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)",
                         (f'platnik_declarations_{self.other}_2026-11', 'unchanged'))
        # Select only A when reading its snapshot; B's unrelated payload is not accessed.
        self.import_rows([ROW, {**ROW, 'source_id': 2, 'form': 'ZUS RCA', 'employee_id': 12, 'employee_name': 'Test Person'}])
        with get_conn() as conn:
            stored = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',
                                  (f'platnik_declarations_{self.company}_2026-11',)).fetchone()[0]
            other = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',
                                 (f'platnik_declarations_{self.other}_2026-11',)).fetchone()[0]
            enova = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',
                                 (f'enova_checking_{self.company}_2026-11',)).fetchone()[0]
        self.assertNotIn('Test Person', stored)
        self.assertEqual(len(decoded_snapshot(stored)['declarations']), 2)
        self.assertEqual(other, 'unchanged')
        self.assertEqual(json.loads(enova)['declarations'][0]['form'], 'PIT-11')
        with patch('app.integrations.enova.accounting.assigned_company_ids', return_value={self.company}):
            row = list_accounting(self.admin, '2026-11')[0]
        self.assertEqual({entry['form'] for entry in declaration_entries(row, 'payroll')}, {'PIT-11', 'ZUS DRA', 'ZUS RCA'})

    def test_failed_read_and_duplicate_nip_preserve_previous_snapshot(self):
        self.import_rows([ROW])
        with get_conn() as conn:
            before = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',
                                  (f'platnik_declarations_{self.company}_2026-11',)).fetchone()[0]
        with self.assertRaises(ValueError):
            self.import_rows([{**ROW, 'valid_link': 0}])
        with get_conn() as conn:
            after = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',
                                 (f'platnik_declarations_{self.company}_2026-11',)).fetchone()[0]
        self.assertEqual(before, after)
        # Normal company saves merge by NIP; model an actual legacy duplicate.
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET nip=%s WHERE id=%s', (NIP, self.other))
            self.assertEqual(conn.execute('SELECT count(*) FROM firmy WHERE nip=%s', (NIP,)).fetchone()[0], 2)
        with self.assertRaises(ValueError):
            self.import_rows([ROW])

    def test_annual_reads_use_reporting_year_and_yearly_obligations_without_changing_office_filings(self):
        from app.db.dao_year_end import save_company_year_declarations
        from app.db.dao_app_settings import get_setting
        save_company_year_declarations(self.company, {2025: ['ZUS IWA']}, self.admin)
        rows = [{**ROW, 'form': 'ZUS IWA', 'rok': '2025'},
                {**ROW, 'form': 'ZUS ZSWA', 'rok': '2025', 'source_id': 2}]
        with patch('app.integrations.platnik.declarations.load', return_value={'enabled': False}), \
             patch('app.integrations.platnik.declarations.fetch', return_value=rows) as fetch:
            import_year(2025, company_ids=[self.company])
        fetch.assert_called_once_with({'enabled': False}, ['2025'], annual=True)
        self.assertIsNone(get_setting(f'platnik_declarations_{self.company}_2025-12'))
        stored = decoded_snapshot(get_setting(f'platnik_declarations_{self.company}_year_2025'))
        self.assertEqual(len(stored['declarations']), 2)
        payload = read_year(self.admin, 2025)
        company = next(company for company in payload['companies'] if company['id'] == self.company)
        self.assertEqual(company['forms'], ['IWA'])
        self.assertEqual([entry['form'] for entry in company['declarations']], ['ZUS IWA'])
        from app.accounting.annual_zus_ui import AnnualZusPanel
        panel = AnnualZusPanel(self.admin)
        self.addCleanup(panel.close)
        panel.payload = payload
        panel.set_filter('IWA', '')
        self.assertEqual(panel.table.rowCount(), 1)
        panel.set_filter('ZSWA', '')
        self.assertEqual(panel.table.rowCount(), 0)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM zakonczenie_roku WHERE firma_id=%s', (self.company,)).fetchone()[0], 0)


class PlatnikSourceSqlTests(DbTestCase):
    """Execute shipped joins/ranges against PostgreSQL with explicit dialect conversion.

    This does not establish native SQL Server compatibility.
    """
    def setUp(self):
        super().setUp()
        self.metadata = [(table, col) for table, cols in REQUIRED.items() for col in cols]
        self.metadata += [('DOKUMENT', 'ID_DOKUMENT'), ('DOKUMENT', 'STATUSPT')]
        with get_conn() as conn:
            for table in REQUIRED:
                cols = [col for name, col in self.metadata if name == table]
                declarations = [col + (' int' if col.startswith('ID_') else ' text') for col in cols]
                conn.execute('CREATE TABLE ' + table + '(' + ','.join(declarations) + ')')

    def query(self, conn, annual=False):
        cursor = Mock()
        cursor.fetchmany.return_value = self.metadata
        sql = source_query(cursor, {}, annual=annual)
        # The conversion helper rejects malformed dates just like TRY_CONVERT.
        conn.execute("""CREATE OR REPLACE FUNCTION pg_temp.try_date(value text, fmt text) RETURNS date
            LANGUAGE plpgsql AS $$ BEGIN RETURN to_date(value,fmt); EXCEPTION WHEN others THEN RETURN NULL; END $$""")
        sql = re.sub(r'CONVERT\(nvarchar\(\d+\),\s*(a\.[A-Z0-9_]+|d\.STATUSPT)(?:,23)?\)', r'CAST(\1 AS varchar)', sql)
        sql = sql.replace('CONVERT(char(7),r.reporting_date,120)', "to_char(r.reporting_date,'YYYY-MM')")
        sql = sql.replace('CONVERT(char(4),r.reporting_date,112)', "to_char(r.reporting_date,'YYYY')")
        sql = sql.replace('TRY_CONVERT(date,r.source_period,23)', "pg_temp.try_date(r.source_period,'YYYY-MM-DD')")
        sql = sql.replace("TRY_CONVERT(date,r.compact_period + N'0101',112)", "pg_temp.try_date(r.compact_period || '0101','YYYYMMDD')")
        sql = sql.replace("TRY_CONVERT(date,RIGHT(r.compact_period,4) + LEFT(r.compact_period,2) + N'01',112)",
                          "pg_temp.try_date(RIGHT(r.compact_period,4) || LEFT(r.compact_period,2) || '01','YYYYMMDD')")
        sql = sql.replace("r.compact_period NOT LIKE N'%[^0-9]%'", "r.compact_period ~ '^[0-9]+$'")
        sql = re.sub(r'nvarchar\(\d+\)', 'varchar', sql)
        sql = sql.replace('LEN(', 'length(').replace("N'", "'").replace('dbo.', '').replace(' + ', ' || ').replace('?', '%s')
        bounds = ('2025-01-01', '2026-01-01') if annual else ('2026-06-01', '2026-07-01')
        return [dict(row) for row in conn.execute(sql, bounds).fetchall()]

    def test_monthly_and_registration_joins_use_correct_period_and_exclusive_end(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO DOKUMENT(ID_DOKUMENT,ID_PLATNIK,STATUSPT) VALUES (1,7,'P'),(10,7,'N'),(11,7,'N')")
            conn.execute("INSERT INTO ZUSRSA(ID_DOKUMENT,ID_PLATNIK,II_1_NIP,I_1_2OKRROZL,I_1_1IDRAPS) VALUES (1,7,%s,'06/2026','02')", (NIP,))
            conn.execute("INSERT INTO UBEZP_ZUSRSA(ID_DOKUMENT,ID_DOK_NAD,ID_PLATNIK,ID_UBEZPIECZONY,III_A_2_IMIEPIERW,III_A_1_NAZWISKO) "
                         "VALUES (2,1,7,20,'Anna','A'),(3,1,7,21,'Jan','B')")
            conn.execute("INSERT INTO ZUSZUA(ID_DOKUMENT,ID_PLATNIK,II_1_NIP,ID_UBEZPIECZONY,III_6_IMIEPIERW,III_5_NAZWISKO,XV_1_DATAWYPEL) "
                         "VALUES (10,7,%s,20,'Anna','A','2026-06-01')", (NIP,))
            conn.execute("INSERT INTO ZUSZWUA(ID_DOKUMENT,ID_PLATNIK,II_1_NIP,ID_UBEZPIECZONY,III_6_IMIEPIERW,III_5_NAZWISKO,VI_1_DATAWYPEL) "
                         "VALUES (11,7,%s,20,'Anna','A','2026-07-01')", (NIP,))
            rows = self.query(conn)
        self.assertEqual(len(rows), 4)
        self.assertEqual({row['form'] for row in rows}, {'ZUS RSA', 'ZUS ZUA'})
        self.assertEqual({row['miesiac'] for row in rows}, {'2026-06'})
        entries = normalize(rows, ['2026-06'], [NIP])[NIP]['2026-06']
        self.assertEqual({entry['employee_id'] for entry in entries if entry['employee_id']}, {'20', '21'})

    def test_annual_query_uses_declared_year_and_rejects_cross_payer_child(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO DOKUMENT(ID_DOKUMENT,ID_PLATNIK) VALUES (1,7),(2,7)")
            conn.execute("INSERT INTO ZUSIWA(ID_DOKUMENT,ID_PLATNIK,II_1_NIP,I_1_2OKRINFOR,I_1_1IDINFOR) VALUES (1,7,%s,'2025','01')", (NIP,))
            conn.execute("INSERT INTO ZUSZSWA(ID_DOKUMENT,ID_PLATNIK,II_1_NIP,I_1_2OKRESZGL,I_1_1IDZGLOSZ) VALUES (2,7,%s,'2025','02')", (NIP,))
            conn.execute("INSERT INTO UBEZP_ZUSZSWA(ID_DOKUMENT,ID_DOK_NAD,ID_PLATNIK,ID_UBEZPIECZONY,III_A_8_IMIEPIERW,III_A_7_NAZWISKO) "
                         "VALUES (3,2,8,20,'Wrong','Payer')")
            rows = self.query(conn, annual=True)
        self.assertEqual({row['rok'] for row in rows}, {'2025'})
        self.assertEqual({row['form'] for row in rows}, {'ZUS IWA', 'ZUS ZSWA'})
        with self.assertRaises(ValueError):
            normalize(rows, ['2025'], [NIP], annual=True)
