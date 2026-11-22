import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.integrations.platnik.gui_agent import load_config
from app.integrations.platnik.gui_desktop import checked_columns, payer_rows
from app.integrations.platnik.gui_evidence import ReadError, collect, display_check
from app.utils.nip import normalize_nip

NIP = '1234567890'
MONTH = '2026-11'
ROW = {'nip': 'PL 123-456-78-90', 'period': '11/2026', 'form': 'DRA', 'report': '01',
       'set_id': 'ZESTAW-9', 'status': 'Potwierdzony', 'receipt': 'POTW-9',
       'sent_at': '25.11.2026 14:02'}
LABELS = {'potwierdzony': 'accepted', 'przekazany': 'sent', 'odrzucony': 'rejected'}


class EvidenceTests(TestCase):
    def test_agent_reads_details_only_for_the_requested_payer(self):
        from unittest.mock import Mock, call
        cells = {(0, 0): '9999999999', (1, 0): 'PL 123-456-78-90',
                 (1, 1): 'Potwierdzony', (2, 0): ''}
        read_cell = Mock(side_effect=lambda row, column: cells[row, column])
        rows = payer_rows(read_cell, {'nip': 0, 'status': 1}, 3, NIP)
        self.assertEqual(list(rows), [{'nip': 'PL 123-456-78-90', 'status': 'Potwierdzony'}])
        self.assertEqual(read_cell.call_args_list, [call(0, 0), call(1, 0), call(1, 1), call(2, 0)])

    def test_exact_scope_and_duplicate_rows_in_one_set(self):
        excluded = [{**ROW, 'nip': '0000000000'}, {**ROW, 'period': '10/2026'},
                    {**ROW, 'form': 'RSA'}]
        found = collect([*excluded, ROW, ROW], NIP, MONTH, LABELS)
        self.assertEqual(found, [{
            'form': 'ZUS DRA', 'report': '01', 'set_id': 'ZESTAW-9', 'status': 'accepted',
            'receipt': 'POTW-9', 'sent_at': '2026-11-25T14:02:00',
        }])
        text, details = display_check({'state': 'completed', 'findings': found},
                                      {'source': 'ZUS DRA', 'reports': ['1', '02']})
        self.assertEqual(text, 'Dane dla części korekt')
        self.assertIn('POTW-9', details)

    def test_duplicate_sets_never_confirm_a_report(self):
        with self.assertRaisesRegex(ReadError, 'DuplicateEvidence'):
            collect([ROW, {**ROW, 'set_id': 'ZESTAW-10'}], NIP, MONTH, LABELS)
        with self.assertRaisesRegex(ReadError, 'DuplicateEvidence'):
            collect([ROW, {**ROW, 'status': 'Nieznany'}], NIP, MONTH, LABELS)

    def test_acceptance_requires_receipt_and_sent_requires_date(self):
        with self.assertRaisesRegex(ReadError, 'ProfileMismatch'):
            collect([{**ROW, 'receipt': ''}], NIP, MONTH, LABELS)
        with self.assertRaisesRegex(ReadError, 'ProfileMismatch'):
            collect([{**ROW, 'status': 'Przekazany', 'sent_at': ''}], NIP, MONTH, LABELS)
        self.assertEqual(collect([{**ROW, 'status': 'Nieznany'}], NIP, MONTH, LABELS), [])

    def test_header_order_must_match_profile(self):
        field = {'nip': {'index': 0, 'header': 'Numer NIP'}}
        self.assertEqual(checked_columns(field, [' Numer   NIP ', 'Miesiąc']), {'nip': 0})
        with self.assertRaisesRegex(ReadError, 'ProfileMismatch'):
            checked_columns(field, ['Miesiąc', 'Numer NIP'])

    def test_matching_preserves_legacy_nip_formats(self):
        for value in ('P-L 12\t34-56\xa078-90', '1234567890', 1234567890):
            self.assertEqual(normalize_nip(value), NIP)
        for value in ('１２３４５６７８９０', '123−456-78-90', 'NIP1234567890', None):
            with self.assertRaises(ValueError):
                normalize_nip(value)

    def test_profile_is_local_and_accepts_only_tab_navigation(self):
        template = Path(__file__).resolve().parents[1] / 'docs' / 'platnik' / 'gui-agent.example.json'
        profile = json.loads(template.read_text(encoding='utf-8'))
        with TemporaryDirectory() as directory:
            binary = Path(directory) / 'platnik.exe'
            binary.touch()
            profile['executable'] = str(binary)
            config = Path(directory) / 'profile.json'
            config.write_text(json.dumps(profile), encoding='utf-8')
            self.assertEqual(load_config(config)['schema'], 'public')
            profile['tabs'] = [{'click': 'Wyślij'}]
            config.write_text(json.dumps(profile), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'zakładki UIA'):
                load_config(config)
