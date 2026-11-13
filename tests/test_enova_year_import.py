import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from cryptography.fernet import Fernet

from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.integrations.enova.checking import import_document_year
from app.integrations.enova.document_counts import run_import

NIP = '1234567890'
SOURCE = {'nip': NIP, 'database': 'ClientA'}


def fetched(config, source, months):
    return [{'nip': source['nip'], 'miesiac': month, 'dokumenty': index,
             'pozycje_bankowe': 0, 'rodzaje': {'AMO': index}}
            for index, month in enumerate(months)]


class EnovaYearImportTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company(nip=NIP, forma_opodatkowania='KPiR')
        self.config = {'enabled': False, 'months_back': 3, 'sources': [SOURCE]}

    def snapshot(self, month='2026-11'):
        with get_conn() as conn:
            row = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',
                               (f'enova_checking_{self.company}_{month}',)).fetchone()
            return json.loads(row[0]) if row else None

    def test_full_year_without_stages_or_schedule_preserves_monthly_data_and_other_sections(self):
        previous = {'documents': {'accounting': {'OLD': 8}, 'payroll': {'Lista płac': 3}},
                    'declarations': [{'form': 'CIT-8', 'status': 'accepted', 'upo': 'REF'}],
                    'imported_at': '2026-01-01'}
        with get_conn() as conn:
            for month in range(1, 13):
                conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok,etap,dokumenty_dostarczone) "
                             "VALUES (%s,%s,%s,'Brak',1)", (self.company, f'2026-{month:02d}', 70 + month))
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)',
                         (f'enova_checking_{self.company}_2026-11', json.dumps(previous)))
            before = [dict(row) for row in conn.execute('SELECT * FROM statusy_miesieczne WHERE firma_id=%s ORDER BY miesiac', (self.company,))]
        progress = Mock()
        with patch('app.integrations.enova.checking.fetch_counts', side_effect=fetched) as remote:
            first = import_document_year(self.config, 2026, actor_id=self.admin, progress=progress)
            # An explicit rerun is independent of daily scheduler markers.
            second = import_document_year(self.config, 2026, actor_id=self.admin)
        self.assertTrue(first[0]['ok'], first)
        self.assertTrue(second[0]['ok'], second)
        self.assertEqual(len(first[0]['counts']), 12)
        self.assertEqual(remote.call_count, 2)
        self.assertEqual(remote.call_args.args[2], [f'2026-{month:02d}' for month in range(1, 13)])
        progress.assert_any_call(0, 1, 'ClientA')
        progress.assert_any_call(1, 1, 'ClientA')
        saved = self.snapshot()
        self.assertEqual(saved['documents']['accounting'], {'AMO': 10})
        self.assertEqual(saved['documents']['payroll'], previous['documents']['payroll'])
        self.assertEqual(saved['declarations'], previous['declarations'])
        self.assertEqual(self.snapshot('2026-01')['documents']['accounting'], {'AMO': 0})
        with get_conn() as conn:
            after = [dict(row) for row in conn.execute('SELECT * FROM statusy_miesieczne WHERE firma_id=%s ORDER BY miesiac', (self.company,))]
            self.assertEqual(before, after)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM monthly_document_imports').fetchone()[0], 0)
            keys = [row[0] for row in conn.execute('SELECT klucz FROM app_settings')]
            self.assertFalse(any(key.startswith(('enova_snapshot_', 'enova_document_count_checked_', 'enova_checking_checked_')) for key in keys))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM import_history WHERE import_type='enova_document_year' AND status='completed'").fetchone()[0], 2)

    def test_wrong_nip_and_missing_month_do_not_create_false_zeros_or_stop_other_company(self):
        self.make_company(nip='9999999999', forma_opodatkowania='KPiR')
        previous = {'documents': {'accounting': {'SP': 9}}}
        with get_conn() as conn:
            conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)',
                         (f'enova_checking_{self.company}_2026-11', json.dumps(previous)))
        config = {**self.config, 'sources': [SOURCE, {'nip': '9999999999', 'database': 'ClientB'}]}
        for failure in ('nip', 'missing_month'):
            def fetch(config, source, months):
                rows = fetched(config, source, months)
                if source['database'] == 'ClientA':
                    if failure == 'nip':
                        rows[0]['nip'] = '9999999999'
                    else:
                        rows.pop()
                return rows
            with patch('app.integrations.enova.checking.fetch_counts', side_effect=fetch):
                results = import_document_year(config, 2026, actor_id=self.admin)
            self.assertEqual([row['ok'] for row in results], [False, True], results)
            self.assertEqual(self.snapshot(), previous)
            self.assertIsNone(self.snapshot('2026-01'))
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM statusy_miesieczne WHERE firma_id=%s', (self.company,)).fetchone()[0], 0)

    def test_authorization_year_mapping_and_overlapping_import_are_checked_before_remote_reads(self):
        employee = self.make_employee()
        with patch('app.integrations.enova.checking.fetch_counts') as remote:
            with self.assertRaises(PermissionError):
                import_document_year(self.config, 2026, actor_id=employee)
            for year in (True, '2026', 1999, 2101):
                with self.assertRaises(ValueError):
                    import_document_year(self.config, year, actor_id=self.admin)
            with self.assertRaises(ValueError):
                import_document_year({**self.config, 'sources': []}, 2026, actor_id=self.admin)
            with get_conn() as conn:
                conn.execute('SELECT pg_advisory_xact_lock(748210)')
                with self.assertRaisesRegex(ValueError, 'już uruchomiony'):
                    import_document_year(self.config, 2026, actor_id=self.admin)
            remote.assert_not_called()

    def test_monthly_import_never_backfills_before_activation_and_preserves_manual_current_count(self):
        with get_conn() as conn:
            for month, count in (('2026-09', 0), ('2026-10', 48), ('2026-11', 77)):
                conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok,etap) VALUES (%s,%s,%s,'Wysłane wyniki')",
                             (self.company, month, count))
        config = {**self.config, 'enabled': True, 'monthly_counts_from': '2026-11'}
        remote = Mock(side_effect=fetched)
        results = run_import(config, apply=True, today=date(2026, 11, 13), fetch=remote)
        self.assertTrue(results[0]['ok'], results)
        self.assertEqual(remote.call_args.args[2], ['2026-11'])
        self.assertEqual(results[0]['manual_preserved'], ['2026-11'])
        with get_conn() as conn:
            self.assertEqual([tuple(row) for row in conn.execute('SELECT miesiac,ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s ORDER BY miesiac', (self.company,))],
                             [('2026-09', 0), ('2026-10', 48), ('2026-11', 77)])

    def test_legacy_configuration_without_activation_month_defaults_to_current_month(self):
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok,etap) VALUES (%s,'2026-10',0,'Wysłane wyniki')", (self.company,))
        remote = Mock(side_effect=fetched)
        result = run_import({**self.config, 'enabled': True}, apply=True,
                            today=date(2026, 11, 13), fetch=remote)
        self.assertTrue(result[0]['ok'], result)
        self.assertEqual(result[0]['mode'], 'skipped')
        remote.assert_not_called()

    def test_worker_button_saves_current_fields_and_submits_explicit_year_without_enabling_schedule(self):
        from app.worker_setup_ui import WorkerSetupWindow
        with patch.dict(os.environ, {'BIO_ENCRYPTION_KEY': Fernet.generate_key().decode()}):
            window = WorkerSetupWindow(self.admin)
            self.addCleanup(window.close)
            window.open_tab(4)
            page = window.opened_pages[4]
            page.server.setText('ENOVA,49848')
            page.username.setText('reader')
            page.password.setText('secret')
            page.import_year.setValue(2025)
            row = next(index for index, company in enumerate(page.companies) if company['id'] == self.company)
            page.table.item(row, 2).setText('ClientA')
            with patch.object(page.import_runner, 'submit') as submit:
                page.import_year_counts()
            self.assertFalse(page.import_button.isEnabled())
            with patch('app.integrations.enova.checking.import_document_year', return_value=[]) as run:
                submit.call_args.args[0]()
            config, year = run.call_args.args
            self.assertEqual(year, 2025)
            self.assertEqual(config['server'], 'ENOVA,49848')
            self.assertEqual(len(config['sources']), 1)
            self.assertEqual({key: config['sources'][0][key] for key in SOURCE}, SOURCE)
            self.assertEqual(Path(config['sources'][0]['payroll_query']).name, 'payroll.sql')
            self.assertFalse(config['enabled'])
            self.assertFalse(page.is_dirty())
            self.assertEqual(run.call_args.kwargs['actor_id'], self.admin)
            with patch('app.admin.settings.enova_connection_settings.QMessageBox.warning'):
                submit.call_args.args[2](ValueError('test'))
            self.assertTrue(page.import_button.isEnabled())

    def test_manual_mapping_dialog_prevents_duplicate_database_and_requires_explicit_replacement(self):
        from app.admin.settings.enova_mapping_dialog import EnovaMappingDialog
        from PyQt6.QtWidgets import QMessageBox
        companies = [{'id': 1, 'nazwa': 'A', 'nip': NIP}, {'id': 2, 'nazwa': 'B', 'nip': '9999999999'}]
        current = {1: 'Old', 2: ''}
        dialog = EnovaMappingDialog(companies, current, {'databases': [], 'matches': {}, 'errors': []})
        self.addCleanup(dialog.close)
        dialog.company.setCurrentIndex(dialog.company.findData(2))
        dialog.database.setCurrentText('old')
        with patch('app.admin.settings.enova_mapping_dialog.QMessageBox.warning') as warning:
            dialog.assign()
        warning.assert_called_once()
        self.assertEqual(dialog.mappings, current)
        dialog.database.setCurrentText('New')
        dialog.assign()
        self.assertEqual(dialog.mappings[2], 'New')
        self.assertEqual(dialog.missing.rowCount(), 0)
        dialog.company.setCurrentIndex(dialog.company.findData(1))
        dialog.database.setCurrentText('Replacement')
        with patch('app.admin.settings.enova_mapping_dialog.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            dialog.assign()
        self.assertEqual(dialog.mappings[1], 'Old')
        with patch('app.admin.settings.enova_mapping_dialog.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            dialog.assign()
        self.assertEqual(dialog.mappings[1], 'Replacement')
        self.assertEqual(current, {1: 'Old', 2: ''})  # Cancel/close cannot mutate the parent's draft.
