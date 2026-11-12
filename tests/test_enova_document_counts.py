from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, patch
from types import SimpleNamespace
import tempfile
from pathlib import Path

from app.integrations.enova.document_counts import (calculate_total, requested_months,
    validate_rows, run_import, fetch_counts, normalize_nip, sql_credentials)
from app.db.dao_conn import get_conn
from tests.base import DbTestCase

SOURCE = {'database': 'ClientA', 'nip': '1234567890', 'accounting_type': 'KH'}


def row(month='2026-11', documents=10, bank=5, nip='1234567890'):
    return {'nip': nip, 'miesiac': month, 'dokumenty': documents, 'pozycje_bankowe': bank}


class EnovaRulesTests(TestCase):
    def test_kpir_ryczalt_and_kh_rules(self):
        self.assertEqual(calculate_total('KPIR', 45, 0), 45)
        self.assertEqual(calculate_total('RYCZALT', 27, 0), 27)
        self.assertEqual(calculate_total('KH', 92, 190), 155)
        for bank, expected in [(0, 10), (1, 10), (2, 11), (3, 11), (4, 11), (5, 12), (8, 13)]:
            self.assertEqual(calculate_total('KH', 10, bank), expected)

    def test_year_boundary_and_invalid_window(self):
        self.assertEqual(requested_months(date(2026, 1, 5), 3), ['2025-11', '2025-12', '2026-01'])
        for back in (0, 25, True, '3'):
            with self.assertRaises(ValueError):
                requested_months(date(2026, 1, 5), back)

    def test_missing_duplicate_wrong_month_wrong_company_and_invalid_count_rejected(self):
        cases = [[], [row(), row()], [row(month='2026-10')], [row(nip='9999999999')],
                 [row(documents=None)], [row(bank=-1)], [row(documents=Decimal('1.5'))],
                 [row(documents=True)]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_rows(rows, SOURCE, ['2026-11'])
        self.assertEqual(validate_rows([row(documents=0, bank=0)], SOURCE, ['2026-11'])[0].total, 0)
        self.assertEqual(normalize_nip('PL 123-456-78-90'), '1234567890')

    def test_odbc_uses_dates_readonly_tls_and_closes_connection_on_bad_result(self):
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / 'counts.sql'
            query.write_text('SELECT actual_counts WHERE date >= ? AND date < ?')
            source = {**SOURCE, 'count_query': 'counts.sql'}
            connection = Mock()
            cursor = connection.cursor.return_value
            cursor.description = [('nip',), ('miesiac',), ('dokumenty',), ('pozycje_bankowe',)]
            cursor.fetchmany.return_value = [('1234567890', '2026-11', 10, 5)]
            driver = SimpleNamespace(connect=Mock(return_value=connection), drivers=lambda: ['ODBC Driver 18 for SQL Server'])
            with patch.dict('sys.modules', {'pyodbc': driver}), patch.dict('os.environ',
                 {'ENOVA_SQL_USER': 'reader', 'ENOVA_SQL_PASSWORD': 'secret;value}'}):
                rows = fetch_counts({'server': 'sql.example,1433', '_directory': Path(directory)}, source, ['2026-11'])
            self.assertEqual(rows, [row()])
            self.assertEqual(cursor.execute.call_args.args[1:], (date(2026, 11, 1), date(2026, 12, 1)))
            connection.close.assert_called_once()
            self.assertEqual(driver.connect.call_args.kwargs['attrs_before'], {101: 1})
            self.assertIn('Encrypt={yes}', driver.connect.call_args.args[0])
            self.assertIn('TrustServerCertificate={no}', driver.connect.call_args.args[0])
            self.assertIn('PWD={secret;value}}}', driver.connect.call_args.args[0])

    def test_invalid_source_columns_still_close_odbc_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / 'counts.sql'
            query.write_text('SELECT wrong_columns')
            connection = Mock()
            connection.cursor.return_value.description = [('wrong',)]
            driver = SimpleNamespace(connect=Mock(return_value=connection), drivers=lambda: ['ODBC Driver 18 for SQL Server'])
            with patch.dict('sys.modules', {'pyodbc': driver}), patch.dict('os.environ',
                 {'ENOVA_SQL_USER': 'reader', 'ENOVA_SQL_PASSWORD': 'secret'}):
                with self.assertRaises(ValueError):
                    fetch_counts({'server': 'sql', '_directory': Path(directory)},
                                 {**SOURCE, 'count_query': 'counts.sql'}, ['2026-11'])
            connection.close.assert_called_once()


class EnovaImportTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.make_company(nip=SOURCE['nip'])
        self.config = {'enabled': True, 'months_back': 1, 'sources': [deepcopy(SOURCE)]}
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok,dokumenty_dostarczone,sekundy_ksiegowosc,etap) "
                         "VALUES (%s,'2026-11',0,1,3600,'Wysłane wyniki')", (self.company,))

    def test_import_uses_saved_company_type_instead_of_legacy_type(self):
        with get_conn() as conn:
            conn.execute("UPDATE firmy SET forma_opodatkowania='KPiR' WHERE id=%s", (self.company,))
        fetched = Mock(return_value=[row(documents=55, bank=0)])
        result = run_import(self.config, apply=True, today=date(2026, 11, 9), fetch=fetched)
        self.assertTrue(result[0]['ok'])
        self.assertEqual(result[0]['counts'], {'2026-11': 55})
        self.assertEqual(fetched.call_args.args[1]['accounting_type'], 'KPIR')
        self.assertTrue(fetched.call_args.args[1]['count_query'].endswith('kpir.sql'))

    def run_job(self, rows=None, apply=True):
        return run_import(self.config, apply=apply, today=date(2026, 11, 9),
                          fetch=lambda *args: rows if rows is not None else [row()])

    def state(self):
        with get_conn() as conn:
            return conn.execute("SELECT ilosc_dok,dokumenty_dostarczone,sekundy_ksiegowosc FROM statusy_miesieczne "
                                "WHERE firma_id=%s AND miesiac='2026-11'", (self.company,)).fetchone()

    def test_preview_is_read_only_and_apply_replaces_without_adding_or_resetting_status(self):
        self.assertTrue(self.run_job(apply=False)[0]['ok'])
        self.assertEqual(tuple(self.state()), (0, 1, 3600))
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM import_history').fetchone()[0], 0)
        for _ in range(2):
            with get_conn() as conn:
                conn.execute("DELETE FROM app_settings WHERE klucz LIKE 'enova_document_count_checked_%%'")
            self.assertTrue(self.run_job()[0]['ok'])
            self.assertEqual(tuple(self.state()), (12, 1, 3600))
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM import_history WHERE status='completed'").fetchone()[0], 2)

    def test_missing_source_data_preserves_existing_counts_and_records_failure(self):
        self.assertFalse(self.run_job(rows=[])[0]['ok'])
        self.assertEqual(self.state()[0], 0)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT status FROM import_history').fetchone()[0], 'failed')

    def test_duplicate_nip_mapping_is_rejected_and_explicit_zero_is_applied(self):
        with get_conn() as conn:
            duplicate = conn.execute("INSERT INTO firmy(nazwa,nip) VALUES ('Duplicate',%s) RETURNING id", (SOURCE['nip'],)).fetchone()[0]
        self.assertFalse(self.run_job()[0]['ok'])
        self.assertEqual(self.state()[0], 0)
        with get_conn() as conn:
            conn.execute('DELETE FROM firmy WHERE id=%s', (duplicate,))
        self.assertTrue(self.run_job(rows=[row(documents=0, bank=0)])[0]['ok'])
        self.assertEqual(self.state()[0], 0)

    def test_one_failed_database_does_not_prevent_other_client_import(self):
        other = self.make_company('Other', nip='1234567891')
        self.config['sources'].append({**SOURCE, 'database': 'ClientB', 'nip': '1234567891'})
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,etap) VALUES (%s,'2026-11','Wysłane wyniki')", (other,))
        def fetch(config, source, months):
            return [] if source['database'] == 'ClientA' else [row(nip=source['nip'])]
        results = run_import(self.config, apply=True, today=date(2026, 11, 9), fetch=fetch)
        self.assertEqual([result['ok'] for result in results], [False, True])
        self.assertEqual(self.state()[0], 0)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s', (other,)).fetchone()[0], 12)

    def test_disabled_import_cannot_write(self):
        self.config['enabled'] = False
        with self.assertRaises(ValueError):
            self.run_job()
        self.assertEqual(self.state()[0], 0)

    def test_overlapping_daily_job_cannot_write(self):
        with get_conn() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(748209)')
            with self.assertRaisesRegex(ValueError, 'już uruchomiony'):
                self.run_job()
        self.assertEqual(self.state()[0], 0)

    def test_enova_zero_has_priority_over_physical_documents_in_reports(self):
        from app.reports.reports_logic import build_report
        with get_conn() as conn:
            conn.execute("INSERT INTO dokumenty(firma_id,data_wyst) VALUES (%s,'2026-11-09')", (self.company,))
        self.assertTrue(self.run_job(rows=[row(documents=0, bank=0)])[0]['ok'])
        self.assertEqual(build_report([self.company], ['2026-11'])[self.company]['2026-11']['dokumenty'], 0)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT source_name FROM monthly_document_imports WHERE firma_id=%s', (self.company,)).fetchone()[0], 'Enova: ClientA')


class AutomaticAccountingTypeTests(TestCase):
    def test_saved_type_selects_bundled_query_without_repeating_configuration(self):
        from app.integrations.enova.document_counts import resolve_source
        for label, kind in [('KPiR', 'KPIR'), ('Księgi rachunkowe', 'KH'), ('Ryczałt', 'RYCZALT')]:
            with self.subTest(label=label):
                result = resolve_source({'database': 'client', 'nip': '1234567890'},
                                        {'forma_opodatkowania': label})
                self.assertEqual(result['accounting_type'], kind)
                self.assertTrue(Path(result['count_query']).is_file())

    def test_unsupported_type_and_mismatched_custom_query_do_not_guess(self):
        from app.integrations.enova.document_counts import resolve_source
        for source, label in [({}, 'Karta podatkowa'),
                               ({'accounting_type': 'KPIR', 'count_query': 'custom.sql'}, 'Księgi rachunkowe')]:
            with self.subTest(label=label), self.assertRaises(ValueError):
                resolve_source(source, {'forma_opodatkowania': label})
