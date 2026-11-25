"""Profile editor writes a valid agent profile without hand-editing JSON."""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtWidgets import QApplication


class ProfileEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['test'])

    def page(self, directory):
        from app.admin.settings.platnik_agent_profile import PlatnikAgentProfile
        with patch('app.integrations.platnik.gui_agent.default_profile',
                   return_value=Path(directory) / 'platnik-agent.json'):
            return PlatnikAgentProfile()

    def test_profile_is_written_and_reread(self):
        from app.integrations.platnik.gui_agent import load_config

        with TemporaryDirectory() as directory:
            page = self.page(directory)
            executable = Path(directory) / 'Płatnik.exe'
            executable.touch()
            page.executable.setText(str(executable))
            page.title.setText('Płatnik 10.02.002')
            page.backend.setCurrentText('win32')
            page.grid.setText('registry-grid')
            for index, (name, (index_field, header_field)) in enumerate(page.columns.items()):
                index_field.setText(str(index))
                header_field.setText(name.upper())
            page.status_labels.setPlainText('Wysłany = sent')
            with patch('app.admin.settings.platnik_agent_profile.QMessageBox'):
                page.save()
            saved = json.loads(page.path.read_text(encoding='utf-8'))
            self.assertEqual(saved['executable'], str(executable))
            self.assertEqual(saved['grid'], {'class_name': 'registry-grid'})
            self.assertEqual(saved['columns']['nip'], {'index': 0, 'header': 'NIP'})
            self.assertEqual(saved['status_labels'], {'Wysłany': 'sent'})
            self.assertEqual(load_config(page.path)['grid'], saved['grid'])
            reopened = self.page(directory)
            self.assertEqual(reopened.grid.text(), 'registry-grid')

    def test_incomplete_profile_is_refused(self):
        with TemporaryDirectory() as directory:
            page = self.page(directory)
            executable = Path(directory) / 'Platnik.exe'
            executable.touch()
            page.executable.setText(str(executable))
            page.title.setText('Płatnik')
            page.grid.setText('registry-grid')
            page.columns['nip'][0].setText('0')
            page.columns['nip'][1].setText('NIP')
            page.status_labels.setPlainText('Wysłany = sent')
            with patch('app.admin.settings.platnik_agent_profile.QMessageBox') as box:
                page.save()
            self.assertTrue(box.warning.called)
            self.assertFalse((Path(directory) / 'platnik-agent.json').exists())

    def test_win32_control_id_is_saved_as_an_integer_locator(self):
        from app.integrations.platnik.gui_agent import load_config

        with TemporaryDirectory() as directory:
            page = self.page(directory)
            executable = Path(directory) / 'Platnik.exe'
            executable.touch()
            page.executable.setText(str(executable))
            page.title.setText('Płatnik')
            page.backend.setCurrentText('win32')
            page.grid.setText('42')
            for index, (name, (index_field, header_field)) in enumerate(page.columns.items()):
                index_field.setText(str(index))
                header_field.setText(name.upper())
            page.status_labels.setPlainText('Wysłany = sent')
            with patch('app.admin.settings.platnik_agent_profile.QMessageBox'):
                page.save()
            saved = json.loads(page.path.read_text(encoding='utf-8'))
            self.assertEqual(saved['grid'], {'control_id': 42})
            self.assertEqual(load_config(page.path)['grid'], {'control_id': 42})

    def test_describe_uses_window_without_validating_columns(self):
        with TemporaryDirectory() as directory:
            page = self.page(directory)
            executable = Path(directory) / 'Platnik.exe'
            executable.touch()
            page.executable.setText(str(executable))
            page.title.setText('Płatnik')
            page.columns['nip'][0].setText('incomplete')
            with patch('app.integrations.platnik.gui_desktop.main_window') as main_window, patch(
                'app.integrations.platnik.gui_desktop.describe',
                return_value=[{'control_type': 'Table', 'class_name': 'List', 'auto_id': 'grid', 'control_id': 42}],
            ) as describe:
                page.describe()
            main_window.assert_called_once_with({'executable': executable.resolve(), 'backend': 'uia',
                                                 'window': {'title': 'Płatnik'}})
            describe.assert_called_once_with(main_window.return_value)
            self.assertIn('auto_id=grid', page.result.toPlainText())
