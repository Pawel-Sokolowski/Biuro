import json
from unittest import TestCase
from unittest.mock import Mock, patch

from app.db.dao_conn import get_conn
from app.integrations.platnik.declarations import REQUIRED, source_query, normalize, import_months, decoded_snapshot
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
        self.assertIn('d.ID_DOKUMENT', query)
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
        self.make_company('Duplicate NIP', nip=NIP)
        with self.assertRaises(ValueError):
            self.import_rows([ROW])
