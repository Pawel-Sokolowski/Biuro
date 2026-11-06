from unittest.mock import patch

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import QApplication

from app.companies.company_names import company_display_name
from app.db.dao_companies import get_companies_filtered, list_companies
from app.db.dao_invoices import create_manual_invoice, get_invoice_by_id
from app.db.dao_tasks import list_tasks, list_tasks_for_timer, save_task
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(['company-display-names'])


class CompanyDisplayNamesTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.full = 'PPHU Usługi Handlowe Jan Kowalski'
        self.short = 'Kowalski Jan'
        self.company_id = self.make_company(self.full, nazwa_skrocona=self.short)

    def test_lists_keep_legal_name_but_sort_and_search_by_short_name(self):
        other = self.make_company('AAAA Pełna', nazwa_skrocona='Żak Adam')
        rows = [r for r in list_companies(1) if r['id'] in {self.company_id, other}]
        self.assertEqual([r['id'] for r in rows], [self.company_id, other])
        self.assertEqual((rows[0]['nazwa'], rows[0]['nazwa_skrocona']), (self.full, self.short))
        self.assertIn(self.company_id, {r['id'] for r in get_companies_filtered('Kowalski Jan', 'Wszystkie', None, 1)})
        self.assertEqual(company_display_name({'nazwa': 'Full', 'nazwa_skrocona': '  '}), 'Full')

    def test_task_picker_and_task_timer_labels_use_short_name(self):
        from app.tasks.tasks_ui import TaskDialog
        task_id = save_task('Test', firma_id=self.company_id, pracownik_id=1, actor_id=1)
        task = next(t for t in list_tasks(viewer_id=1) if t['id'] == task_id)
        self.assertEqual(task['firma_nazwa'], self.short)
        timer_task = next(t for t in list_tasks_for_timer(1, self.company_id) if t['id'] == task_id)
        self.assertEqual(timer_task['firma_nazwa'], self.short)
        dialog = TaskDialog(None, 1, list_companies(1), [], task)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.company.currentText(), self.short)
        self.assertEqual(dialog.company.currentData(), self.company_id)

    def test_calendar_picker_uses_short_name(self):
        from app.kalendarz.calendar_ui import EventDialog
        with patch('app.kalendarz.calendar_ui.google_sync.calendars_for', return_value=[]):
            dialog = EventDialog(1, QDate(2026, 11, 6))
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.company.itemText(dialog.company.findData(self.company_id)), self.short)

    def test_invoice_search_uses_short_name_but_document_keeps_legal_name(self):
        from app.invoices.manual_invoice_dialog import ManualInvoiceDialog
        dialog = ManualInvoiceDialog()
        self.addCleanup(dialog.close)
        dialog._search_buyers('Kowalski Jan')
        item = next(dialog.results.item(i) for i in range(dialog.results.count())
                    if dialog.results.item(i).data(Qt.ItemDataRole.UserRole) == {'typ': 'firma', 'id': self.company_id})
        self.assertIn(self.short, item.text())
        self.assertNotIn('PPHU', item.text())
        invoice_id = create_manual_invoice('firma', self.company_id,
            [{'nazwa': 'Usługa', 'ilosc': 1, 'jednostka': 'szt.', 'cena_netto': 100, 'vat': 23}],
            '2026-11-06', '2026-11-06', '2026-11-13')
        self.assertEqual(get_invoice_by_id(invoice_id)['nabywca_pelna'], self.full)
