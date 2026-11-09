"""Company-import updates preserve source presence and complete price identity."""
import os
import unittest

from openpyxl import Workbook

from app.companies.cennik_keys import parse_cennik_key
from app.db.dao_companies import get_company
from app.db.dao_conn import get_conn
from app.utils.import_enrichment import enrich_payload
from app.utils.importers import (
    _build_payload_from_row,
    import_companies_from_excel_file,
    save_imported_payload,
)
from tests.base import DbTestCase


def sheet_payload(values):
    headers = list(values)
    return _build_payload_from_row(headers, {name: i for i, name in enumerate(headers)}, list(values.values()))


class MasterImportPresenceTests(DbTestCase):
    def test_partial_sheet_keeps_existing_contract_counts_and_health_flag(self):
        company = self.make_company('Existing', nip='9240000010', uop_ilosc=7,
                                    uz_ilosc=3, uod_ilosc=2, wliczac_zdrowotna=1)
        wb = Workbook()
        wb.active.append(['Nazwa Firmy', 'NIP'])
        wb.active.append(['Existing', '9240000010'])
        path = os.path.join(self._tmp, 'partial.xlsx')
        wb.save(path)
        result = import_companies_from_excel_file(path)
        self.assertEqual(result['updated'], 1)
        actual = get_company(company)
        self.assertEqual([actual[key] for key in ('uop_ilosc', 'uz_ilosc', 'uod_ilosc', 'wliczac_zdrowotna')],
                         [7, 3, 2, 1])

    def test_explicit_empty_pricing_and_zero_payload_remain_updates(self):
        company = self.make_company('Existing', nip='9240000010', uop_ilosc=7,
                                    uz_ilosc=3, uod_ilosc=2, wliczac_zdrowotna=1)
        payload = sheet_payload({'Nazwa Firmy': 'Existing', 'NIP': '9240000010',
                                 'Cenniki usług kadrowych': '', 'Cenniki usług księgowych': ''})
        save_imported_payload(payload)
        actual = get_company(company)
        self.assertEqual([actual[key] for key in ('uop_ilosc', 'uz_ilosc', 'uod_ilosc', 'wliczac_zdrowotna')],
                         [0, 0, 0, 0])

    def test_enriched_aliases_fill_empty_canonical_fields_but_keep_source_address(self):
        payload = sheet_payload({'Nazwa Firmy': 'Enriched', 'NIP': '9240000010',
                                 'Ulica': 'Źródłowa 12', 'Adres korespondencyjny - miasto': 'Z pliku'})
        self.assertIsNone(enrich_payload(payload, lambda _: {
            'dz_miasto': 'Lublin', 'dz_ulica': 'Rejestrowa', 'dz_budynek': '99',
            'dz_kod': '20-001', 'koresp_miasto': 'Warszawa', 'koresp_ulica': 'Korespondencyjna',
        }))
        save_imported_payload(payload)
        with get_conn() as conn:
            actual = conn.execute('SELECT * FROM firmy WHERE nip=%s', ('9240000010',)).fetchone()
        self.assertEqual((actual['miasto'], actual['kod_pocztowy']), ('Lublin', '20-001'))
        self.assertEqual((actual['ulica'], actual['adres_budynek']), ('Źródłowa', '12'))
        self.assertEqual((actual['adres_koresp_miasto'], actual['adres_koresp_ulica']),
                         ('Z pliku', 'Korespondencyjna'))


class AccountingPriceIdentityTests(DbTestCase):
    def test_extra_and_health_rates_have_distinct_reusable_keys(self):
        keys = []
        for i, (extra, health) in enumerate(((6, 0), (9, 0), (9, 30), (9, 30))):
            payload = sheet_payload({
                'Nazwa Firmy': f'Pricing {i}', 'NIP': f'12345678{i:02d}',
                'Forma opodatkowania': 'Skala podatkowa', 'VAT': 'Nie',
                'Cenniki usług księgowych': f'Ryczałt 350 zł, ponad limit 10 szt. {extra} zł, zdrowotna {health} zł',
            })
            save_imported_payload(payload)
            with get_conn() as conn:
                keys.append(conn.execute('SELECT glowny_cennik FROM firmy WHERE nip=%s',
                                         (payload['nip'],)).fetchone()[0])
        self.assertEqual(keys[0], 'KPIR_N_10_350')
        self.assertEqual(len(set(keys)), 3)
        self.assertEqual(keys[2], keys[3])
        with get_conn() as conn:
            rows = conn.execute('SELECT klucz, cena_za_dokument_ponad_limit, zdrowotna '
                                'FROM cenniki WHERE klucz=ANY(%s)', (keys,)).fetchall()
        prices = {row['klucz']: (row['cena_za_dokument_ponad_limit'], row['zdrowotna']) for row in rows}
        self.assertEqual([prices[key] for key in keys], [(6, 0), (9, 0), (9, 30), (9, 30)])
        self.assertTrue(all(parse_cennik_key(key)['price'] == 350 for key in keys))


if __name__ == '__main__':
    unittest.main()
