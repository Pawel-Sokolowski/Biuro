from datetime import date
from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.accounting.insights import comparison, previous_active_types, hr_status, needs_attention
from app.integrations.enova.accounting import declarations, declaration_entries
from app.integrations.enova.checking import validate_declarations
from app.integrations.enova.hr_events import validate_events, event_months
from app.preferences.department import default_department


class CheckingInsightsTests(TestCase):
    def test_only_previous_month_drives_changes_and_missing_is_not_zero(self):
        row = {'checking': {'documents': {'accounting': {}}}, 'history': [
            {'month': '2026-10', 'documents': {'accounting': {'AMO': 4}}},
            {'month': '2025-11', 'documents': {'accounting': {'OLD': 9}}}]}
        self.assertEqual(previous_active_types(row, '2026-11'), {'AMO'})
        values = comparison(row, '2026-11', 'accounting', 'AMO')
        self.assertEqual((values['previous'], values['current'], values['delta'], values['percent']), (4, 0, -4, -100))
        self.assertTrue(needs_attention(row, 'accounting', date(2026, 11, 12), month='2026-11'))
        row['checking'] = {}
        self.assertIsNone(comparison(row, '2026-11', 'accounting', 'AMO')['delta'])
        row['checking'] = {'documents': {'accounting': {'AMO': 2}}}
        row['history'][0]['documents']['accounting'] = {}
        values = comparison(row, '2026-11', 'accounting', 'AMO')
        self.assertTrue(values['new'])
        self.assertIsNone(values['percent'])

    def test_payroll_counts_do_not_trigger_comparison_attention(self):
        row = {'checking': {'documents': {'payroll': {}}}, 'history': [
            {'month': '2026-10', 'documents': {'payroll': {'Lista płac': 2}}}]}
        self.assertFalse(needs_attention(row, 'payroll', date(2026, 11, 12), month='2026-11'))

    def test_employee_declarations_remain_individual_and_aggregate_receipts_are_honest(self):
        base = {'nip': '1234567890', 'miesiac': '2026-11', 'department': 'payroll',
                'form': 'PIT-11', 'sent_at': None, 'upo_at': None, 'source_system': 'enova'}
        rows = [{**base, 'employee_id': '1', 'employee_name': 'Anna A', 'status': 'accepted', 'upo': 'REF'},
                {**base, 'employee_id': '2', 'employee_name': 'Jan B', 'status': 'not_sent', 'upo': ''}]
        entries = validate_declarations(rows, {'nip': base['nip']}, ['2026-11'])['2026-11']
        row = {'checking': {'declarations': entries}}
        self.assertEqual(len(declaration_entries(row, 'payroll')), 2)
        summary = declarations(row, 'payroll')['PIT-11']
        self.assertEqual(summary['status_text'], 'Przyjęte: 1/2')
        self.assertEqual((summary['upo_count'], summary['count']), (1, 2))
        self.assertNotIn('upo', summary)
        with self.assertRaises(ValueError):
            validate_declarations([rows[0], rows[0]], {'nip': base['nip']}, ['2026-11'])
        with self.assertRaises(ValueError):
            validate_declarations([{**rows[0], 'employee_name': ''}], {'nip': base['nip']}, ['2026-11'])

    def test_hr_events_preserve_company_employee_and_contract_identities(self):
        source = {'nip': '1234567890'}
        base = {**source, 'event_id': 'a', 'employee_id': '1', 'employee_name': 'Anna A',
                'event_type': 'contract_end', 'due_date': '2026-11-12', 'status': 'active', 'document_number': 'U/1'}
        events = validate_events([base, {**base, 'event_id': 'b', 'document_number': 'U/2'}], source)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]['document_number'], 'U/2')
        self.assertEqual(hr_status(events[0], date(2026, 11, 12)), 'Dzisiaj')
        self.assertEqual(hr_status({**events[0], 'due_date': None}), 'Bezterminowa')
        self.assertEqual(hr_status({**events[0], 'event_type': 'medical_exam', 'due_date': None}), 'Brak terminu')
        self.assertIsNone(hr_status({**events[0], 'status': 'completed'}))
        for change in ({'nip': '9999999999'}, {'employee_id': ''}, {'due_date': 'not-a-date'}):
            with self.assertRaises(ValueError):
                validate_events([{**base, **change}], source)
        self.assertEqual(event_months(date(2026, 12, 31)), ['2026-12', '2027-01', '2027-02', '2027-03', '2027-04'])

    def test_navigation_defaults_respect_role_and_admin(self):
        cases = [({'admin': 0, 'nazwa': 'kadrowa', 'payroll': False, 'accounting': False}, 'payroll'),
                 ({'admin': 0, 'nazwa': 'księgowa', 'payroll': True, 'accounting': True}, 'accounting'),
                 ({'admin': 1, 'nazwa': 'kadrowa', 'payroll': True, 'accounting': False}, 'accounting'),
                 ({'admin': 0, 'nazwa': 'opiekun', 'payroll': True, 'accounting': False}, 'payroll')]
        for record, expected in cases:
            connection = MagicMock()
            connection.execute.return_value.fetchone.return_value = record
            with patch('app.preferences.department.get_conn') as factory:
                factory.return_value.__enter__.return_value = connection
                self.assertEqual(default_department(12), expected)
