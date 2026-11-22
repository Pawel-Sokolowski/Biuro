import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch, Mock
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
             patch('app.utils.crypto.decrypt_password',return_value='synthetic-password'):
            self.assertTrue(login_window(actor_id=1))
        controls[config['submit_selector']].click.assert_called_once()
        context.close.assert_called_once()
