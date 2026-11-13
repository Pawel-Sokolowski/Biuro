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
        config = {**DEFAULTS,'username_selector':'#existing'}
        with patch('app.security.authorization.is_administrator',return_value=True), \
             patch('app.integrations.saldeo_web.settings.load',return_value=config), \
             patch('app.db.dao_app_settings.set_setting') as save:
            self.assertTrue(remember_login_fields(page,1))
            import json
            value = json.loads(save.call_args.args[1])
            self.assertEqual(value['username_selector'],'#existing')
            self.assertEqual(value['password_selector'],'input[type="password"]')
            self.assertFalse(value['enabled'])
            self.assertNotIn('input_value',repr(page.mock_calls))
        page.locator.return_value.count.return_value = 2
        with patch('app.db.dao_app_settings.set_setting') as save:
            self.assertFalse(remember_login_fields(page,1))
            save.assert_not_called()
