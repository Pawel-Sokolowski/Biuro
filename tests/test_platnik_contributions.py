from unittest import TestCase

from app.integrations.platnik.contributions import capture_amounts, summarize


def form(kind, revision='01', *, employee='', title='', owner=None, **amounts):
    entry = {'form': f'ZUS {kind}', 'report_id': revision, 'employee_id': employee,
             'insurance_code': title, 'status_text': 'status wysyłki/UPO nieustalony'}
    capture_amounts(entry, {'contribution_' + key: value for key, value in amounts.items()}, owner)
    return entry


class ContributionReportTests(TestCase):
    def test_dra_uses_latest_revision_and_keeps_exact_amounts(self):
        result = summarize([
            form('DRA', health='1.00', bridging_pensions='5'),
            form('DRA', '02', health='398.01', pension='10.10', disability='2.20',
                 sickness='0.00', accident='0.01', labour_fund='0.00'),
        ])['company']
        self.assertEqual(result['reports'], ['02'])
        self.assertEqual(result['amounts'], {
            'health': '398.01', 'pension': '10.10', 'disability': '2.20', 'sickness': '0.00',
            'accident': '0.01', 'labour_fund': '0.00', 'social': '12.31',
        })

    def test_rca_selects_matched_owner_and_applies_corrections_per_title(self):
        result = summarize([
            form('DRA', health='999.99'),
            form('RCA', employee='3', owner=7, title='051000', health='999.99'),
            form('RCA', employee='7', owner=7, title='051000', health='1'),
            form('RCA', '02', employee='7', owner=7, title='051000', health='398.01'),
            form('RCA', employee='7', owner=7, title='051400', health='0', pension='100.09'),
        ])
        self.assertEqual(result['company']['amounts']['health'], '999.99')
        self.assertEqual(result['owner']['reports'], ['01', '02'])
        self.assertEqual(result['owner']['amounts'], {'health': '398.01'})

    def test_dra_covers_sole_payer_and_adds_independent_series(self):
        result = summarize([
            form('DRA', title='051000', insured_count=1, health='100.01'),
            form('DRA', '40', title='051400', insured_count=1, health='0.01'),
        ])
        self.assertEqual(result['owner'], result['company'])
        self.assertEqual(result['owner']['amounts']['health'], '100.02')
        for kwargs in ({'insured_count': 2, 'title': '051000'}, {'insured_count': 1, 'title': ''}):
            self.assertEqual(summarize([form('DRA', **kwargs)])['owner'], {})

    def test_ambiguous_revisions_do_not_produce_a_total(self):
        for entries in ([form('DRA', health='1'), form('DRA', health='2')],
                        [form('DRA', '51', health='1')]):
            result = summarize(entries)
            self.assertIn('problem', result['company'])
            self.assertNotIn('amounts', result['company'])
