import json
import unittest
from decimal import Decimal
from unittest.mock import patch

import psycopg
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    assign_invoice_number, cancel_invoice, create_or_update_invoice, delete_draft_invoice,
    delete_issued_invoice, get_invoice_by_id, InvoiceLockedError, list_invoices,
    mark_exported, set_invoice_number,
)
from app.db.dao_year_end import list_year_end, set_company_declarations
from app.invoices.positions import normalize_position, totals
from tests.base import DbTestCase

POSITION = {"nazwa": "Usługa", "ilosc": 1, "cena_netto": 10, "vat": 23}


class InvoiceHistoryTests(DbTestCase):
    def draft(self, name="Klient"):
        return create_or_update_invoice(self.make_company(name), "2026-11", [POSITION], 10, 12.3)

    def test_issue_moves_draft_and_numbers_it(self):
        invoice = self.draft()
        mark_exported([invoice], 1)
        self.assertNotIn(invoice, [i['id'] for i in list_invoices(1, False)])
        self.assertIn(invoice, [i['id'] for i in list_invoices(1, True)])
        self.assertTrue(get_invoice_by_id(invoice)['numer'])

    def test_deleted_draft_number_is_never_reused(self):
        first = self.draft()
        number = assign_invoice_number(first)
        delete_draft_invoice(first)
        second = self.draft("Drugi")
        self.assertNotEqual(assign_invoice_number(second), number)
        with self.assertRaises(ValueError):
            set_invoice_number(second, number)

    def test_cancel_retains_complete_document_and_audit(self):
        invoice = self.draft()
        mark_exported([invoice], 1)
        before = get_invoice_by_id(invoice)
        cancel_invoice(invoice, 1, "Dokument niewprowadzony do obrotu")
        after = get_invoice_by_id(invoice)
        self.assertEqual(after['numer'], before['numer'])
        self.assertEqual(after['status'], 'anulowana')
        with get_conn() as conn:
            archived = conn.execute('SELECT document_json FROM faktury_archiwum WHERE faktura_id=%s', (invoice,)).fetchone()
            self.assertEqual(json.loads(archived[0])['numer'], before['numer'])
            self.assertEqual(conn.execute("SELECT uzytkownik_id FROM audit_log WHERE akcja='anulowanie'").fetchone()[0], 1)
        with self.assertRaises(InvoiceLockedError):
            delete_issued_invoice(invoice)
        with self.assertRaises(InvoiceLockedError):
            set_invoice_number(invoice, 'ZMIANA')
        for sql in ('DELETE FROM faktury WHERE id=%s', "UPDATE faktury SET numer='ZMIANA' WHERE id=%s"):
            with self.assertRaises(psycopg.Error):
                with get_conn() as conn:
                    conn.execute(sql, (invoice,))

    def test_ui_issues_without_a_file_dialog(self):
        from app.invoices.faktury_ui import FakturyPage
        app = QApplication.instance() or QApplication([])
        invoice = self.draft()
        page = FakturyPage(1)
        page.refresh()
        with patch.object(page, '_selected_ids', return_value=[invoice]), patch('app.invoices.faktury_ui.QFileDialog.getSaveFileName', side_effect=AssertionError('XLSX dialog')):
            page._export_selected_drafts()
        self.assertEqual(page.btn_export.text(), 'Wystaw faktury')
        self.assertIs(page.tabs.currentWidget(), page.tab_wystawione)
        self.assertTrue(get_invoice_by_id(invoice)['eksportowana'])
        page.close()

    def test_number_batch_rolls_back_on_missing_invoice(self):
        invoice = self.draft()
        with self.assertRaises(ValueError):
            mark_exported([invoice, 999999], 1)
        self.assertFalse(get_invoice_by_id(invoice)['eksportowana'])


