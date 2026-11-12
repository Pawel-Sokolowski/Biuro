import json
import unittest
from decimal import Decimal
from unittest.mock import patch

import psycopg
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    assign_invoice_number, cancel_invoice, create_or_update_invoice, delete_draft_invoice,
    delete_issued_invoice, get_invoice_by_id, get_invoice_for_company_month, InvoiceLockedError, list_invoices,
    mark_exported, set_invoice_number,
)
from app.db.dao_year_end import list_year_end, set_company_declarations
from app.invoices.positions import normalize_position, reverse_positions, totals
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

    def test_deleted_unissued_draft_releases_its_number(self):
        first = self.draft()
        number = assign_invoice_number(first)
        delete_draft_invoice(first)
        second = self.draft("Drugi")
        self.assertEqual(assign_invoice_number(second), number)

    def test_cancel_returns_invoice_to_drafts_without_number_and_keeps_audit(self):
        invoice = self.draft()
        mark_exported([invoice], 1)
        before = get_invoice_by_id(invoice)
        cancel_invoice(invoice, 1, "Dokument niewprowadzony do obrotu")
        after = get_invoice_by_id(invoice)
        self.assertFalse(after['numer'])
        self.assertEqual(after['status'], 'robocza')
        self.assertFalse(after['eksportowana'])
        self.assertIn(invoice, [i['id'] for i in list_invoices(1, False)])
        self.assertNotIn(invoice, [i['id'] for i in list_invoices(1, True)])
        with get_conn() as conn:
            archived = conn.execute('SELECT document_json FROM faktury_archiwum WHERE faktura_id=%s', (invoice,)).fetchone()
            self.assertEqual(json.loads(archived[0])['numer'], before['numer'])
            audit = conn.execute("SELECT uzytkownik_id, szczegoly FROM audit_log WHERE akcja='anulowanie'").fetchone()
            self.assertEqual(audit[0], 1)
            self.assertIn(before['numer'], audit[1])
        # Zwykła zmiana wystawionej faktury nadal jest blokowana przez trigger.
        other = self.draft("Inny")
        mark_exported([other], 1)
        with self.assertRaises(psycopg.Error):
            with get_conn() as conn:
                conn.execute("UPDATE faktury SET numer='ZMIANA' WHERE id=%s", (other,))

    def test_cancel_releases_number_for_another_invoice(self):
        first = self.draft("Anulowany")
        mark_exported([first], 1)
        number = get_invoice_by_id(first)["numer"]
        cancel_invoice(first, 1, "Dokument archiwalny")

        second = self.draft("Nowy klient")
        self.assertEqual(assign_invoice_number(second), number)
        self.assertFalse(get_invoice_by_id(first)["numer"])

    def test_cancel_requires_access_to_the_locked_invoice(self):
        invoice = self.draft()
        mark_exported([invoice], 1)
        with patch("app.db.dao_invoices.can", return_value=True), \
                patch("app.db.dao_invoices.can_access_invoice", return_value=False):
            with self.assertRaises(PermissionError):
                cancel_invoice(invoice, 999, "Niedozwolone")
        self.assertTrue(get_invoice_by_id(invoice)["eksportowana"])

    def test_invoice_with_ksef_number_cannot_be_cancelled(self):
        invoice = self.draft()
        mark_exported([invoice], 1)
        with get_conn() as conn:
            conn.execute("UPDATE faktury SET ksef_numer='5260250274-20261104-AAAAAA-BB' WHERE id=%s", (invoice,))
        with self.assertRaises(InvoiceLockedError):
            cancel_invoice(invoice, 1, "Pomyłka")
        self.assertTrue(get_invoice_by_id(invoice)["numer"])

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

    def test_historical_amounts_are_preserved_for_reading_and_reversal(self):
        historical = {'nazwa': 'A', 'ilosc': 3, 'netto': 10, 'brutto': 12.3, 'vat': 23}
        preserved = normalize_position(historical, preserve_amounts=True)
        self.assertEqual((preserved['netto'], preserved['brutto']), (10, 12.3))
        correction = reverse_positions([historical])[0]
        self.assertEqual((correction['ilosc'], correction['netto'], correction['brutto']), (-3, -10, -12.3))


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
    def test_company_and_owner_can_select_the_same_declaration(self):
        from app.db.dao_year_end import save_owner_year_declarations
        company = self.make_company('Niezależne PIT-11', roczne_deklaracje='["PIT-11"]')
        with get_conn() as conn:
            owner = conn.execute(
                "INSERT INTO udzialowcy(firma_id,imie_pierwsze,nazwisko) "
                "VALUES (%s,'Anna','Nowak') RETURNING id",
                (company,),
            ).fetchone()[0]
        save_owner_year_declarations(company, {(2025, owner): ['PIT-11']}, 1)
        declarations = next(c for c in list_year_end(2025) if c['id'] == company)['deklaracje']
        self.assertEqual(
            {(d['kod'], d.get('owner_key', 0)) for d in declarations},
            {('PIT-11', 0), ('PIT-11', owner)},
        )

    def test_new_active_company_is_snapshotted_after_year_initialization(self):
        list_year_end(2025)
        company = self.make_company('Dodana później', forma_opodatkowania='Skala')
        declarations = next(c for c in list_year_end(2025) if c['id'] == company)['deklaracje']
        self.assertEqual([d['kod'] for d in declarations], ['PIT-36'])

    def test_owner_save_seeds_company_defaults_and_existing_filing(self):
        from app.db.dao_year_end import save_owner_year_declarations
        company = self.make_company('Domyślne firmy', roczne_deklaracje='["PIT-36"]')
        with get_conn() as conn:
            owner = conn.execute(
                "INSERT INTO udzialowcy(firma_id,imie_pierwsze,nazwisko) "
                "VALUES (%s,'Anna','Nowak') RETURNING id",
                (company,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO zakonczenie_roku(firma_id,rok,deklaracja,zlozona,upo,owner_key) "
                "VALUES (%s,2025,'PIT-11',1,'UPO-FIRMY',0)",
                (company,),
            )
        save_owner_year_declarations(company, {(2025, owner): ['PIT-36L']}, 1)
        declarations = next(c for c in list_year_end(2025) if c['id'] == company)['deklaracje']
        company_rows = [d for d in declarations if d.get('owner_key', 0) == 0]
        self.assertEqual({d['kod'] for d in company_rows}, {'PIT-36', 'PIT-11'})
        self.assertEqual(next(d for d in company_rows if d['kod'] == 'PIT-11')['upo'], 'UPO-FIRMY')

    def test_deleted_annual_owner_discards_pending_edits_and_reloads_company(self):
        from app.companies.company_form import CompanyForm
        from app.db.dao_companies import delete_partner
        app = QApplication.instance() or QApplication([])
        company = self.make_company('Usunięty wspólnik', roczne_deklaracje='[]')
        with get_conn() as conn:
            owner = conn.execute(
                "INSERT INTO udzialowcy(firma_id,imie_pierwsze,nazwisko) "
                "VALUES (%s,'Anna','Nowak') RETURNING id",
                (company,),
            ).fetchone()[0]
        form = CompanyForm(company, actor_id=1)
        form.annual_owner.setCurrentIndex(form.annual_owner.findData(owner))
        form.declaration_checks['PIT-36'].setChecked(True)
        delete_partner(owner)
        form._load_partners()
        self.assertEqual(form.annual_owner.currentData(), 0)
        self.assertFalse(form.declaration_checks['PIT-36'].isChecked())
        self.assertFalse(form._owner_annual_edits)
        form.close()

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
