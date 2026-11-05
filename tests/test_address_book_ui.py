import threading
import types
from email.message import EmailMessage
from unittest import mock

from PyQt6.QtCore import Qt, QUrl, QObject, pyqtSignal
from PyQt6.QtGui import QStandardItem
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QTextBrowser

from app.db.dao_address_book import find_contact, import_contacts, save_contact
from app.email import email_ui, address_book_ui
from app.email.address_book import contact_mailbox
from app.email.address_book_ui import AddressBookDialog, ContactDialog
from app.email.compose_dialog import ComposeDialog, split_addresses
from app.email.recipient_edit import RecipientEdit
from tests.base import DbTestCase
from tests.test_review_optimizations import wait_until

_app = QApplication.instance() or QApplication(['address-book-ui'])


class ContactUiTests(DbTestCase):
    def test_unresolved_contact_name_cannot_be_sent_as_an_address(self):
        dialog = ComposeDialog(pracownik_id=1, to='Jan Kowalski')
        self.addCleanup(dialog.close)
        with mock.patch.object(QMessageBox, 'warning') as warning:
            dialog._accept()
        warning.assert_called_once()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_editing_the_query_hides_old_recipient_suggestions_immediately(self):
        edit = RecipientEdit(1, 'Jan')
        self.addCleanup(edit.close)
        edit.show()
        edit.activateWindow()
        edit.setFocus()
        _app.processEvents()
        row = QStandardItem('Old result')
        row.setData('old@example.com', Qt.ItemDataRole.UserRole)
        edit._model.appendRow(row)
        edit._completer.setCompletionPrefix('')
        edit._completer.complete()
        self.assertTrue(edit._completer.popup().isVisible())
        edit.setCursorPosition(3)
        QTest.keyClicks(edit, 'x')
        self.assertFalse(edit._completer.popup().isVisible())
        edit._timer.stop()

    def test_save_form_persists_all_details_for_another_employee(self):
        other = self.make_employee('other@example.com')
        dialog = ContactDialog(1, initial={'display_name': 'Jan Kowalski', 'email': 'jan@example.com'})
        self.addCleanup(dialog.close)
        dialog.fields['additional_email'].setText('extra@example.com')
        dialog.fields['home_city'].setText('Łódź')
        dialog.fields['notes'].setPlainText('Notatka\nDruga linia')
        dialog._save()
        self.assertTrue(wait_until(lambda: dialog.result() == QDialog.DialogCode.Accepted))
        self.assertEqual(find_contact(other, 'extra@example.com')['home_city'], 'Łódź')
        self.assertEqual(find_contact(other, 'jan@example.com')['notes'], 'Notatka\nDruga linia')

    def test_completion_replaces_only_the_current_recipient_and_handles_quoted_names(self):
        edit = RecipientEdit(1, 'first@example.com; Jan; last@example.com')
        self.addCleanup(edit.close)
        edit.setCursorPosition(edit.text().index('Jan') + 3)
        row = QStandardItem('Jan')
        row.setData(contact_mailbox({'display_name': 'Kowalski, Jan', 'email': 'jan@example.com'}), Qt.ItemDataRole.UserRole)
        edit._model.appendRow(row)
        edit._choose(edit._model.index(0, 0))
        self.assertEqual(split_addresses(edit.text()), ['first@example.com', 'jan@example.com', 'last@example.com'])
        self.assertIn('"Kowalski, Jan"', edit.text())

    def test_return_selects_a_suggestion_without_accepting_the_compose_dialog(self):
        dialog = ComposeDialog(pracownik_id=1)
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.activateWindow()
        edit = dialog.to_edit
        edit.setFocus()
        edit.setText('Jan')
        edit.setCursorPosition(3)
        row = QStandardItem('Jan Kowalski — jan@example.com')
        row.setData('"Jan Kowalski" <jan@example.com>', Qt.ItemDataRole.UserRole)
        edit._model.appendRow(row)
        edit._completer.setCompletionPrefix('')
        edit._completer.complete()
        edit._completer.popup().setCurrentIndex(edit._completer.completionModel().index(0, 0))
        _app.processEvents()
        QTest.keyClick(edit, Qt.Key.Key_Return)
        self.assertEqual(split_addresses(edit.text()), ['jan@example.com'])
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertTrue(dialog.isVisible())

    def test_suggestions_search_shared_data_off_the_ui_thread_and_include_secondary_email(self):
        save_contact(1, {'email': 'jan@example.com', 'additional_email': 'extra@example.com', 'display_name': 'Jan Kowalski'})
        other = self.make_employee('reader@example.com')
        edit = RecipientEdit(other)
        self.addCleanup(edit.close)
        edit.show()
        edit.activateWindow()
        edit.setFocus()
        _app.processEvents()
        QTest.keyClicks(edit, 'Jan')
        self.assertTrue(wait_until(lambda: edit._model.rowCount() == 2))
        self.assertIn('extra@example.com', edit._model.item(1).text())
        edit._completer.popup().hide()

    def test_import_does_not_close_or_delete_contacts_while_write_is_in_progress(self):
        dialog = AddressBookDialog(1)
        self.addCleanup(dialog.close)
        dialog.show()
        self.assertTrue(wait_until(lambda: 'Kontakty:' in dialog.status.text()))
        entered, release = threading.Event(), threading.Event()
        threads = []
        self.addCleanup(release.set)
        plan = {'contacts': [{'email': 'imported@example.com', 'display_name': 'Imported'}],
                'issues': [], 'duplicates': 0, 'encoding': 'utf-8-sig'}
        def write(actor, rows):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return import_contacts(actor, rows)
        with mock.patch.object(address_book_ui.QFileDialog, 'getOpenFileName', return_value=('contacts.csv', 'CSV')), \
                mock.patch.object(address_book_ui, 'read_contacts_csv', return_value=plan), \
                mock.patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(QMessageBox, 'information'), \
                mock.patch.object(QMessageBox, 'question') as confirm_delete, \
                mock.patch.object(address_book_ui, 'import_contacts', side_effect=write):
            dialog._import_csv()
            self.assertTrue(wait_until(entered.is_set))
            self.assertTrue(dialog._busy)
            dialog._delete()
            dialog.reject()
            confirm_delete.assert_not_called()
            self.assertTrue(dialog.isVisible())
            release.set()
            self.assertTrue(wait_until(lambda: not dialog._busy))
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertIsNotNone(find_contact(1, 'imported@example.com'))

    def test_cancelled_import_preview_does_not_write_contacts(self):
        dialog = AddressBookDialog(1)
        self.addCleanup(dialog.close)
        plan = {'contacts': [{'email': 'new@example.com'}], 'issues': [], 'duplicates': 0, 'encoding': 'utf-8'}
        with mock.patch.object(address_book_ui.QFileDialog, 'getOpenFileName', return_value=('contacts.csv', 'CSV')), \
                mock.patch.object(address_book_ui, 'read_contacts_csv', return_value=plan), \
                mock.patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Cancel), \
                mock.patch.object(address_book_ui, 'import_contacts') as write:
            dialog._import_csv()
            self.assertTrue(wait_until(lambda: not dialog._busy))
            write.assert_not_called()


