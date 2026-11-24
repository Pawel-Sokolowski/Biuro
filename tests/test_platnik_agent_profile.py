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
        with TemporaryDirectory() as directory:
            page = self.page(directory)
            page.executable.setText(r'C:\Platnik\Płatnik.exe')
            page.title.setText('Płatnik 10.02.002')
            page.grid.setText('registry-grid')
            page.columns['nip'][0].setText('0')
            page.columns['nip'][1].setText('NIP')
            page.status_labels.setPlainText('Wysłany = sent')
            with patch('app.admin.settings.platnik_agent_profile.QMessageBox'):
                page.save()
            saved = json.loads((Path(directory) / 'platnik-agent.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['executable'], r'C:\Platnik\Płatnik.exe')
            self.assertEqual(saved['grid'], {'auto_id': 'registry-grid'})
            self.assertEqual(saved['columns']['nip'], {'index': 0, 'header': 'NIP'})
            self.assertEqual(saved['status_labels'], {'Wysłany': 'sent'})

    def test_incomplete_profile_is_refused(self):
        with TemporaryDirectory() as directory:
            page = self.page(directory)
            with patch('app.admin.settings.platnik_agent_profile.QMessageBox') as box:
                page.save()
            self.assertTrue(box.warning.called)
            self.assertFalse((Path(directory) / 'platnik-agent.json').exists())
