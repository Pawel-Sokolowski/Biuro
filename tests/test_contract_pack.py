"""Supplied DOCX templates, PDF order and safe output replacement."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn
from pypdf import PdfReader, PdfWriter

from app.documents import contract_pack as pack
from tests.base import DbTestCase


def pdf(path, widths):
    writer = PdfWriter()
    for width in widths:
        writer.add_blank_page(width=width, height=842)
    writer.write(path)
    writer.close()


class ContractTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = dict(name='PPHU Jan Kowalski & Synowie', address='Długa 5/2, 71-450 Szczecin',
                         nip='8522605060', krs='0000123456', phone='123456789', email='jan@example.test',
                         agreement_date='06.11.2026', main_agreement_date='01.10.2026', accounting='kpir',
                         payroll='kadry', invoicing=True, representatives=[dict(imie_pierwsze='Jan', nazwisko='Kowalski',
                         pesel='12345678901', nr_telefonu='987654321')], document_limit='50', insured_limit='7',
                         extra_document_price='5,00', first_period='11.2026', monthly_fee='450,00',
                         tax_office='Pierwszy US w Szczecinie', pep_person='Jan Kowalski',
                         authorized_person='Anna Nowak, ABC123456, Prezydent Szczecina')

    def fill(self, key):
        path = self.root / (key + '.docx')
        pack.fill_template(key, self.data, path)
        return Document(path)

    def test_all_supplied_templates_are_valid_and_preserve_images_and_tables(self):
        for key, _, _ in pack.DOCUMENTS:
            with self.subTest(key=key):
                result = self.fill(key)
                original = Document(pack.TEMPLATES / (key + '.docx'))
                self.assertEqual(len(result.tables), len(original.tables))
                self.assertEqual(len(result.inline_shapes), len(original.inline_shapes))
                self.assertEqual(result.sections[0].page_width, original.sections[0].page_width)
                self.assertEqual(result.sections[0].page_height, original.sections[0].page_height)

    def test_client_representative_dates_and_real_word_checkbox_state(self):
        result = self.fill('00_framework')
        table = result.tables[0]
        self.assertEqual(table.cell(0, 1).text, self.data['name'])
        self.assertEqual(table.cell(2, 3).text, self.data['email'])
        self.assertEqual(table.cell(7, 3).text, 'Kowalski')
        self.assertIn('06.11.2026', result.paragraphs[1].text)
        checks = {}
        for paragraph in result.paragraphs:
            state = paragraph._p.find('.//' + qn('w14:checked'))
            if state is not None:
                checks[paragraph.text.strip()] = state.get(qn('w14:val'))
        self.assertEqual(checks['Podatkowej Księgi Przychodów i Rozchodów'], '1')
        self.assertEqual(checks['Ksiąg Rachunkowych'], '0')
        self.assertEqual(checks['Pełnych usług kadrowo-płacowych'], '1')
        processing = self.fill('03_processing')
        self.assertIn('06.11.2026', processing.paragraphs[1].text)
        text = '\n'.join(p.text for p in processing.paragraphs)
        self.assertIn('01.10.2026 r.', text)
        self.assertNotIn('09.02.2026 r.', text)

    def test_representative_role_is_printed_in_both_agreements_and_pesel_is_optional(self):
        self.data['representatives'] = [dict(imie_pierwsze='Anna', nazwisko='Nowak', stanowisko='Prezes zarządu', pesel='')]
        for key in ('00_framework', '03_processing'):
            with self.subTest(key=key):
                table = self.fill(key).tables[0]
                self.assertIn('Anna Nowak — Prezes zarządu', table.cell(6, 0).text)
                self.assertEqual(table.cell(8, 1).text, '')
                self.assertEqual(table.cell(7, 3).text, 'Nowak')

    def test_parameters_fee_pep_and_authorization_are_filled_without_answering_declarations(self):
        result = self.fill('04_checklist')
        self.assertEqual(result.tables[0].cell(0, 1).text, '50')
        self.assertEqual(result.tables[0].cell(3, 1).text, '11.2026')
        self.assertEqual(result.tables[1].cell(19, 1).text, self.data['tax_office'])
        self.assertEqual(self.fill('02_prices').tables[0].cell(1, 1).text, '450,00')
        pep = self.fill('05_pep')
        self.assertIn('Jan Kowalski', pep.paragraphs[1].text)
        self.assertFalse(any(n.get(qn('w14:val')) == '1' for n in pep.element.iter(qn('w14:checked'))))
        authorization = self.fill('09_authorization')
        self.assertEqual(authorization.paragraphs[8].text, self.data['authorized_person'])

    def test_pdf_merge_preserves_order_and_inserts_duplex_separator_only_between_documents(self):
        first, second, out = [self.root / (s + '.pdf') for s in ('first', 'second', 'out')]
        pdf(first, [595])
        pdf(second, [600, 601])
        pack.merge_pdfs([first, second], out, duplex=True)
        self.assertEqual([float(p.mediabox.width) for p in PdfReader(out).pages], [595, 595, 600, 601])
        pack.merge_pdfs([first, second], out)
        self.assertEqual(len(PdfReader(out).pages), 3)

    def test_context_keeps_full_legal_name_and_does_not_assume_shareholders_represent_the_company(self):
        context = pack.company_context(dict(nazwa='Full legal name', nazwa_skrocona='Short', ulica='Długa',
                                       adres_budynek='5', adres_lokal='2', miasto='Szczecin', kod_pocztowy='71-450'),
                                       [dict(imie_pierwsze='Jan', nazwisko='Kowalski', ulica='Krótka', nr_domu='3')])
        self.assertEqual(context['name'], 'Full legal name')
        self.assertEqual(context['address'], 'Długa 5/2, 71-450 Szczecin')
        self.assertEqual(context['representatives'], [])
        self.assertEqual(context['partners'][0]['adres_zamieszkania'], 'Krótka 3')
        contact = pack.company_context({'nazwa': 'Firma'}, contacts=[dict(imie='Anna', nazwisko='Nowak',
            telefon='123456789', rola='Prezes zarządu')])['representative_candidates'][0]
        self.assertEqual((contact['imie_pierwsze'], contact['stanowisko']), ('Anna', 'Prezes zarządu'))

    def test_converter_missing_or_not_producing_output_is_reported(self):
        with patch.object(pack.os, 'name', 'posix'), patch.object(pack.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'Word'):
                pack.convert_to_pdf([], self.root / 'pdf')
        with patch.object(pack.os, 'name', 'posix'), patch.object(pack.shutil, 'which', return_value='/office'), \
             patch.object(pack.subprocess, 'run'):
            with self.assertRaisesRegex(RuntimeError, 'poprawny PDF'):
                pack.convert_to_pdf([self.root / 'first.docx'], self.root / 'pdf')


class ContractPackTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company_id = self.make_company('PPHU Jan Kowalski', nazwa_skrocona='Kowalski Jan', ulica='Długa 5', miasto='Szczecin')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = dict(agreement_date='06.11.2026')

    def test_docx_bundle_uses_template_order_and_contains_filled_legal_name(self):
        out = self.root / 'pack.zip'
        pack.generate_pack(self.company_id, 1, self.data, ['03_processing', '00_framework'], out, docx_only=True)
        with ZipFile(out) as bundle:
            self.assertEqual(bundle.namelist(), ['00_framework.docx', '03_processing.docx'])
            with bundle.open('00_framework.docx') as document:
                self.assertEqual(Document(document).tables[0].cell(0, 1).text, 'PPHU Jan Kowalski')

    def test_pdf_pipeline_merges_extra_forms_and_keeps_existing_output_on_failure(self):
        out, extra = self.root / 'pack.pdf', self.root / 'UPL-1.pdf'
        pdf(extra, [612])
        def convert(paths, directory):
            directory.mkdir()
            result = []
            for path in paths:
                dest = directory / (path.stem + '.pdf')
                pdf(dest, [595])
                result.append(dest)
            return result
        with patch.object(pack, 'convert_to_pdf', side_effect=convert):
            pack.generate_pack(self.company_id, 1, self.data, ['00_framework'], out, extra_pdfs=[extra])
        self.assertEqual([float(p.mediabox.width) for p in PdfReader(out).pages], [595, 612])
        before = out.read_bytes()
        with patch.object(pack, 'convert_to_pdf', side_effect=RuntimeError('conversion failed')):
            with self.assertRaises(RuntimeError):
                pack.generate_pack(self.company_id, 1, self.data, ['00_framework'], out)
        self.assertEqual(out.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.biuro-komplet-*')))

    def test_unknown_document_and_missing_access_are_rejected_before_writing(self):
        out = self.root / 'pack.pdf'
        with self.assertRaises(PermissionError):
            pack.generate_pack(self.company_id, None, self.data, ['00_framework'], out)
        with self.assertRaises(ValueError):
            pack.generate_pack(self.company_id, 1, self.data, ['../secret'], out)
        self.assertFalse(out.exists())

    def test_unchanged_terms_use_the_supplied_pdf_without_word_repagination(self):
        out = self.root / 'terms.pdf'
        with patch.object(pack, 'convert_to_pdf') as convert:
            pack.generate_pack(self.company_id, 1, self.data, ['01_terms'], out)
        convert.assert_not_called()
        original = PdfReader(pack.STATIC_PDFS['01_terms'])
        generated = PdfReader(out)
        self.assertEqual(len(generated.pages), 9)
        self.assertEqual([p.extract_text() for p in generated.pages], [p.extract_text() for p in original.pages])

    def test_dialog_defaults_to_onboarding_pack_and_can_save_without_pdf_converter(self):
        from PyQt6.QtCore import QDate
        from PyQt6.QtWidgets import QApplication
        from app.documents.contract_pack_ui import ContractPackDialog
        app = QApplication.instance() or QApplication(['contract-pack-tests'])
        dialog = ContractPackDialog(self.company_id, 1)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.values()['name'], 'PPHU Jan Kowalski')
        self.assertFalse(dialog.documents['10_termination'].isChecked())
        self.assertEqual(sum(c.isChecked() for c in dialog.documents.values()), 9)
        dialog.agreement_date.setDate(QDate(2026, 10, 1))
        self.assertEqual(dialog.main_date.date(), QDate(2026, 10, 1))
        dialog.main_date.setDate(QDate(2026, 9, 1))
        dialog.agreement_date.setDate(QDate(2026, 10, 2))
        self.assertEqual(dialog.main_date.date(), QDate(2026, 9, 1))
        out = self.root / 'pack.zip'
        with patch('app.documents.contract_pack_ui.QFileDialog.getSaveFileName', return_value=(str(out), '')), \
             patch.object(dialog.runner, 'submit') as submit:
            dialog._generate(True)
            self.assertTrue(dialog._busy)
            # A second click during generation cannot queue another export.
            dialog._generate(True)
            self.assertEqual(submit.call_count, 1)
            result = submit.call_args.args[0]()
            self.assertEqual(result, str(out))
        dialog._set_busy(False)
        self.assertTrue(out.exists())
        self.assertIsNotNone(app)

    def test_representative_can_be_chosen_edited_or_cleared_with_optional_pesel(self):
        from PyQt6.QtWidgets import QApplication
        from app.db.dao_companies import save_partner
        from app.documents.contract_pack_ui import ContractPackDialog
        app = QApplication.instance() or QApplication(['contract-representative-tests'])
        save_partner(self.company_id, dict(imie_pierwsze='Anna', nazwisko='Nowak', telefon='123456789', stanowisko='Prezes zarządu'))
        dialog = ContractPackDialog(self.company_id, 1)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.representative_choices[0].currentIndex(), 0)
        dialog.representative_choices[0].setCurrentIndex(1)
        person = dialog.values()['representatives'][0]
        self.assertEqual((person['imie_pierwsze'], person['nazwisko'], person['pesel'], person['nr_telefonu']),
                         ('Anna', 'Nowak', '', '123456789'))
        # A role can always be supplied/changed, even if absent from the card.
        dialog.representatives[0]['stanowisko'].setCurrentText('Członek zarządu')
        self.assertEqual(dialog.values()['representatives'][0]['stanowisko'], 'Członek zarządu')
        self.assertTrue(dialog.agreement_date.calendarPopup())
        self.assertTrue(dialog.agreement_date.calendarWidget().isNavigationBarVisible())
        dialog.representative_choices[0].setCurrentIndex(0)
        self.assertTrue(all(not v for v in dialog.values()['representatives'][0].values()))
        self.assertIsNotNone(app)