class YearEndSnapshotTests(DbTestCase):
    def test_company_changes_do_not_rewrite_previous_year(self):
        company = self.make_company('Historyczny', forma_opodatkowania='Skala')
        previous = next(c for c in list_year_end(2025) if c['id'] == company)
        self.assertEqual(previous['deklaracje'][0]['kod'], 'PIT-36')
        with get_conn() as conn:
            conn.execute("UPDATE firmy SET forma_opodatkowania='Ryczałt', status='Nieaktywne' WHERE id=%s", (company,))
        historic = next(c for c in list_year_end(2025) if c['id'] == company)
        self.assertEqual(historic['deklaracje'][0]['kod'], 'PIT-36')
        set_company_declarations(company, 'PIT-36L', '', year=2025, actor_id=1)
        self.assertEqual(next(c for c in list_year_end(2025) if c['id'] == company)['deklaracje'][0]['kod'], 'PIT-36L')
        self.assertNotIn(company, [c['id'] for c in list_year_end(2026)])


class RoundingTests(unittest.TestCase):
    def test_half_cent_and_negative_correction(self):
        p = normalize_position({'nazwa':'A', 'cena_netto':'1.005', 'ilosc':1, 'vat':0})
        self.assertEqual(p['netto'], 1.01)
        p = normalize_position({'nazwa':'A', 'cena_netto':'0.50', 'ilosc':-1, 'vat':23})
        self.assertEqual(p['brutto'], -0.62)
        self.assertEqual(totals(iter([p, p])), (-1.0, -1.24))


class AnnualCompanyFormTests(DbTestCase):
    def test_year_switch_keeps_unsaved_ticks_and_saves_independently(self):
        from app.companies.company_form import CompanyForm
        from app.db.dao_year_end import get_company_year_declarations, save_company_year_declarations
        app = QApplication.instance() or QApplication([])
        company = self.make_company('Deklaracje', roczne_deklaracje='[]')
        form = CompanyForm(company, actor_id=1)
        form.annual_year.setValue(2024)
        form.declaration_checks['PIT-36'].setChecked(True)
        form.annual_year.setValue(2025)
        self.assertFalse(form.declaration_checks['PIT-36'].isChecked())
        form.declaration_checks['PIT-28'].setChecked(True)
        form.declaration_checks['PIT-11'].setChecked(True)
        form.annual_year.setValue(2024)
        self.assertTrue(form.declaration_checks['PIT-36'].isChecked())
        self.assertFalse(form.declaration_checks['PIT-28'].isChecked())
        save_company_year_declarations(company, form._annual_edits, 1)
        self.assertEqual(get_company_year_declarations(company, 2024), ['PIT-36'])
        self.assertEqual(get_company_year_declarations(company, 2025), ['PIT-28', 'PIT-11'])
        form.close()
        reopened = CompanyForm(company, actor_id=1)
        reopened.annual_year.setValue(2025)
        self.assertTrue(reopened.declaration_checks['PIT-28'].isChecked())
        self.assertTrue(reopened.declaration_checks['PIT-11'].isChecked())
        reopened.declaration_checks['PIT-28'].setChecked(False)
        reopened.declaration_checks['PIT-11'].setChecked(False)
        save_company_year_declarations(company, reopened._annual_edits, 1)
        self.assertEqual(get_company_year_declarations(company, 2025), [])
        self.assertEqual(get_company_year_declarations(company, 2024), ['PIT-36'])
        # Initializing the year list must not overwrite a previously configured company.
        self.assertEqual(next(c for c in list_year_end(2024) if c['id']==company)['deklaracje'][0]['kod'], 'PIT-36')
        reopened.close()


