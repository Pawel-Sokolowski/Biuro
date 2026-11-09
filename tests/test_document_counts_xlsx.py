from datetime import datetime
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import Mock, patch
import openpyxl

from app.reports.document_counts_xlsx import read_document_counts, import_document_counts_file, month_header
from app.db.dao_conn import get_conn
from tests.base import DbTestCase

NIP = '6741006281'
HEADERS = ['Firma', 'NIP', 'Razem', 'styczeń 2026r.', 'luty 2026r.', 'marzec 2026r.']


def workbook(path, rows, extra=None):
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append(row)
    if extra:
        sheet = book.create_sheet('Drugi arkusz')
        for row in extra:
            sheet.append(row)
    book.save(path)
    book.close()


class DocumentCountParsingTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'Dowolna nazwa dokumenty.xlsx'

    def test_polish_months_ignore_annual_total_and_keep_zero_without_filling_blanks(self):
        workbook(self.path, [HEADERS, ['Other name', int(NIP), 9999, 19, None, 0]])
        cells, warnings = read_document_counts(self.path)
        self.assertEqual([(c.nip, c.month, c.count) for c in cells], [(NIP, '2026-01', 19), (NIP, '2026-03', 0)])
        self.assertEqual(warnings, [])

    def test_preamble_reordered_columns_years_and_numeric_strings(self):
        workbook(self.path, [['Eksport z systemu'], ['luty 2025r.', 'NIP', 'Firma', datetime(2026, 1, 1)],
                             ['1 234', 'PL 674-100-62-81', 'Name', '15,0']])
        cells, _ = read_document_counts(self.path)
        self.assertEqual({c.month: c.count for c in cells}, {'2025-02': 1234, '2026-01': 15})
        self.assertEqual(month_header('październik 2026r.'), '2026-10')
        self.assertEqual(month_header('grudzień 2026 r.'), '2026-12')

    def test_invalid_nip_is_reported_without_extracting_digits_from_company_name(self):
        workbook(self.path, [HEADERS, ['firma ' + NIP, 233, 19, 15]])
        cells, warnings = read_document_counts(self.path)
        self.assertEqual(cells, [])
        self.assertIn('nieprawidłowy NIP', warnings[0])

    def test_multiple_sheets_identical_duplicates_merge_conflicting_counts_fail(self):
        rows = [HEADERS, ['Name', NIP, 99, 15]]
        workbook(self.path, rows, rows)
        self.assertEqual(len(read_document_counts(self.path)[0]), 1)
        workbook(self.path, rows, [HEADERS, ['Other', NIP, 99, 16]])
        with self.assertRaisesRegex(ValueError, 'sprzeczne'):
            read_document_counts(self.path)

    def test_formulas_without_cached_values_do_not_become_zero(self):
        workbook(self.path, [HEADERS, ['Name', NIP, '=SUM(D2:F2)', '=1+2', 12]])
        cells, warnings = read_document_counts(self.path)
        self.assertEqual([(c.month, c.count) for c in cells], [('2026-02', 12)])
        self.assertIn('formuła', warnings[0])

    def test_duplicate_month_headers_and_invalid_counts_fail(self):
        for headers, value in [(HEADERS, -1), (HEADERS, 1.5), (HEADERS, 'bad'),
                                (['NIP', 'styczeń 2026r.', '2026-01'], 3)]:
            workbook(self.path, [headers, ['Name', NIP, 0, value] if len(headers) > 3 else [NIP, value, value]])
            with self.assertRaises(ValueError):
                read_document_counts(self.path)

    def test_wrong_workbook_is_rejected(self):
        workbook(self.path, [['Firma', 'NIP', 'Razem'], ['Name', NIP, 15]])
        with self.assertRaisesRegex(ValueError, 'nagłówka'):
            read_document_counts(self.path)


class DocumentCountImportTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.make_company('Real company', nip=NIP, uop_ilosc=3, uz_ilosc=2)
        self.path = Path(self._tmp) / 'Historyczne dokumenty.xlsx'
        workbook(self.path, [HEADERS, ['Unrelated name', NIP, 999, 19, None, 0]])
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok,sekundy_ksiegowosc,dokumenty_dostarczone) "
                         "VALUES (%s,'2026-02',7,3600,1)", (self.company,))

    def run_import(self, overwrite=False):
        return import_document_counts_file(self.path, actor_id=1, overwrite=overwrite)

    def test_updates_exact_months_preserves_blanks_status_and_kadry_and_is_repeatable(self):
        self.assertEqual(self.run_import()['updated'], 2)
        self.assertEqual(self.run_import()['unchanged'], 2)
        with get_conn() as conn:
            rows = conn.execute('SELECT miesiac,ilosc_dok,sekundy_ksiegowosc,dokumenty_dostarczone '
                                'FROM statusy_miesieczne WHERE firma_id=%s ORDER BY miesiac', (self.company,)).fetchall()
            self.assertEqual([tuple(row) for row in rows], [('2026-01', 19, 0, 0), ('2026-02', 7, 3600, 1), ('2026-03', 0, 0, 0)])
            self.assertEqual(tuple(conn.execute('SELECT uop_ilosc,uz_ilosc FROM firmy WHERE id=%s', (self.company,)).fetchone()), (3, 2))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM monthly_document_imports').fetchone()[0], 2)

    def test_overwrite_is_explicit(self):
        workbook(self.path, [HEADERS, ['Name', NIP, 999, None, 0]])
        self.assertEqual(self.run_import()['skipped'], 1)
        self.assertEqual(self.run_import(overwrite=True)['updated'], 1)
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s AND miesiac='2026-02'", (self.company,)).fetchone()[0], 0)

    def test_imported_totals_and_zero_take_precedence_over_physical_documents_in_reports_and_view(self):
        from app.reports.reports_logic import build_report
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic
        with get_conn() as conn:
            for month in ('2026-01', '2026-03'):
                conn.execute('INSERT INTO dokumenty(firma_id,data_wyst) VALUES (%s,%s)', (self.company, month + '-10'))
        self.run_import()
        report = build_report([self.company], ['2026-01', '2026-03'])
        self.assertEqual(report[self.company]['2026-01']['dokumenty'], 19)
        self.assertEqual(report[self.company]['2026-03']['dokumenty'], 0)
        logic = MonthlyViewLogic(1)
        for month, expected in [('2026-01', 19), ('2026-03', 0)]:
            rows = logic.get_monthly_rows(month, 0, 'Wszystkie')
            self.assertEqual(next(row['ilosc_dok'] for row in rows if row['id'] == self.company), expected)

    def test_invoice_estimates_cannot_overwrite_direct_count_even_with_overwrite(self):
        from app.reports.import_dao import _apply_document_counts
        self.run_import()
        with get_conn() as conn:
            changed = _apply_document_counts(conn, {(self.company, '2026-03'): (100, None)}, {(self.company, '2026-03')}, overwrite=True)
            self.assertEqual(changed, 0)
            self.assertEqual(conn.execute("SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s AND miesiac='2026-03'", (self.company,)).fetchone()[0], 0)

    def test_invalid_file_is_validated_before_any_database_write(self):
        workbook(self.path, [HEADERS, ['Name', NIP, 999, 19, 1.5]])
        with self.assertRaises(ValueError):
            self.run_import()
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM monthly_document_imports').fetchone()[0], 0)

    def test_unmatched_and_ambiguous_nips_are_reported_without_creating_companies(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO firmy(nazwa,nip) VALUES ('Duplicate',%s)", (NIP,))
        self.assertEqual(self.run_import()['unmatched'], 2)
        workbook(self.path, [HEADERS, ['Unknown', '1234567890', 1, 1]])
        self.assertEqual(self.run_import()['unmatched'], 1)

    def test_permission_is_checked_before_opening_workbook(self):
        employee = self.make_employee()
        with self.assertRaises(PermissionError):
            import_document_counts_file('/does/not/exist.xlsx', actor_id=employee)

    def test_profitability_uses_explicit_counts(self):
        from app.reports.import_dao import build_profitability_report
        self.run_import()
        with get_conn() as conn:
            conn.execute("INSERT INTO import_czas_pracy(hash,firma_id,firma_nazwa,data,miesiac,sekundy,dzial) "
                         "VALUES ('direct-counts',%s,'Name','2026-01-10','2026-01',3600,'ksiegowosc')", (self.company,))
        rows = build_profitability_report([self.company], ['2026-01'], 1)
        self.assertEqual(rows[0]['ilosc'], 19)

    def test_daily_report_and_annual_report_use_direct_counts(self):
        from app.reports.reports_logic import build_daily_report, build_yearly_report
        self.run_import()
        with get_conn() as conn:
            conn.execute("INSERT INTO dokumenty(firma_id,data_wyst) VALUES (%s,'2026-01-05')", (self.company,))
            conn.execute("INSERT INTO czasomierze(firma_id,pracownik_id,miesiac,typ,rozpoczecie,zakonczenie,sekundy) "
                         "VALUES (%s,1,'2026-01','ksiegowosc','2026-01-05 10:00:00','2026-01-05 11:00:00',3600)", (self.company,))
        daily = build_daily_report([1], '2026-01-05', '2026-01-05', 1)
        self.assertEqual(daily[0]['dokumenty'], 19)
        self.assertEqual(float(daily[0]['dokumenty_srednia']), 19.0)
        self.assertEqual(build_yearly_report(self.company, 2026)['2026-01']['dokumenty'], 19)

    def test_schema_upgrade_is_current_and_repeatable(self):
        from app.db.schema import upgrade_database, schema_is_current
        upgrade_database()
        upgrade_database()
        self.assertTrue(schema_is_current())

    def test_reports_button_uses_background_import_and_existing_overwrite_option(self):
        from app.reports.reports_ui import ReportsPage
        page = ReportsPage(1)
        self.addCleanup(page.close)
        page.chk_overwrite.setChecked(True)
        page._import_jobs = Mock()
        with patch('PyQt6.QtWidgets.QFileDialog.getOpenFileName', return_value=(str(self.path), '')):
            page._run_import('liczba dokumentów')
        self.assertTrue(page._import_busy)
        work, done, failed = page._import_jobs.submit.call_args.args
        result = work()
        with patch('app.reports.reports_ui.QMessageBox.information'):
            done(result)
        self.assertFalse(page._import_busy)
        self.assertEqual(result['updated'], 2)
