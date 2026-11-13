"""Regression inputs for report import identity and shared pricing consumers."""
from pathlib import Path
import unittest
from unittest import mock

from openpyxl import Workbook

from app.db.dao_conn import get_conn
from app.db.dao_monthly import save_month_status
from app.reports.import_dao import (
    _apply_document_counts, _company_index, _match_company, import_invoices_file,
)
from app.reports.import_parse import parse_invoice_rows
from app.reports.reports_logic import build_report, build_yearly_report
from tests.base import DbTestCase


HEADER = ("Nr faktury", "Nazwa kontrahenta", "NIP", "Pozycja faktury", "Wartość netto", "Indeks pozycji")


class InvoiceSourceIdentityTests(unittest.TestCase):
    def test_first_column_invoice_number_preserves_number_month_and_identity(self):
        rows = parse_invoice_rows([HEADER,
            ("FV/0001/01/2026", "Shared", "1234563218", "Usługa księgowa", 350, ""),
            ("FV/0002/02/2026", "Shared", "1234563218", "Usługa księgowa", 350, ""),
        ], "2026-03")
        self.assertEqual([(r['nr_faktury'], r['miesiac']) for r in rows],
                         [("FV/0001/01/2026", "2026-01"), ("FV/0002/02/2026", "2026-02")])
        self.assertEqual(len({r['hash'] for r in rows}), 2)

    def test_first_column_position_is_not_discarded(self):
        rows = parse_invoice_rows([
            ("Pozycja faktury", "Nr faktury", "Wartość netto"),
            ("Usługa księgowa", "FV/1/01/2026", 350),
        ])
        self.assertEqual(rows[0]['pozycja'], "Usługa księgowa")

    def test_identity_preserves_clients_line_codes_and_repeated_lines_when_reordered(self):
        a = ("FV/1/01/2026", "Shared", "1234563218", "Usługa księgowa", 350, "A")
        b = ("FV/1/01/2026", "Shared", "9240000010", "Usługa księgowa", 350, "A")
        c = ("FV/1/01/2026", "Shared", "1234563218", "Usługa księgowa", 350, "B")
        first = parse_invoice_rows([HEADER, a, b, c, a])
        second = parse_invoice_rows([HEADER, c, a, a, b])
        self.assertEqual(len({r['hash'] for r in first}), 4)
        self.assertEqual({r['hash'] for r in first}, {r['hash'] for r in second})


