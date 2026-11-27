from contextlib import nullcontext
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock, call, patch

from app.integrations.platnik.gui_agent import bounded_read, load_config, read_job, run
from app.integrations.platnik.gui_desktop import checked_columns, main_window, payer_rows, select_sent_set, uia_grid_metadata
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

    def test_receipt_reference_accepts_the_plus_separated_zus_format(self):
        receipt = 'PW+000000001+20261125150800+0T'
        found, = collect([{**ROW, 'receipt': receipt}], NIP, MONTH, LABELS)
        self.assertEqual(found['receipt'], receipt)

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
            profile['confirmation_download'] = {
                'grid': {'auto_id': 'sent-sets'},
                'columns': {'set_id': {'index': 0, 'header': 'Numer zestawu'}},
            }
            config.write_text(json.dumps(profile), encoding='utf-8')
            self.assertEqual(load_config(config)['confirmation_download']['tabs'], [])
            profile['confirmation_download']['command'] = 'Wyślij'
            config.write_text(json.dumps(profile), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Zestawy wysłane'):
                load_config(config)
            del profile['confirmation_download']
            profile['tabs'] = [{'click': 'Wyślij'}]
            config.write_text(json.dumps(profile), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'zakładki UIA'):
                load_config(config)


class DesktopTests(TestCase):
    def test_launcher_connects_to_the_verified_application_process(self):
        executable = Path('p2.exe')
        launcher = Path('P2start.exe')
        desktop = Mock()
        desktop.windows.side_effect = [[], [Mock(process_id=Mock(return_value=42))]]
        app = Mock(process=42)
        modules = {
            'pywinauto': SimpleNamespace(Application=Mock(return_value=app), Desktop=Mock(return_value=desktop)),
            'pywinauto.timings': SimpleNamespace(wait_until=lambda _timeout, _retry, lookup, **_kwargs: lookup()),
        }
        config = {'executable': executable, 'launcher': launcher, 'backend': 'uia',
                  'window': {'class_name': 'PlatnikMain'}}
        with patch.dict('sys.modules', modules), \
             patch('app.integrations.platnik.gui_desktop.interactive_desktop', return_value=True), \
             patch('app.integrations.platnik.gui_desktop.process_image', return_value=executable), \
             patch('app.integrations.platnik.gui_desktop.Popen') as launch:
            self.assertIs(main_window(config), app.window.return_value)
        launch.assert_called_once_with([str(launcher)], cwd=launcher.parent)
        app.start.assert_not_called()
        app.connect.assert_called_once_with(process=42, timeout=5)
        app.window.assert_called_once_with(class_name='PlatnikMain')

    def test_grid_diagnostics_distinguish_legacy_accessibility_from_grid_patterns(self):
        class UnsupportedPattern(Exception):
            pass

        def interface(_element, name):
            if name != 'LegacyIAccessible':
                raise UnsupportedPattern

        definitions = SimpleNamespace(NoPatternInterfaceError=UnsupportedPattern, get_elem_interface=interface)
        info = SimpleNamespace(control_type='Pane', class_name='TdxDBGrid', element=object())
        with patch.dict('sys.modules', {'pywinauto.uia_defines': definitions}):
            self.assertEqual(uia_grid_metadata(info), {'uia_patterns': ['LegacyIAccessible']})


class DownloadTests(TestCase):
    @patch('app.integrations.platnik.gui_desktop.register')
    def test_probe_reads_configured_columns_and_reports_no_values(self, register):
        from app.integrations.platnik.gui_desktop import probe_register
        read = Mock(side_effect=['private NIP', 'private status'])
        register.return_value = (Mock(), 1, {'nip': 0, 'status': 2}, read)
        report = probe_register(Mock(), {})
        self.assertEqual([args.args for args in read.call_args_list], [(0, 0), (0, 2)])
        self.assertIn('Odczytano pierwszy wiersz', report)
        self.assertNotIn('private', report)

    @patch('app.integrations.platnik.gui_agent.interactive_desktop', return_value=True)
    @patch('app.integrations.platnik.gui_agent.download_confirmation')
    @patch('app.integrations.platnik.gui_agent.grid_rows')
    @patch('app.integrations.platnik.gui_agent.main_window')
    def test_download_uses_matched_sent_set_once_and_reads_registered_state(self, main, grid, download, active):
        sent = {**ROW, 'set_id': '9', 'status': 'Przekazany'}
        missing_id = {**sent, 'set_id': '10', 'report': '02', 'receipt': ''}
        unrelated = {**sent, 'set_id': '11', 'period': '10/2026'}
        grid.side_effect = [
            [sent, {**sent, 'form': 'RCA'}, missing_id, unrelated, {**sent, 'nip': '9999999999'}],
            [{**sent, 'status': 'Potwierdzony'}, {**sent, 'form': 'RCA', 'status': 'Potwierdzony'}, missing_id],
        ]
        view = {'tabs': ['sent']}
        config = {'statuses': LABELS, 'backend': 'uia', 'confirmation_download': view}
        result = read_job(config, {'nip': NIP, 'period': MONTH})
        download.assert_called_once_with(main.return_value, {**view, 'backend': 'uia'}, '9', 'POTW-9')
        self.assertEqual([row['status'] for row in result], ['accepted', 'accepted', 'sent'])

    @patch('app.integrations.platnik.gui_agent.download_confirmation')
    @patch('app.integrations.platnik.gui_agent.grid_rows')
    @patch('app.integrations.platnik.gui_agent.main_window')
    def test_unregistered_download_does_not_report_success(self, main, grid, download):
        grid.return_value = [{**ROW, 'set_id': '9', 'status': 'Przekazany'}]
        config = {'statuses': LABELS, 'backend': 'uia', 'confirmation_download': {'tabs': ['sent']}}
        with self.assertRaisesRegex(ReadError, 'ReadFailed'):
            read_job(config, {'nip': NIP, 'period': MONTH})

    @patch('app.integrations.platnik.gui_desktop.register')
    def test_duplicate_set_number_fails_before_selection(self, register):
        grid = Mock()
        register.return_value = (grid, 2, {'set_id': 0}, Mock(return_value='9'))
        with self.assertRaisesRegex(ReadError, 'ProfileMismatch'):
            select_sent_set(Mock(), {'backend': 'win32'}, '9', 'POTW-9')
        grid.get_item.assert_not_called()


class AgentProcessTests(TestCase):
    def test_frozen_reader_reenters_the_agent_entry_point(self):
        executable = r'C:\Program Files\Biuro\Biuro.exe'
        profile = Path(r'C:\Users\Agent\AppData\Local\Biuro\platnik-agent.json')
        completed = Mock(stdout='{"findings": []}')
        with patch('app.integrations.platnik.gui_agent.sys.frozen', True, create=True), \
             patch('app.integrations.platnik.gui_agent.sys.executable', executable), \
             patch('app.integrations.platnik.gui_agent.subprocess.CREATE_NO_WINDOW', 0, create=True), \
             patch('app.integrations.platnik.gui_agent.subprocess.run', return_value=completed) as process:
            result = bounded_read(profile, {'nip': NIP, 'period': MONTH})
        self.assertEqual(result, {'findings': []})
        self.assertEqual(process.call_args.args[0], [
            executable, '--platnik-agent', 'read-job', '--config', str(profile),
        ])

    def test_reader_rejects_malformed_child_output(self):
        completed = Mock(stdout='[]')
        with patch('app.integrations.platnik.gui_agent.subprocess.CREATE_NO_WINDOW', 0, create=True), \
             patch('app.integrations.platnik.gui_agent.subprocess.run', return_value=completed):
            self.assertEqual(bounded_read(Path('profile.json'), {}), {'error': 'ReadFailed'})

    def test_one_shot_run_reports_unavailable_desktop(self):
        with patch('app.integrations.platnik.gui_agent.interactive_desktop', return_value=False), \
             self.assertRaisesRegex(ReadError, 'DesktopUnavailable'):
            run({}, Path('profile.json'), once=True)

    def test_manual_run_exits_after_draining_the_queue(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        jobs = [
            ('first', 'lease-1', NIP, MONTH),
            (True,),
            ('second', 'lease-2', NIP, '2026-10'),
            (True,),
            None,
        ]
        connection.execute.return_value.fetchone.side_effect = jobs
        with patch('app.integrations.platnik.gui_agent.interactive_desktop', return_value=True), \
             patch('app.integrations.platnik.gui_agent.connect', return_value=connection), \
             patch('app.integrations.platnik.gui_agent.bounded_read', return_value={'findings': []}) as read:
            result = run({'schema': 'public'}, Path('profile.json'))
        self.assertEqual(result, {'completed': 2, 'failed': 0, 'interrupted': None})
        self.assertEqual(read.call_count, 2)
        connection.__exit__.assert_called_once()

    def test_legacy_autostart_modes_leave_the_queue_untouched(self):
        from app.integrations.platnik.gui_agent import main

        with patch('app.integrations.platnik.gui_agent.sys.platform', 'win32'), \
             patch('app.integrations.platnik.gui_agent.run_pending') as process:
            main([])
            main(['run'])
        process.assert_not_called()

    def test_reader_uses_redirected_windows_handles_when_no_console_exists(self):
        from app.integrations.platnik.gui_agent import _serve_read_job

        source = StringIO('{"nip":"1234567890","period":"2026-11"}')
        destination = StringIO()
        module = 'app.integrations.platnik.gui_agent'
        with patch(f'{module}.sys.stdin', None), patch(f'{module}.sys.stdout', None), \
             patch(f'{module}._windows_pipe', side_effect=[nullcontext(source), nullcontext(destination)]) as pipe, \
             patch(f'{module}.load_config', return_value={}), patch(f'{module}.read_job', return_value=[]) as read:
            _serve_read_job(Path('profile.json'))
        self.assertEqual(pipe.call_args_list, [call(-10, 'r'), call(-11, 'w')])
        read.assert_called_once_with({}, {'nip': NIP, 'period': MONTH})
        self.assertEqual(json.loads(destination.getvalue()), {'findings': []})


class AgentDiagnosticTests(TestCase):
    def test_group_send_wizard_is_distinct_from_receipt_wizard(self):
        from app.integrations.platnik.gui_diagnostic_capture import classify_window

        title = 'Kreator grupowego wysyłania zestawów do ZUS - krok 1 / 10'
        self.assertEqual(classify_window(title), {
            'kind': 'group_send_wizard', 'step': {'current': 1, 'total': 10},
        })

    def test_test_walkthrough_reports_each_read_only_stage(self):
        from app.integrations.platnik.gui_agent import diagnose
        config = {'executable': Path('/tmp/Platnik.exe'), 'backend': 'uia', 'confirmation_download': None}
        window = Mock()
        progress = []
        with patch('app.integrations.platnik.gui_agent.sys.platform', 'win32'), \
             patch('app.integrations.platnik.gui_agent.load_config', return_value=config), \
             patch('app.integrations.platnik.gui_agent.check_queue_access', return_value=True), \
             patch('app.integrations.platnik.gui_agent.interactive_desktop', return_value=True), \
             patch('app.integrations.platnik.gui_agent.main_window', return_value=window), \
             patch('app.integrations.platnik.gui_agent.register',
                   return_value=(Mock(), 0, {'nip': 0, 'period': 1})):
            self.assertTrue(diagnose(Path('profile.json'), progress=lambda *event: progress.append(event)))
        self.assertEqual([stage for stage, state, _detail in progress if state == 'running'], [
            'Profil agenta', 'Połączenie z kolejką PostgreSQL', 'Pulpit Windows',
            'Okno Płatnika', 'Tabela deklaracji',
        ])
        self.assertEqual(progress[-1][0], 'Test zakończony')
        self.assertIn('nie wykonano importu', progress[-1][2])

    def test_test_walkthrough_stops_at_queue_failure(self):
        from app.integrations.platnik.gui_agent import diagnose
        config = {'executable': Path('/tmp/Platnik.exe'), 'backend': 'uia'}
        progress = []
        with patch('app.integrations.platnik.gui_agent.sys.platform', 'win32'), \
             patch('app.integrations.platnik.gui_agent.load_config', return_value=config), \
             patch('app.integrations.platnik.gui_agent.check_queue_access', side_effect=OSError), \
             patch('app.integrations.platnik.gui_agent.main_window') as open_window:
            with self.assertRaises(OSError):
                diagnose(Path('profile.json'), progress=lambda *event: progress.append(event))
        self.assertEqual(progress[-1][1], 'failed')
        self.assertIn('pliki libpq agenta', progress[-1][2])
        open_window.assert_not_called()
