"""Full downloads require an explicit Settings action for permitted accounts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication,QMessageBox
from app.preferences import mail_sync


class ManualMailSyncSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def widget(self):
        widget=mail_sync.MailSyncSettings(7)
        self.addCleanup(widget.close)
        return widget

    def test_opening_settings_never_downloads_and_all_assigned_accounts_can_be_selected(self):
        accounts=[{'id':1,'email':'a@example.com'},{'id':2,'email':'b@example.com'}]
        with patch.object(mail_sync,'can',return_value=False), \
             patch.object(mail_sync,'get_email_accounts_for_employee',return_value=accounts), \
             patch.object(mail_sync,'get_all_email_accounts') as all_accounts, \
             patch.object(mail_sync,'sync_requested_accounts',return_value=[]) as download, \
             patch.object(mail_sync.threading,'Thread') as thread, \
             patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):
            thread.return_value.start.side_effect=lambda:thread.call_args.kwargs['target']()
            widget=self.widget()
            self.assertEqual(widget.accounts.count(),3)
            download.assert_not_called()
            widget.start()
            self.assertEqual(download.call_args.args[0],accounts)
            widget.accounts.setCurrentIndex(widget.accounts.findData(2))
            widget.start()
            self.assertEqual(download.call_args.args[0],[accounts[1]])
            all_accounts.assert_not_called()

    def test_administrator_can_select_every_account_and_cancel_starts_nothing(self):
        with patch.object(mail_sync,'can',return_value=True), \
             patch.object(mail_sync,'get_all_email_accounts',return_value=[{'id':3,'email':'system@example.com'}]), \
             patch.object(mail_sync,'get_email_accounts_for_employee') as assigned, \
             patch.object(mail_sync,'sync_requested_accounts') as download, \
             patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.No):
            widget=self.widget()
            self.assertEqual(widget.accounts.itemData(1),3)
            widget.start()
            download.assert_not_called()
            assigned.assert_not_called()
