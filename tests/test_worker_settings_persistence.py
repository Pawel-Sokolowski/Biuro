import os
from types import SimpleNamespace
from unittest.mock import patch
from cryptography.fernet import Fernet

from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting
from app.worker_setup_ui import WorkerSetupWindow
from app.admin.settings.settings_ui import GlobalSettingsPage
from app.integrations.enova.connection_settings import load_connection,credentials
from app.integrations.platnik.holidays import load as load_platnik
from app.services.worker_settings import load_settings


class WorkerSettingsPersistenceTests(DbTestCase):
    def setUp(self):
        super().setUp()
        key = patch.dict(os.environ,{'BIO_ENCRYPTION_KEY':Fernet.generate_key().decode()})
        key.start()
        self.addCleanup(key.stop)
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]

    def worker(self):
        window = WorkerSetupWindow(self.admin)
        self.addCleanup(window.close)
        return window

    def page(self,settings,label):
        row = next(i for i in range(settings.nav.count()) if settings.nav.item(i).text().strip()==label)
        settings.nav.setCurrentRow(row)
        return settings.stack.currentWidget().widget()

    def test_install_persists_open_sql_forms_and_reopened_desktop_uses_same_settings(self):
        desktop = GlobalSettingsPage(self.admin,True)
        self.addCleanup(desktop.close)
        desktop_enova = self.page(desktop,'Enova — połączenie')
        desktop_platnik = self.page(desktop,'Płatnik — połączenie')
        self.assertEqual(desktop_enova.server.text(),'')
        self.assertEqual(desktop_platnik.fields['server'].text(),'')
        window = self.worker()
        window.open_tab(4)
        enova = window.opened_pages[4]
        enova.server.setText('ENOVA,49848')
        enova.database.setText('Biuro_rachunkowe')
        enova.username.setText('reader')
        enova.password.setText('enova-secret')
        enova.enabled.setChecked(True)
        enova.discovery_timeout.setValue(1200)
        window.open_tab(10)
        platnik = window.opened_pages[10]
        platnik.fields['server'].setText('PLATNIK-SRV,1433')
        platnik.fields['username'].setText('reader')
        platnik.password.setText('platnik-secret')
        platnik.query_timeout.setValue(1200)
        window.open_tab(2)
        window.opened_pages[2].enabled.setChecked(True)
        with patch('app.worker_setup_ui.os',SimpleNamespace(name='nt')), \
             patch('app.worker_setup_ui.sys.frozen',True,create=True), \
             patch.object(window.runner,'submit') as submit:
            window.install()
        submit.assert_called_once()  # Preflight is requested only after successful persistence.
        self.assertEqual(load_connection()['server'],'ENOVA,49848')
        self.assertEqual(load_connection()['discovery_query_timeout'],1200)
        self.assertEqual(credentials(load_connection()),('reader','enova-secret'))
        self.assertEqual(load_platnik()['server'],'PLATNIK-SRV,1433')
        self.assertEqual(load_platnik()['query_timeout'],1200)
        self.assertTrue(load_settings()['enabled'])
        from app.utils.crypto import decrypt_password
        self.assertEqual(decrypt_password(load_platnik()['password_enc']),'platnik-secret')
        self.assertIs(self.page(desktop,'Enova — połączenie'),desktop_enova)
        self.assertEqual(desktop_enova.server.text(),'ENOVA,49848')
        self.assertEqual(desktop_enova.discovery_timeout.value(),1200)
        self.assertIs(self.page(desktop,'Płatnik — połączenie'),desktop_platnik)
        self.assertEqual(desktop_platnik.fields['server'].text(),'PLATNIK-SRV,1433')
        self.assertEqual(desktop_platnik.query_timeout.value(),1200)
        self.assertEqual(desktop_enova.password.text(),'')
        self.assertNotIn('enova-secret',get_setting('enova_connection'))
        self.assertNotIn('platnik-secret',get_setting('platnik_connection'))
        self.assertIn(self.schema_name,desktop.database_info.text())
        self.assertIn(self.schema_name,window.runtime_info.text())
        reopened = self.worker()
        reopened.open_tab(4)
        self.assertEqual(reopened.opened_pages[4].server.text(),'ENOVA,49848')
        reopened.open_tab(10)
        self.assertEqual(reopened.opened_pages[10].fields['server'].text(),'PLATNIK-SRV,1433')

    def test_untouched_tabs_are_not_created_or_saved_and_invalid_form_blocks_install(self):
        window = self.worker()
        self.assertTrue(window.save_opened_settings())
        self.assertIsNone(get_setting('enova_connection'))
        self.assertIsNone(get_setting('platnik_connection'))
        window.open_tab(10)
        window.opened_pages[10].fields['server'].setText('PLATNIK-SRV,1433')
        with patch('app.worker_setup_ui.os',SimpleNamespace(name='nt')), \
             patch('app.worker_setup_ui.sys.frozen',True,create=True), \
             patch('app.worker_setup_ui.QMessageBox.warning'), \
             patch.object(window.runner,'submit') as submit:
            window.install()
        submit.assert_not_called()
        self.assertEqual(window.tabs.currentIndex(),10)
        self.assertIsNone(get_setting('platnik_connection'))

    def test_refresh_preserves_unsaved_changes_and_import_requires_company_mapping(self):
        from app.integrations.enova.document_counts import run_import
        window = self.worker()
        window.open_tab(4)
        page = window.opened_pages[4]
        page.server.setText('ENOVA,49848')
        page.username.setText('reader')
        page.password.setText('secret')
        page.enabled.setChecked(True)
        page.persist()
        self.assertTrue(load_connection()['enabled'])
        with self.assertRaisesRegex(ValueError,'Przed importem'):
            run_import(load_connection(),apply=True)
        page.server.setText('draft-address')
        page.reload_saved()
        self.assertEqual(page.server.text(),'draft-address')
        self.assertEqual(load_connection()['server'],'ENOVA,49848')
