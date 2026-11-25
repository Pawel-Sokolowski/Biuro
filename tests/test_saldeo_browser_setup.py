import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch, Mock, MagicMock
from app.integrations.saldeo_web.executable import resolve
from app.integrations.saldeo_web.browser import remember_login_fields
from app.integrations.saldeo_web.settings import DEFAULTS


class BrowserSetupTests(TestCase):
    def test_per_user_chrome_is_found_without_playwright_channel_detection(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ,{'LOCALAPPDATA':directory}), \
             patch('app.integrations.saldeo_web.executable.registry_candidates',return_value=[]):
            path = Path(directory)/'Google/Chrome/Application/chrome.exe'
            path.parent.mkdir(parents=True)
            path.touch()
            self.assertEqual(resolve({'channel':'chrome'}),str(path))

    def test_explicit_browser_path_and_registry_path_are_supported(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'chrome.exe'
            path.touch()
            self.assertEqual(resolve({'channel':'msedge','executable_path':str(path)}),str(path))
            with patch('app.integrations.saldeo_web.executable.registry_candidates',return_value=[path]):
                self.assertEqual(resolve({'channel':'chrome'}),str(path))
        with self.assertRaisesRegex(ValueError,'Wskazany plik'):
            resolve({'executable_path':'not-a-browser.exe'})

    def test_only_unique_controls_are_learned_and_existing_fields_are_preserved(self):
        page = Mock()
        page.locator.return_value.count.return_value = 1
        config = {**DEFAULTS,'username_selector':'#existing','submit_selector':''}
        with patch('app.security.authorization.is_administrator',return_value=True), \
             patch('app.integrations.saldeo_web.settings.load',return_value=config), \
             patch('app.db.dao_app_settings.set_setting') as save:
            self.assertTrue(remember_login_fields(page,1))
            import json
            value = json.loads(save.call_args.args[1])
            self.assertEqual(value['username_selector'],'#existing')
            self.assertEqual(value['password_selector'],'input[qa-id="password"]')
            self.assertEqual(value['submit_selector'],'button[qa-id="login-button"]')
            self.assertFalse(value['enabled'])
            self.assertNotIn('input_value',repr(page.mock_calls))
        page.locator.return_value.count.return_value = 2
        with patch('app.db.dao_app_settings.set_setting') as save:
            self.assertFalse(remember_login_fields(page,1))
            save.assert_not_called()


class HistoryReadTests(TestCase):
    def run_history(self, *, empty_marker=False):
        from types import SimpleNamespace
        from app.integrations.saldeo_web.browser import read_history
        from app.integrations.saldeo_web.settings import DEFAULTS

        page = Mock()
        row_locator = Mock()
        row_locator.filter.return_value = row_locator
        row_locator.count.return_value = 0
        row_locator.evaluate_all.return_value = []
        empty_locator = Mock()
        empty_locator.filter.return_value = empty_locator
        empty_locator.count.return_value = int(empty_marker)
        marker = Mock()
        paginator = Mock()
        page.locator.side_effect = lambda selector: {
            DEFAULTS['logged_in_selector']: marker,
            DEFAULTS['item_selector']: row_locator,
            'input[qa-id="paginatorPage"]': paginator,
            '#empty': empty_locator,
        }[selector]
        context = Mock()
        context.pages = [page]
        playwright = MagicMock()
        playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value = context
        config = {**DEFAULTS, 'enabled': True, 'empty_selector': '#empty' if empty_marker else ''}
        updates = []
        with patch.dict('sys.modules', {
            'playwright.sync_api': SimpleNamespace(sync_playwright=playwright),
        }), patch('app.integrations.saldeo_web.browser.open_authenticated_history', return_value=True), \
             patch('app.integrations.saldeo_web.settings.validate'), \
             patch('app.integrations.saldeo_web.browser.profile_path', return_value='profile'), \
             patch('app.integrations.saldeo_web.browser.resolve_browser', return_value='browser.exe'):
            result = read_history(config, progress=lambda stage, counts: updates.append((stage, counts)))
        return result, updates, context

    def test_unmatched_row_selector_is_reported_as_partial_not_empty_success(self):
        result, updates, context = self.run_history()
        self.assertEqual((result['state'], result['reason'], result['rows_seen']), ('partial', 'rows_not_found', 0))
        self.assertEqual(updates[-1][0], 'rows_not_found')
        context.close.assert_called_once()

    def test_configured_empty_history_marker_allows_confirmed_empty_result(self):
        result, _updates, _context = self.run_history(empty_marker=True)
        self.assertEqual((result['state'], result['pages'], result['rows_seen']), ('ok', 1, 0))

    def test_missing_paginator_is_not_treated_as_a_complete_history(self):
        from app.integrations.saldeo_web.browser import _next_page
        page = Mock()
        page.locator.return_value.first.count.return_value = 0
        self.assertIsNone(_next_page(page, {'next_selector': '#next', 'item_selector': '.row'}, []))

    def test_failed_page_change_is_not_mistaken_for_the_last_page(self):
        from app.integrations.saldeo_web.browser import _next_page
        page = Mock()
        control = page.locator.return_value.first
        control.count.return_value = 1
        control.is_visible.return_value = True
        control.evaluate.return_value = 'input'
        control.get_attribute.return_value = '5'
        control.input_value.return_value = '1'
        page.locator.return_value.all_inner_texts.return_value = ['same row']
        page.wait_for_function.side_effect = TimeoutError('page did not change')
        with self.assertRaisesRegex(TimeoutError, 'page did not change'):
            _next_page(page, {'next_selector': '#page', 'item_selector': '.row'}, [])


class SavedLoginTests(TestCase):
    def config(self):
        return {**DEFAULTS,'username':'synthetic-user','password_enc':'encrypted-test-value',
                'submit_selector':'#submit'}

    def page(self,config):
        page = Mock()
        page.url = config['login_url']
        controls = {}
        for key in ('username_selector','password_selector','submit_selector'):
            control = Mock()
            control.count.return_value = 1
            control.is_visible.return_value = True
            controls[config[key]] = control
        page.locator.side_effect = controls.__getitem__
        return page,controls

    def test_legacy_public_login_defaults_migrate_and_custom_selectors_survive(self):
        from app.integrations.saldeo_web.settings import load
        with patch('app.integrations.saldeo_web.settings.get_setting',
                   return_value='{"login_url":"https://saldeo.brainshare.pl/","submit_selector":""}'):
            config = load()
        self.assertEqual(config['login_url'], 'https://saldeo.brainshare.pl/login/login.hta')
        self.assertEqual(config['submit_selector'], 'button[qa-id="login-button"]')
        with patch('app.integrations.saldeo_web.settings.get_setting',
                   return_value='{"submit_selector":"#custom"}'):
            self.assertEqual(load()['submit_selector'], '#custom')

    def test_legacy_bell_preset_moves_to_the_full_operations_table(self):
        from app.integrations.saldeo_web.settings import LEGACY_ITEM_SELECTOR, OPERATIONS_NEXT, OPERATIONS_ROWS, SESSION_SELECTOR, load
        import json
        saved = {'item_selector': LEGACY_ITEM_SELECTOR, 'open_selector': SESSION_SELECTOR,
                 'next_selector': '', 'empty_selector': ''}
        with patch('app.integrations.saldeo_web.settings.get_setting', return_value=json.dumps(saved)):
            config = load()
        self.assertEqual(config['item_selector'], OPERATIONS_ROWS)
        self.assertEqual(config['next_selector'], OPERATIONS_NEXT)
        self.assertEqual(config['open_selector'], '')
        with patch('app.integrations.saldeo_web.settings.get_setting',
                   return_value=json.dumps({**saved, 'next_selector': '#custom-next'})):
            config = load()
        self.assertEqual(config['item_selector'], LEGACY_ITEM_SELECTOR)
        self.assertEqual(config['next_selector'], '#custom-next')

    def test_saved_navigation_next_selector_moves_to_the_page_number_input(self):
        from app.integrations.saldeo_web.settings import (OPERATIONS_NEXT, OPERATIONS_NEXT_LEGACY, OPERATIONS_ROWS,
                                                          load)
        import json
        # An installation saved before the fix holds the removed next/prev selector; it
        # matched nothing, so every run ended partial. It must be migrated, not kept.
        saved = {'item_selector': OPERATIONS_ROWS, 'open_selector': '', 'next_selector': OPERATIONS_NEXT_LEGACY}
        with patch('app.integrations.saldeo_web.settings.get_setting', return_value=json.dumps(saved)):
            config = load()
        self.assertEqual(config['next_selector'], OPERATIONS_NEXT)
        self.assertEqual(config['item_selector'], OPERATIONS_ROWS)

    def test_history_reauthenticates_once_and_reopens_the_history(self):
        from unittest.mock import call
        from app.integrations.saldeo_web.browser import open_authenticated_history
        config = self.config()
        page = Mock(url=config['login_url'])
        marker, login = Mock(), Mock()
        marker.is_visible.return_value = False
        page.locator.side_effect = lambda selector: (marker if selector == config['logged_in_selector'] else login)
        marker.filter.return_value.first = marker
        login.filter.return_value.first = login
        with patch('app.integrations.saldeo_web.browser.attempt_saved_login', return_value=True) as submit:
            self.assertTrue(open_authenticated_history(page, config))
        submit.assert_called_once_with(page, config)
        self.assertEqual(page.goto.call_args_list, [
            call(url, wait_until='domcontentloaded', timeout=45000)
            for url in (config['history_url'], config['login_url'], config['history_url'])
        ])

    def test_history_does_not_submit_credentials_for_a_foreign_redirect(self):
        from app.integrations.saldeo_web.browser import open_authenticated_history
        page = Mock(url='https://different.example.com/')
        with patch('app.integrations.saldeo_web.browser.attempt_saved_login') as submit:
            self.assertFalse(open_authenticated_history(page, self.config()))
        submit.assert_not_called()

    def test_saved_credentials_are_submitted_only_to_the_configured_origin(self):
        from app.integrations.saldeo_web.browser import attempt_saved_login
        config = self.config()
        page,controls = self.page(config)
        with patch('app.utils.crypto.decrypt_password',return_value='synthetic-password') as decrypt:
            self.assertTrue(attempt_saved_login(page,config))
        controls[config['username_selector']].fill.assert_called_once_with('synthetic-user')
        controls[config['password_selector']].fill.assert_called_once_with('synthetic-password')
        controls[config['submit_selector']].click.assert_called_once()
        self.assertEqual(decrypt.call_count,1)
        page.url = 'https://different.example.com/'
        with patch('app.utils.crypto.decrypt_password') as decrypt:
            self.assertFalse(attempt_saved_login(page,config))
        decrypt.assert_not_called()

    def test_missing_or_ambiguous_controls_leave_manual_login_available(self):
        from app.integrations.saldeo_web.browser import attempt_saved_login
        config = self.config()
        page,controls = self.page(config)
        controls[config['password_selector']].count.return_value = 2
        with patch('app.utils.crypto.decrypt_password') as decrypt:
            self.assertFalse(attempt_saved_login(page,config))
            self.assertFalse(attempt_saved_login(page,{**config,'password_enc':''}))
        decrypt.assert_not_called()
        controls[config['submit_selector']].click.assert_not_called()

    def test_interactive_window_submits_once_and_confirms_session_before_closing(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from app.integrations.saldeo_web.browser import login_window
        config = self.config()
        page,controls = self.page(config)
        marker = Mock()
        marker.first.is_visible.side_effect = [False,True]
        page.locator.side_effect = lambda selector: marker if selector==config['logged_in_selector'] else controls[selector]
        context = Mock()
        context.pages = [page]
        playwright = MagicMock()
        playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value = context
        lock = MagicMock()
        lock.return_value.__enter__.return_value = True
        with patch.dict('sys.modules',{'playwright.sync_api':SimpleNamespace(sync_playwright=playwright)}), \
             patch('app.integrations.saldeo_web.browser.browser_lock',lock), \
             patch('app.integrations.saldeo_web.browser.profile_path',return_value='synthetic-profile'), \
             patch('app.integrations.saldeo_web.browser.resolve_browser',return_value='browser.exe'), \
             patch('app.integrations.saldeo_web.settings.load',return_value=config), \
             patch('app.integrations.saldeo_web.browser.remember_login_fields',return_value=True), \
             patch('app.integrations.saldeo_web.browser._confirm_history',return_value=True) as confirm, \
             patch('app.utils.crypto.decrypt_password',return_value='synthetic-password'):
            self.assertTrue(login_window(actor_id=1))
        # The header marker is confirmed by opening the operations list before reporting success.
        self.assertEqual(confirm.call_count, 1)
        controls[config['submit_selector']].click.assert_called_once()
        context.close.assert_called_once()
