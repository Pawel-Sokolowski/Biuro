from datetime import date
import json
import unittest

from app.companies.monthly_insurance import read_codes, summarize_periods
from app.db.checking_storage import save_snapshot
from app.db.dao_conn import get_conn
from app.integrations.platnik.insurance import SOURCE, normalize_periods
from app.utils.crypto import encrypt_password
from tests.base import DbTestCase

NIP = '1112223344'


class InsuranceRuleTests(unittest.TestCase):
    def test_mid_month_start_and_preferential_months_exclude_partial_first_month(self):
        for code, end in [('054000', '2026-07-31'), ('057000', '2028-01-31')]:
            registry = {'imported_at': '2026-02-11', 'periods': [
                {'nip': NIP, 'code': code, 'from': '2026-01-20', 'until': None},
            ]}
            self.assertEqual(summarize_periods(registry, '2026-02', {})[2], [end + ' · prognoza'])

    def test_plus_uses_new_limit_from_2026_and_deduplicates_overlapping_months(self):
        registry = {'imported_at': '2026-11-21', 'periods': [
            {'nip': NIP, 'code': '059000', 'from': '2024-08-01', 'until': '2026-04-01'},
            {'nip': NIP, 'code': '059000', 'from': '2026-06-01', 'until': None},
            {'nip': NIP, 'code': '059000', 'from': '2026-06-17', 'until': None},
        ]}
        codes, _notes, deadlines = summarize_periods(registry, '2026-11', {})
        self.assertEqual(codes, {'059000'})
        self.assertEqual(deadlines, ['2029-02-28 · prognoza'])
        self.assertEqual(summarize_periods(registry, '2026-05', {}), (set(), [], []))

    def test_discontinuous_preferential_history_needs_the_date_from_the_card(self):
        registry = {'imported_at': '2026-11-21', 'periods': [
            {'nip': NIP, 'code': '057000', 'from': '2025-04-02', 'until': '2026-04-01'},
            {'nip': NIP, 'code': '057000', 'from': '2026-11-01', 'until': None},
        ]}
        self.assertEqual(summarize_periods(registry, '2026-11', {})[2], [])
        self.assertEqual(summarize_periods(registry, '2026-11', {'nip': NIP, 'data_koniec_zus_pref': '2027-04-30'})[2],
                         ['2027-04-30 · kartoteka'])
        self.assertEqual(summarize_periods(registry, '2026-11', {'nip': '1234567890',
                                                              'data_koniec_zus_pref': '2027-04-30'})[2], [])

    def test_source_rejects_two_different_owners_for_one_nip(self):
        row = (NIP, 1, 5, '0570', 0, 0, date(2026, 1, 1), None)
        with self.assertRaises(ValueError):
            normalize_periods([row, (NIP, 1, 6, *row[3:])], {NIP: 1})


class InsuranceViewTests(DbTestCase):
    def test_registry_is_combined_with_monthly_codes_only_for_allowed_company(self):
        company = self.make_company(nip=NIP)
        registry = {'imported_at': '2026-11-21', 'periods': [
            {'nip': NIP, 'code': '059000', 'from': '2026-01-01', 'until': None},
        ]}
        with get_conn() as conn:
            save_snapshot(conn, SOURCE, company, '', encrypt_password(json.dumps(registry)))
            self.assertEqual(read_codes(conn, [], '2026-11'), {})
            row = read_codes(conn, [company], '2026-11')[company]
        from app.monthly_view.monthly_view_model import MonthlyViewModel
        model = MonthlyViewModel([row], 'kadry')
        self.assertEqual(model.data(model.index(0,11)), '059000')
        self.assertEqual(model.data(model.index(0,12)), '2028-12-31 · prognoza')
