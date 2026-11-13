import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.admin.settings.enova_connection_settings import EnovaConnectionSettings


class EnovaSchemaExportTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company(nip='1234567890')

    def page(self):
        page = EnovaConnectionSettings(self.admin)
        self.addCleanup(page.close)
        page.server.setText('ENOVA,49848')
        page.username.setText('reader')
        page.password.setText('secret')
        page.discovery_timeout.setValue(1200)
        row = next(index for index,company in enumerate(page.companies) if company['id']==self.company)
        page.table.item(row,2).setText('ClientA')
        page.table.setCurrentCell(row,2)
        return page

    def test_export_reports_progress_writes_file_in_background_and_restores_button(self):
        page = self.page()
        with TemporaryDirectory() as directory:
            destination = Path(directory)/'schema.json'
            with patch('app.admin.settings.enova_connection_settings.QFileDialog.getSaveFileName',return_value=(str(destination),'JSON')), \
                 patch.object(page.schema_runner,'submit') as submit:
                page.export_schema()
            self.assertFalse(page.schema_button.isEnabled())
            self.assertIn('ClientA',page.schema_status.text())
            payload = {'database':'ClientA','columns':[{'column_name':'Status'}],'foreign_keys':[]}
            def inspect(config,database,*,password,progress):
                self.assertEqual(config['discovery_query_timeout'],1200)
                self.assertEqual(database,'ClientA')
                self.assertEqual(password,'secret')
                progress('Odczyt tabel i kolumn…')
                return payload
            with patch('app.integrations.enova.schema_probe.inspect_schema',side_effect=inspect):
                result = submit.call_args.args[0]()
            self.assertEqual(json.loads(destination.read_text(encoding='utf-8')),payload)
            with patch('app.admin.settings.enova_connection_settings.QMessageBox.information'):
                submit.call_args.args[1](result)
            self.assertTrue(page.schema_button.isEnabled())
            self.assertIn(str(destination),page.schema_status.text())
            self.assertFalse(page._schema_running)

    def test_failed_metadata_read_leaves_existing_file_and_displays_error(self):
        page = self.page()
        with TemporaryDirectory() as directory:
            destination = Path(directory)/'schema.json'
            destination.write_text('previous export',encoding='utf-8')
            with patch('app.admin.settings.enova_connection_settings.QFileDialog.getSaveFileName',return_value=(str(destination),'JSON')), \
                 patch.object(page.schema_runner,'submit') as submit:
                page.export_schema()
            with patch('app.integrations.enova.schema_probe.inspect_schema',side_effect=ValueError('SQL: limit zapytania')):
                with self.assertRaises(ValueError) as error:
                    submit.call_args.args[0]()
            with patch('app.admin.settings.enova_connection_settings.QMessageBox.warning'):
                submit.call_args.args[2](error.exception)
            self.assertEqual(destination.read_text(encoding='utf-8'),'previous export')
            self.assertTrue(page.schema_button.isEnabled())
            self.assertIn('limit zapytania',page.schema_status.text())
