from datetime import datetime
from unittest import TestCase
from app.invoices.numbering import DEFAULT, render, compile_pattern
from tests.base import DbTestCase
from app.db.dao_invoices import create_or_update_invoice
from app.db.dao_app_settings import set_setting
from app.db.dao_invoices import assign_invoice_number, get_invoice_by_id


class TemplateValidationTests(TestCase):
    def test_examples_and_invalid_formats(self):
        issued = datetime(2026, 11, 5)
        self.assertEqual(render(DEFAULT, 'FV', issued), 'FV/0001/11/2026')
        self.assertEqual(render('{year}/{prefix}/{number:05d}', 'KFV', issued, 42), '2026/KFV/00042')
        for pattern in ('{unknown}/{number}/{year}', '{number}', '{number}/{number}/{year}',
                        '{number:999999d}/{year}', '{number}{year}', '{prefix.__class__}/{number}/{year}'):
            with self.assertRaises(ValueError, msg=pattern):
                compile_pattern(pattern, 'FV', issued)


class TemplateAllocationTests(DbTestCase):
    def draft(self, name):
        return create_or_update_invoice(self.make_company(name), "2026-11", [
            {"nazwa": "Usługa", "ilosc": 1, "cena_netto": 10, "vat": 23}], 10, 12.3)

    def test_custom_pattern_continues_existing_series_and_preserves_assigned_number(self):
        set_setting('faktury_wzor_numeracji', '{prefix}/{year}/{month:02d}/{number:05d}')
        first, second = self.draft('First custom'), self.draft('Second custom')
        one, two = assign_invoice_number(first), assign_invoice_number(second)
        self.assertTrue(one.endswith('/00001'), one)
        self.assertTrue(two.endswith('/00002'), two)
        set_setting('faktury_wzor_numeracji', DEFAULT)
        self.assertEqual(assign_invoice_number(first), one)
        self.assertEqual(get_invoice_by_id(first)['numer'], one)

    def test_annual_series_continues_across_months(self):
        from app.db.dao_conn import get_conn
        set_setting('faktury_wzor_numeracji', '{prefix}/{number:04d}/{year}')
        first, second = self.draft('January'), self.draft('February')
        with get_conn() as conn:
            conn.execute("UPDATE faktury SET data_wystawienia='2026-01-05' WHERE id=%s", (first,))
            conn.execute("UPDATE faktury SET data_wystawienia='2026-02-05' WHERE id=%s", (second,))
        self.assertEqual(assign_invoice_number(first), 'FV/0001/2026')
        self.assertEqual(assign_invoice_number(second), 'FV/0002/2026')

    def test_document_kinds_have_independent_counters_with_literal_fv_template(self):
        from app.db.dao_conn import get_conn
        for template in (DEFAULT, 'FV/{number:04d}/{month:02d}/{year}'):
            set_setting('faktury_wzor_numeracji', template)
            ids = [self.draft(str((template, i))) for i in range(6)]
            kinds = ['zaliczkowa', 'faktura', 'korekta', 'faktura', 'zaliczkowa', 'korekta']
            with get_conn() as conn:
                for invoice_id, kind in zip(ids, kinds):
                    conn.execute("UPDATE faktury SET rodzaj=%s, data_wystawienia='2026-11-01' WHERE id=%s", (kind, invoice_id))
            # Each template continues the same rendered series, independently per kind.
            start = 1 if template == DEFAULT else 3
            expected = [f'FZAL/{start:04d}/11/2026', f'FV/{start:04d}/11/2026',
                        f'FKOR/{start:04d}/11/2026', f'FV/{start+1:04d}/11/2026',
                        f'FZAL/{start+1:04d}/11/2026', f'FKOR/{start+1:04d}/11/2026']
            self.assertEqual([assign_invoice_number(i) for i in ids], expected)