class ReportImportDatabaseTests(DbTestCase):
    def _workbook(self, name, rows):
        path = Path(self._tmp) / name
        workbook = Workbook()
        for row in rows:
            workbook.active.append(row)
        workbook.save(path)
        workbook.close()
        return str(path)

    def test_conflicting_and_ambiguous_names_remain_unmatched(self):
        first = self.make_company("Shared", nip="1234563218", nazwa_skrocona="Ambiguous")
        second = self.make_company("Other", nip="9240000010", nazwa_skrocona="Ambiguous")
        legacy = self.make_company("Legacy without NIP")
        with get_conn() as conn:
            index = _company_index(conn)
        self.assertEqual(_match_company(index, "123-456-32-18", "Other"), first)
        self.assertEqual(_match_company(index, "9240000010", "Shared"), second)
        self.assertIsNone(_match_company(index, "9876543210", "Shared"))
        self.assertIsNone(_match_company(index, "", "Ambiguous"))
        self.assertEqual(_match_company(index, "9876543210", "Legacy without NIP"), legacy)
        path = self._workbook("conflict.xlsx", [HEADER,
            ("FV/1/01/2026", "Shared", "9876543210", "Usługa księgowa", 350, "A")])
        result = import_invoices_file(path, "2026-03")
        self.assertEqual(result['bez_firmy'], 1)
        with get_conn() as conn:
            self.assertIsNone(conn.execute("SELECT firma_id FROM import_faktury_pozycje").fetchone()[0])

    def test_new_identity_reimports_without_losing_clients_or_duplicate_lines(self):
        first = self.make_company("Shared", nip="1234563218")
        second = self.make_company("Shared", nip="9240000010")
        rows = [HEADER,
            ("FV/1/01/2026", "Shared", "9240000010", "Usługa księgowa", 350, "A"),
            ("FV/1/01/2026", "Shared", "9240000010", "Usługa księgowa", 350, "A"),
            ("FV/1/01/2026", "Shared", "9240000010", "Usługa księgowa", 350, "A"),
        ]
        path = self._workbook("identity.xlsx", rows)
        import_invoices_file(path, "2026-03")
        path = self._workbook("identity.xlsx", [HEADER, *reversed(rows[1:])])
        import_invoices_file(path, "2026-03")
        with get_conn() as conn:
            actual = {r['firma_id']: r['total'] for r in conn.execute(
                "SELECT firma_id, SUM(wartosc_netto) AS total FROM import_faktury_pozycje GROUP BY firma_id"
            ).fetchall()}
        self.assertEqual(actual, {first: 700, second: 350})

    def test_matching_legacy_hash_is_promoted_once_without_changing_row_id(self):
        company = self.make_company("Shared", nip="1234563218")
        source_row = ("FV/1/01/2026", "Shared", "1234563218", "Usługa księgowa", 350, "")
        parsed = parse_invoice_rows([HEADER, source_row])[0]
        with get_conn() as conn:
            old_id = conn.execute(
                "INSERT INTO import_faktury_pozycje(hash,firma_id,kontrahent,nip,nr_faktury,pozycja,indeks,"
                "wartosc_netto,waluta,miesiac,rodzaj) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (parsed['legacy_hash'], company, parsed['kontrahent'], parsed['nip'], parsed['nr_faktury'],
                 parsed['pozycja'], parsed['indeks'], 350, 'PLN', '2026-01', parsed['rodzaj']),
            ).fetchone()[0]
        path = self._workbook("legacy.xlsx", [HEADER, source_row, source_row])
        import_invoices_file(path, "2026-03")
        import_invoices_file(path, "2026-03")
        with get_conn() as conn:
            stored = conn.execute("SELECT id,hash,wartosc_netto FROM import_faktury_pozycje ORDER BY id").fetchall()
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[0]['id'], old_id)
        self.assertEqual(stored[0]['hash'], parsed['hash'])
        self.assertEqual(sum(r['wartosc_netto'] for r in stored), 700)

    def test_legacy_collision_with_different_nip_is_not_reassigned(self):
        first = self.make_company("Shared", nip="1234563218")
        second = self.make_company("Shared", nip="9240000010")
        source_row = ("FV/1/01/2026", "Shared", "9240000010", "Usługa księgowa", 350, "A")
        parsed = parse_invoice_rows([HEADER, source_row])[0]
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO import_faktury_pozycje(hash,firma_id,kontrahent,nip,nr_faktury,pozycja,indeks,"
                "wartosc_netto,waluta,miesiac,rodzaj) VALUES (%s,%s,'Shared','1234563218',%s,%s,'A',350,'PLN','2026-01',%s)",
                (parsed['legacy_hash'], first, parsed['nr_faktury'], parsed['pozycja'], parsed['rodzaj']),
            )
        import_invoices_file(self._workbook("collision.xlsx", [HEADER, source_row]), "2026-03")
        with get_conn() as conn:
            actual = {(r['firma_id'], r['nip']) for r in conn.execute(
                "SELECT firma_id,nip FROM import_faktury_pozycje").fetchall()}
        self.assertEqual(actual, {(first, '1234563218'), (second, '9240000010')})

    def test_late_parser_failure_rolls_back_legacy_promotion_and_flushed_rows(self):
        company = self.make_company("Shared", nip="1234563218")
        source_row = ("FV/1/01/2026", "Shared", "9240000010", "Usługa księgowa", 350, "A")
        parsed = parse_invoice_rows([HEADER, source_row])[0]
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO import_faktury_pozycje(hash,firma_id,kontrahent,nip,nr_faktury,pozycja,indeks,"
                "wartosc_netto,waluta,miesiac,rodzaj) VALUES (%s,%s,'Shared','1234563218',%s,%s,'A',350,'PLN','2026-01',%s)",
                (parsed['legacy_hash'], company, parsed['nr_faktury'], parsed['pozycja'], parsed['rodzaj']),
            )
        closed = []
        def source(path):
            try:
                yield HEADER
                yield source_row
                for line in range(500):
                    yield (f"FV/{line + 2}/01/2026", "Shared", "1234563218", "Usługa księgowa", 350, "A")
                raise ValueError("malformed worksheet")
            finally:
                closed.append(True)
        with mock.patch('app.reports.import_dao.read_sheet_rows', side_effect=source):
            with self.assertRaisesRegex(ValueError, 'malformed worksheet'):
                import_invoices_file('broken.xlsx', '2026-01')
        with get_conn() as conn:
            stored = conn.execute("SELECT hash FROM import_faktury_pozycje").fetchall()
        self.assertEqual([r['hash'] for r in stored], [parsed['legacy_hash']])
        self.assertEqual(closed, [True])

    def test_report_and_backfill_use_company_rate_and_hourly_units(self):
        company = self.make_company("Rate override", limit_dokumentow=10, cena_ponad_limit=9, zdrowotna=90)
        save_month_status(company, '2026-01', {'sekundy_ksiegowosc': 3600, 'sekundy_kadry': 5400})
        with get_conn() as conn:
            conn.execute("INSERT INTO cenniki(klucz,netto,zdrowotna,cena_za_dokument_ponad_limit) "
                         "VALUES ('review_shared',350,60,6)")
            conn.execute("UPDATE firmy SET glowny_cennik='review_shared' WHERE id=%s", (company,))
            _apply_document_counts(conn, {(company, '2026-01'): (90, None)}, set())
        monthly = build_report([company], ['2026-01'])[company]['2026-01']
        annual = build_yearly_report(company, 2026)['2026-01']
        for report in (monthly, annual):
            self.assertEqual(report['dokumenty'], 20)
            self.assertEqual(report['koszt_ksiegowosc'], 440)
            self.assertEqual(report['koszt_na_godzine_ksiegowosc'], 440)
            self.assertEqual(report['koszt_kadry'], 90)
            self.assertEqual(report['koszt_na_godzine_kadry'], 60)


if __name__ == '__main__':
    unittest.main()
