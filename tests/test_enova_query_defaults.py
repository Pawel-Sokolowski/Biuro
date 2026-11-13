"""Installer-shipped defaults must also work with saved and JSON configuration."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.integrations.enova.connection_settings import load_connection
from app.integrations.enova.document_counts import load_config
from app.integrations.enova.query_defaults import with_query_defaults


class QueryDefaultsTests(TestCase):
    def test_saved_settings_resolve_bundled_payroll_without_a_selected_file(self):
        saved = {'sources': [{'nip': '1234567890', 'database': 'ClientA'}]}
        with patch('app.integrations.enova.connection_settings.get_setting', return_value=json.dumps(saved)):
            config = load_connection()
        query = Path(config['sources'][0]['payroll_query'])
        self.assertTrue(query.is_absolute())
        self.assertTrue(query.is_file())
        self.assertEqual(query.name, 'payroll.sql')
        self.assertEqual(config['payroll_query'], '')
        self.assertNotIn('declarations_query', config['sources'][0])

    def test_json_configuration_uses_bundle_independently_of_config_directory(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'enova.json'
            path.write_text(json.dumps({'server': 'sql', 'sources': [{'nip': '1234567890', 'database': 'ClientA'}]}))
            config = load_config(path)
        self.assertTrue(Path(config['sources'][0]['payroll_query']).is_file())

    def test_company_override_precedes_global_and_blank_returns_to_bundle(self):
        config = {'payroll_query': 'global.sql', 'sources': [{'payroll_query': 'company.sql'}, {}]}
        resolved = with_query_defaults(config)
        self.assertEqual([s['payroll_query'] for s in resolved['sources']], ['company.sql', 'global.sql'])
        self.assertEqual(config['sources'][1], {})
        config['payroll_query'] = ''
        config['sources'][0]['payroll_query'] = ''
        self.assertEqual(Path(with_query_defaults(config)['sources'][0]['payroll_query']).name, 'payroll.sql')
