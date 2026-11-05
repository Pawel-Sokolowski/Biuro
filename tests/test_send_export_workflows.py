"""UI actions capture their request before dialogs and run network/file work off-thread."""
import tempfile
import threading
from pathlib import Path
from unittest import mock

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QTextBrowser

from app.email import email_ui
from app.reports import reports_ui
from tests.test_background_workflows import wait
from tests.test_review_followups import MailboxFixture

_app = QApplication.instance() or QApplication([])


class SendAndExportWorkflowTests(MailboxFixture):
    def setUp(self):
        super().setUp()
        for name in ('information', 'warning', 'critical'):
            patcher = mock.patch.object(QMessageBox, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def page(self):
        with mock.patch.object(email_ui.EmailPage, 'start_background_sync'), \
                mock.patch.object(email_ui, 'MailBrowser', QTextBrowser):
            page = email_ui.EmailPage(1)
        self.addCleanup(page.deleteLater)
        page.accounts = [{'id': 1, 'email': 'office@example.com'}]
        page.current_account = page.accounts[0]
        return page

    def test_send_runs_off_thread_and_captures_the_accepted_draft(self):
        page = self.page()
        entered, release, sent = threading.Event(), threading.Event(), []
        main_thread = threading.get_ident()
        draft = {'account_id': 1, 'to': ['client@example.com'], 'cc': [], 'bcc': [],
                 'subject': 'Accepted subject', 'body': 'Body', 'html': '', 'attachments': []}
        dialog = mock.Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.values.return_value = draft
        def send(*args, **kwargs):
            entered.set()
            release.wait(3)
            sent.append((threading.get_ident(), args, kwargs))
            return True, 'OK'
        with mock.patch.object(email_ui, 'ComposeDialog', return_value=dialog), \
                mock.patch('app.email.signature.choose_sender', return_value=page.current_account), \
                mock.patch.object(email_ui, 'send_email_smtp', side_effect=send):
            self.assertTrue(page._open_compose('Compose', copy_folder='Sent'))
            self.assertTrue(entered.wait(1))
            page.current_account = {'id': 99}
            self.assertFalse(QMessageBox.information.called)
            release.set()
            self.assertTrue(wait(lambda: QMessageBox.information.called))
        self.assertEqual(len(sent), 1)
        self.assertNotEqual(sent[0][0], main_thread)
        self.assertEqual(sent[0][1][:3], (1, ['client@example.com'], 'Accepted subject'))
        self.assertEqual(sent[0][2]['copy_folder'], 'Sent')

    def test_export_queries_and_writes_off_thread_with_captured_controls(self):
        page = reports_ui.ReportsPage(1)
        self.addCleanup(page.deleteLater)
        company = self.make_company('Export company')
        # Populate the existing page after creating the fixture company.
        page.firm_list.clear()
        entry = reports_ui.QListWidgetItem('Export company')
        entry.setData(reports_ui.Qt.ItemDataRole.UserRole, company)
        page.firm_list.addItem(entry)
        entry.setSelected(True)
        page.mode_combo.setCurrentText(reports_ui.PROFIT_MODE)
        entered, release, calls = threading.Event(), threading.Event(), []
        main_thread = threading.get_ident()
        def query(selected, months, actor):
            entered.set()
            release.wait(3)
            calls.append((threading.get_ident(), selected, months, actor))
            return []
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'report.xlsx')
            with mock.patch.object(reports_ui.QFileDialog, 'getSaveFileName', return_value=(path, '')), \
                    mock.patch('app.reports.import_dao.build_profitability_report', side_effect=query):
                page._export_to_excel(background=True)
                self.assertTrue(entered.wait(1))
                page.firm_list.clearSelection()
                self.assertTrue(page._export_busy)
                release.set()
                self.assertTrue(wait(lambda: not page._export_busy))
            self.assertTrue(Path(path).is_file())
        self.assertNotEqual(calls[0][0], main_thread)
        self.assertEqual(calls[0][1], [company])
        self.assertEqual(calls[0][3], 1)
        QMessageBox.critical.assert_not_called()