class HeaderContactTests(DbTestCase):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(email_ui, 'get_email_accounts_for_employee', return_value=[]),
                        mock.patch.object(email_ui.QMessageBox, 'warning'),
                        mock.patch.object(email_ui, 'MailBrowser', QTextBrowser)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = email_ui.EmailPage(1)
        self.addCleanup(self.page.close)
        self.addCleanup(self.page.stop_background_sync)
        self.page.mail_view.show_html = mock.Mock()

    def test_sender_to_and_cc_addresses_have_safe_click_targets(self):
        message = EmailMessage()
        message['From'] = 'Jan Kowalski <jan@example.com>'
        message['To'] = 'Anna <anna@example.com>'
        message['Cc'] = 'Other <other@example.com>'
        message.set_content('Body')
        self.page._render_message(message)
        targets = set(self.page.mail_view.contact_links.values())
        self.assertEqual(targets, {('Jan Kowalski', 'jan@example.com'), ('Anna', 'anna@example.com'), ('Other', 'other@example.com')})
        header = self.page.mail_view.show_html.call_args.args[1]
        self.assertEqual(header.count('href="biuro-contact:'), 3)

    def test_click_uses_prefilled_form_or_existing_contact(self):
        with mock.patch.object(address_book_ui, 'ContactDialog') as editor:
            self.page._add_global_contact('Jan Kowalski', 'jan@example.com')
            self.assertTrue(wait_until(lambda: editor.called))
        self.assertEqual(editor.call_args.kwargs['initial'], {'email': 'jan@example.com', 'display_name': 'Jan Kowalski'})
        self.assertIsNone(editor.call_args.kwargs['contact'])
        existing = save_contact(1, {'email': 'jan@example.com'})
        with mock.patch.object(address_book_ui, 'ContactDialog') as editor:
            self.page._add_global_contact('Jan', 'jan@example.com')
            self.assertTrue(wait_until(lambda: editor.called))
        self.assertEqual(editor.call_args.kwargs['contact']['id'], existing['id'])

    def test_untrusted_contact_links_without_current_header_token_are_ignored(self):
        class View(QObject):
            address_clicked = pyqtSignal(str, str)
        view = View()
        view.contact_links = {'valid': ('Jan', 'jan@example.com')}
        received = []
        view.address_clicked.connect(lambda *args: received.append(args))
        page = types.SimpleNamespace(parent=lambda: view)
        navigation = email_ui.QWebEnginePage.NavigationType.NavigationTypeLinkClicked
        email_ui._MailWebPage.acceptNavigationRequest(page, QUrl('biuro-contact:unknown'), navigation, True)
        self.assertEqual(received, [])
        email_ui._MailWebPage.acceptNavigationRequest(page, QUrl('biuro-contact:valid'), navigation, True)
        self.assertEqual(received, [('Jan', 'jan@example.com')])