class OwnerAnnualTests(DbTestCase):
    def test_two_owners_have_independent_forms_and_filing_status(self):
        from app.companies.company_form import CompanyForm
        from app.db.dao_year_end import save_owner_year_declarations, save_declaration
        app = QApplication.instance() or QApplication([])
        company = self.make_company('Dwóch wspólników', roczne_deklaracje='[]')
        with get_conn() as conn:
            a = conn.execute("INSERT INTO udzialowcy(firma_id,imie_pierwsze,nazwisko) VALUES (%s,'Anna','Nowak') RETURNING id", (company,)).fetchone()[0]
            b = conn.execute("INSERT INTO udzialowcy(firma_id,imie_pierwsze,nazwisko) VALUES (%s,'Jan','Kowalski') RETURNING id", (company,)).fetchone()[0]
        form = CompanyForm(company, actor_id=1)
        form.annual_year.setValue(2025)
        form.annual_owner.setCurrentIndex(form.annual_owner.findData(a))
        form.declaration_checks['PIT-36'].setChecked(True)
        form.annual_owner.setCurrentIndex(form.annual_owner.findData(b))
        self.assertFalse(form.declaration_checks['PIT-36'].isChecked())
        form.declaration_checks['PIT-36L'].setChecked(True)
        save_owner_year_declarations(company, form._owner_annual_edits, 1)
        form.close()
        declarations = next(c for c in list_year_end(2025) if c['id']==company)['deklaracje']
        self.assertEqual({(d['kod'],d['owner_name']) for d in declarations}, {('PIT-36','Anna Nowak'),('PIT-36L','Jan Kowalski')})
        # Even the same PIT form for both owners has independent completion/UPO.
        save_owner_year_declarations(company, {(2025, b): ['PIT-36']}, 1)
        save_declaration(1, company, 2025, 'PIT-36', owner_key=a, filed=True, upo='ANNA-123')
        declarations = next(c for c in list_year_end(2025) if c['id']==company)['deklaracje']
        self.assertTrue(next(d for d in declarations if d['owner_key']==a)['zlozona'])
        self.assertFalse(next(d for d in declarations if d['owner_key']==b).get('zlozona',False))

    def test_additional_zus_checkboxes_persist_by_year_and_owner(self):
        from app.companies.company_form import CompanyForm
        from app.db.dao_year_end import save_company_year_declarations, save_owner_year_declarations
        app = QApplication.instance() or QApplication([])
        company = self.make_company('ZUS roczny', roczne_deklaracje='[]')
        with get_conn() as conn:
            owner = conn.execute("INSERT INTO udzialowcy(firma_id,imie_pierwsze,nazwisko) VALUES (%s,'Anna','Nowak') RETURNING id", (company,)).fetchone()[0]
        form = CompanyForm(company, actor_id=1)
        form.annual_year.setValue(2025)
        form.declaration_checks['ZUS IWA'].setChecked(True)
        form.declaration_checks['PFRON DEK-R'].setChecked(True)
        form.annual_owner.setCurrentIndex(form.annual_owner.findData(owner))
        form.declaration_checks['ZUS DRA — roczne zdrowotne'].setChecked(True)
        save_company_year_declarations(company, form._annual_edits, 1)
        save_owner_year_declarations(company, form._owner_annual_edits, 1)
        form.close()
        reopened = CompanyForm(company, actor_id=1)
        reopened.annual_year.setValue(2025)
        self.assertTrue(reopened.declaration_checks['ZUS IWA'].isChecked())
        reopened.annual_owner.setCurrentIndex(reopened.annual_owner.findData(owner))
        self.assertTrue(reopened.declaration_checks['ZUS DRA — roczne zdrowotne'].isChecked())
        self.assertFalse(reopened.declaration_checks['ZUS IWA'].isChecked())
        reopened.annual_year.setValue(2024)
        self.assertFalse(reopened.declaration_checks['ZUS DRA — roczne zdrowotne'].isChecked())
        reopened.close()
        declarations = next(c for c in list_year_end(2025) if c['id']==company)['deklaracje']
        self.assertEqual({d['kod'] for d in declarations}, {'ZUS IWA', 'PFRON DEK-R', 'ZUS DRA — roczne zdrowotne'})
