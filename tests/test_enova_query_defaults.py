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
        self.assertEqual(Path(config['sources'][0]['declarations_query']).name, 'declarations.sql')
        self.assertTrue(Path(config['sources'][0]['jpk_query']).is_file())

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

    def test_tax_overrides_resolve_per_source_and_do_not_mutate_saved_config(self):
        config = {'jpk_query': 'shared-jpk.sql', 'declarations_query': 'shared-tax.sql',
                  'sources': [{'jpk_query': 'client-jpk.sql', 'declarations_query': 'client-tax.sql'}, {}]}
        resolved = with_query_defaults(config)['sources']
        self.assertEqual(resolved[0]['jpk_query'], 'client-jpk.sql')
        self.assertEqual(resolved[0]['declarations_query'], 'client-tax.sql')
        self.assertEqual(resolved[1]['jpk_query'], 'shared-jpk.sql')
        self.assertEqual(resolved[1]['declarations_query'], 'shared-tax.sql')
        self.assertEqual(config['sources'][1], {})

    def test_desktop_resolved_bundle_is_rebased_on_worker_and_never_saved_as_override(self):
        from app.integrations.enova.query_defaults import saved_source
        raw = {'sources': [{'nip': '1234567890', 'database': 'ClientA'}]}
        with patch('app.integrations.enova.query_defaults.bundled_directory', return_value=Path('/desktop/install/queries')):
            desktop = with_query_defaults(raw)
        self.assertEqual(saved_source(desktop['sources'][0]), raw['sources'][0])
        with patch('app.integrations.enova.query_defaults.bundled_directory', return_value=Path('/worker/install/queries')):
            worker = with_query_defaults(desktop)
        self.assertEqual(Path(worker['sources'][0]['jpk_query']), Path('/worker/install/queries/jpk.sql'))
        self.assertEqual(Path(worker['sources'][0]['declarations_query']), Path('/worker/install/queries/declarations.sql'))

    def test_shared_override_keeps_inheriting_after_reload_but_real_company_override_survives(self):
        from app.integrations.enova.query_defaults import saved_source
        config = with_query_defaults({'jpk_query': 'first.sql', 'sources': [{}, {'jpk_query': 'client.sql'}]})
        config['jpk_query'] = 'second.sql'
        reloaded = with_query_defaults(config)
        self.assertEqual([row['jpk_query'] for row in reloaded['sources']], ['second.sql', 'client.sql'])
        self.assertNotIn('jpk_query', saved_source(reloaded['sources'][0]))
        reloaded['sources'][0]['jpk_query'] = 'edited-client.sql'
        self.assertEqual(saved_source(reloaded['sources'][0])['jpk_query'], 'edited-client.sql')
