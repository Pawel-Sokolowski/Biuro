"""Background KSeF completions cannot reset the working invoice view."""
from copy import deepcopy
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QItemSelectionModel, QSettings
from PyQt6.QtWidgets import QApplication, QAbstractItemView

from app.db.dao_invoices import create_or_update_invoice, get_invoice_by_id, mark_exported
from app.db.dao_payments import record_manual_payment
from app.invoices.faktury_ui import FakturyPage
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(['invoice-refresh-state'])
POSITION = {'nazwa': 'Usługa', 'ilosc': 1, 'cena_netto': 100, 'vat': 23}


class InvoiceRefreshStateTests(DbTestCase):
    def setUp(self):
        super().setUp()
        company = self.make_company('Refresh client')
        first = create_or_update_invoice(company, '2026-09', [POSITION], 100, 123)
        mark_exported([first], 1)
        record_manual_payment(first, '2026-08-31', '123.00', 1)
        self.template = get_invoice_by_id(first)
        self.rows = [{**self.template, 'id': first + number, 'numer': f'FV/{number:04d}/09/2026',
                      'firma_nazwa': 'Refresh client', 'nazwa_skrocona': 'Refresh', 'ksef_status': '', 'ksef_numer': None}
                     for number in range(100)]
        # Each row is paid. Use real invoice/page setup but a large deterministic
        # list so viewport checks do not depend on expensive fixture writes.
        self.payments = {row['id']: [{'data_zaplaty': '2026-08-31', 'kwota': 123}] for row in self.rows}
        self.drafts = []
        def listing(actor, issued, *args):
            if issued is None:
                return deepcopy(self.rows + self.drafts)
            return deepcopy(self.rows if issued else self.drafts)
        self.settings = QSettings(str(Path(self._tmp) / "invoice-view.ini"), QSettings.Format.IniFormat)
        patchers = [mock.patch("app.invoices.faktury_ui.QSettings", return_value=self.settings), mock.patch('app.invoices.faktury_ui.list_invoices', side_effect=listing),
                    mock.patch('app.invoices.faktury_ui.payments_for_invoices', side_effect=lambda ids: deepcopy(self.payments)),
                    mock.patch.object(FakturyPage, '_start_ksef_batch')]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = FakturyPage(1)
        self.page.resize(1300, 720)
        self.page.show()
        self.addCleanup(self.page.close)
        self.page.month_button.setCurrentText('2026-09')
        self.page.payment_filter.setCurrentIndex(self.page.payment_filter.findData('paid'))
        self.page.refresh()
        _app.processEvents()

    def place_view(self):
        table = self.page.issued_table
        table.clearSelection()
        table.setCurrentCell(55, 4, QItemSelectionModel.SelectionFlag.NoUpdate)
        for row in (55, 57):
            table.selectionModel().select(table.model().index(row, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        table.scrollToItem(table.item(50, 0), QAbstractItemView.ScrollHint.PositionAtTop)
        _app.processEvents()
        return table.verticalScrollBar().value()

    def selected(self):
        return set(self.page._selected_ids(self.page.issued_table))

    def finish_poll(self):
        self.page._ksef_batch_finished({'accepted': [], 'pending': [self.rows[0]['id']], 'rejected': [], 'errors': []})

    def test_repeated_background_checks_keep_paid_filter_scroll_selection_and_widgets(self):
        scroll = self.place_view()
        selected = self.selected()
        table = self.page.issued_table
        original = table.item(55, 4)
        original_widget = table.cellWidget(55, self.page.ISSUED_PREVIEW_COLUMN)
        for _ in range(4):
            self.finish_poll()
        self.assertEqual(self.page.payment_filter.currentData(), 'paid')
        self.assertEqual(self.page._current_period(), '2026-09')
        self.assertEqual(table.verticalScrollBar().value(), scroll)
        self.assertEqual(self.selected(), selected)
        self.assertIs(table.item(55, 4), original)
        self.assertIs(table.cellWidget(55, self.page.ISSUED_PREVIEW_COLUMN), original_widget)

    def test_changed_status_updates_data_without_jumping_to_the_top(self):
        scroll = self.place_view()
        selected = self.selected()
        self.rows[10]['ksef_status'] = 'odrzucona'
        self.finish_poll()
        self.assertEqual(self.page.issued_table.item(10, 13).text(), 'Odrzucona')
        self.assertEqual(self.page.issued_table.verticalScrollBar().value(), scroll)
        self.assertEqual(self.selected(), selected)
        self.assertEqual(self.page.issued_table.currentRow(), 55)

    def test_filters_combine_without_database_reads(self):
        self.rows[20]['rodzaj'] = 'korekta'
        self.rows[21]['rodzaj'] = 'korekta'
        self.payments[self.rows[21]['id']] = []
        self.page.refresh()
        with mock.patch('app.invoices.faktury_ui.list_invoices') as invoices, \
                mock.patch('app.invoices.faktury_ui.payments_for_invoices') as payments:
            self.page.invoice_kind_filter.setCurrentIndex(self.page.invoice_kind_filter.findData('korekta'))
            self.page.invoice_number_filter.setText('002')
            self.assertEqual(self.page.issued_table.rowCount(), 1)
            self.assertEqual(self.page.issued_table.item(0, 4).text(), self.rows[20]['numer'])
            self.page.payment_filter.setCurrentIndex(self.page.payment_filter.findData('unpaid'))
            self.assertEqual(self.page.issued_table.item(0, 4).text(), self.rows[21]['numer'])
            self.page.invoice_kind_filter.setCurrentIndex(self.page.invoice_kind_filter.findData('sale'))
            self.assertEqual(self.page.issued_table.rowCount(), 0)
            invoices.assert_not_called()
            payments.assert_not_called()

    def test_refresh_fetches_both_invoice_lists_once_and_shows_short_buyer(self):
        with mock.patch('app.invoices.faktury_ui.list_invoices', return_value=deepcopy(self.rows)) as listing:
            self.page.refresh()
        listing.assert_called_once_with(1, None, '2026-09', None, None)
        self.assertEqual(self.page.issued_table.item(0, 2).text(), 'Refresh')
        self.assertGreaterEqual(self.page.issued_table.columnWidth(2), 240)

    def test_extra_activities_load_once_when_tab_is_opened(self):
        self.assertIsNone(self.page.extra_activities)
        with mock.patch('app.tasks.extra_activities_ui.dao_services.list_extra_activities', return_value=[]) as listing:
            self.page.tabs.setCurrentWidget(self.page.tab_dodatkowe)
            self.assertEqual(self.page.extra_activities.month.currentText(), '2026-09')
            listing.assert_called_once()
            self.page.tabs.setCurrentWidget(self.page.tab_wystawione)
            self.page.tabs.setCurrentWidget(self.page.tab_dodatkowe)
            listing.assert_called_once()

    def test_number_search_combines_with_payment_filter_and_survives_refresh(self):
        self.payments[self.rows[21]['id']] = []
        self.page.refresh()
        self.page.invoice_number_filter.setText(' fv/002 ')
        self.assertEqual(self.page.issued_table.rowCount(), 9)
        self.assertEqual(self.page.issued_table.item(0, 4).text(), 'FV/0020/09/2026')
        self.finish_poll()
        self.assertEqual(self.page.invoice_number_filter.text(), ' fv/002 ')
        self.assertEqual(self.page.issued_table.rowCount(), 9)
        self.page.payment_filter.setCurrentIndex(self.page.payment_filter.findData('unpaid'))
        self.assertEqual(self.page.issued_table.rowCount(), 1)
        self.assertEqual(self.page.issued_table.item(0, 4).text(), 'FV/0021/09/2026')
        self.page.invoice_number_filter.setText('no match')
        self.assertEqual(self.page.issued_table.rowCount(), 0)
        self.page.invoice_number_filter.clear()
        self.assertEqual(self.page.issued_table.rowCount(), 1)

    def test_paid_filter_removes_newly_unpaid_row_and_never_selects_another_invoice(self):
        self.place_view()
        removed = self.rows[55]['id']
        other_selected = self.rows[57]['id']
        self.payments[removed] = []
        self.finish_poll()
        self.assertEqual(self.page.payment_filter.currentData(), 'paid')
        self.assertEqual(self.page.issued_table.rowCount(), 99)
        self.assertEqual(self.selected(), {other_selected})
        self.assertNotEqual(self.page.issued_table.currentRow(), 55)

    def test_company_filter_and_uncommitted_search_text_survive_poll(self):
        self.page.firma_combo.setCurrentIndex(1)
        self.page.firma_combo.setEditText('search being typed')
        before = (self.page.firma_combo.currentIndex(), self.page.firma_combo.currentText())
        self.finish_poll()
        self.assertEqual((self.page.firma_combo.currentIndex(), self.page.firma_combo.currentText()), before)
        self.assertEqual(self.page.payment_filter.currentData(), 'paid')

    def test_open_inline_editor_defers_background_refresh(self):
        with mock.patch.object(self.page.positions_table, 'state', return_value=QAbstractItemView.State.EditingState), \
                mock.patch.object(self.page, '_fill_issued') as fill:
            self.page.refresh()
        fill.assert_not_called()
        self.assertTrue(self.page._refresh_later.isActive())
        self.page._refresh_later.stop()
        self.page.refresh()
        self.assertEqual(self.page.issued_table.rowCount(), 100)

    def test_paid_filter_is_restored_when_the_page_is_recreated(self):
        other_page = FakturyPage(1)
        self.addCleanup(other_page.close)
        self.assertEqual(other_page.payment_filter.currentData(), 'paid')
        # A different employee has an independent default preference.
        self.assertIsNone(self.settings.value('users/99/invoices/payment_filter'))

    def test_opening_and_refreshing_accepted_invoices_does_not_schedule_ksef_checks(self):
        for row in self.rows:
            row.update(ksef_status='przyjeta', ksef_numer='NO-LOCAL-CACHE', invoice_email_status='queued')
        self.page._ksef_poll_timer.stop()
        with mock.patch.object(self.page, '_start_ksef_batch') as start:
            for _ in range(3):
                self.page.refresh()
        self.assertFalse(self.page._ksef_poll_timer.isActive())
        self.assertEqual(self.page._ksef_pending, set())
        start.assert_not_called()

    def test_waiting_invoice_retries_after_one_minute_and_stops_after_acceptance(self):
        invoice_id = self.rows[0]['id']
        self.finish_poll()
        self.assertTrue(self.page._ksef_poll_timer.isActive())
        self.assertGreater(self.page._ksef_poll_timer.remainingTime(), 59000)
        self.assertEqual(self.page._ksef_poll_timer.interval(), 60000)
        with mock.patch.object(self.page, '_start_ksef_batch') as start:
            self.page._poll_pending_ksef()
        start.assert_called_once_with([invoice_id], send=False)
        self.page._ksef_batch_finished({'accepted': [{'id': invoice_id, 'number': 'ACCEPTED'}],
                                        'pending': [], 'rejected': [], 'errors': []})
        self.assertFalse(self.page._ksef_poll_timer.isActive())
        self.assertEqual(self.page._ksef_pending, set())

    def test_new_rows_keep_the_same_top_visible_invoice_and_selected_ids(self):
        self.place_view()
        table = self.page.issued_table
        anchor = table.item(table.rowAt(0), 0).text()
        selected = self.selected()
        added = {**self.rows[0], 'id': self.rows[-1]['id'] + 100, 'numer': 'FV/NEW/09/2026'}
        self.rows.insert(0, added)
        self.payments[added['id']] = [{'data_zaplaty': '2026-08-31', 'kwota': 123}]
        self.finish_poll()
        self.assertEqual(table.item(table.rowAt(0), 0).text(), anchor)
        self.assertEqual(self.selected(), selected)

    def test_draft_selection_and_service_search_survive_background_completion(self):
        company = self.make_company('Draft customer')
        draft = create_or_update_invoice(company, '2026-09', [POSITION], 100, 123)
        self.drafts = [{**get_invoice_by_id(draft), 'firma_nazwa': 'Draft customer', 'nazwa_skrocona': 'Draft'}]
        self.page.refresh()
        self.page.tabs.setCurrentWidget(self.page.tab_robocze)
        self.page.draft_table.selectRow(0)
        self.page.service_choice.setEditText('service search in progress')
        position = self.page.positions_table.item(0, 0)
        self.finish_poll()
        self.assertEqual(self.page._selected_ids(self.page.draft_table), [draft])
        self.assertIs(self.page.positions_table.item(0, 0), position)
        self.assertEqual(self.page.service_choice.currentText(), 'service search in progress')
        # An update to the selected draft still leaves the catalogue query intact.
        self.drafts[0]['firma_nazwa'] = 'Changed display name'
        self.page.refresh()
        self.assertEqual(self.page.service_choice.currentText(), 'service search in progress')
