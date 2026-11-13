"""Full uploaded XSDs, paper UPL and employee correspondence constraints."""
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from lxml import etree
from PyQt6.QtWidgets import QApplication
from pypdf import PdfReader, PdfWriter

from app.documents.crd_forms import validate_xml, render_xml_to_html, render_xml_to_print_html, VERSIONS
from app.documents.power_of_attorney import build_xml, reviewed_data, pesel_birth_date, resolve_employee
from tests.base import DbTestCase

OFFICE_EMAIL = 'office@example.test'

_app = QApplication.instance() or QApplication(['authority-tests'])


def data():
    return dict(name='PRZYKŁADOWA SPÓŁKA', nip='1234563218', principal_kind='legal', agreement_date='06.11.2026',
                tax_office='Pierwszy Urząd Skarbowy w Szczecinie', country='PL', street='Długa', building='5',
                postcode='71-450', city='Szczecin', pel_scope='all',
                attorney=dict(imie_pierwsze='Jan', nazwisko='Testowy', pesel='90010112345', country='PL',
                              street='Przykładowa', building='35', postcode='71-450', city='Szczecin'),
                representatives=[dict(imie_pierwsze='Anna', nazwisko='Nowak', stanowisko='Prezes zarządu')])


class PowerOfAttorneyXmlTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('app.documents.power_of_attorney.office_email', return_value=OFFICE_EMAIL)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_company_and_individual_documents_validate_against_both_full_schemas(self):
        for kind in ('UPL', 'PEL'):
            for principal in ('legal', 'person', 'other'):
                with self.subTest(kind=kind, principal=principal):
                    values = dict(data(), principal_kind=principal, principal_first_name='Anna', principal_surname='Nowak')
                    xml = build_xml(kind, values)
                    self.assertEqual(validate_xml(kind, xml).tag, '{' + VERSIONS[kind] + '}Dokument')
                    html = render_xml_to_html(kind, xml)
                    self.assertIn('Testowy', html)
                    self.assertIn(OFFICE_EMAIL, html)
                    self.assertNotIn('SignatureValue', xml.decode())

    def test_xml_scope_is_checked_and_authorizations_are_always_indefinite(self):
        for scope in ('all', 'receive', 'correspondence', 'case'):
            xml = build_xml('PEL', dict(data(), pel_scope=scope, pel_case='Rozliczenie konta', authority_until='30.11.2026'))
            self.assertNotIn(b'2026-11-30', xml)
            self.assertEqual(etree.fromstring(xml).xpath('//*[local-name()="DoOdwolania"]/text()'), ['true'])
        with self.assertRaisesRegex(ValueError, 'zakres'):
            build_xml('PEL', dict(data(), pel_scope=''))
        upl = build_xml('UPL', dict(data(), authority_until='01.01.2026'))
        self.assertNotIn(b'DataDo', upl)
        with self.assertRaisesRegex(ValueError, 'Opisz'):
            build_xml('PEL', dict(data(), pel_scope='case', pel_case=''))

    def test_print_html_removes_only_the_last_forced_page_break(self):
        raw = render_xml_to_html('PEL', build_xml('PEL', data()))
        printable = render_xml_to_print_html('PEL', build_xml('PEL', data()))
        self.assertEqual(raw.count('page-break-after:always'), 5)
        self.assertEqual(printable.count('page-break-after:always'), 4)
        self.assertIn('page-break-after:auto', printable)
        self.assertIn(OFFICE_EMAIL, printable)

    def test_optional_representative_pesel_is_omitted_without_fake_values(self):
        xml = build_xml('PEL', data())
        root = etree.fromstring(xml)
        self.assertFalse(root.xpath('//*[local-name()="Mocodawca1"]/*[local-name()="PESEL"]'))
        self.assertIn(b'Prezes', xml)
        upl = etree.fromstring(build_xml('UPL', data()))
        self.assertFalse(upl.xpath('//*[local-name()="PodpisWnioskodawcy" or local-name()="DataUwierzytelnienia"]'))

    def test_country_codes_use_the_uploaded_country_enumeration(self):
        values = dict(data(), country='NOT_A_COUNTRY')
        with self.assertRaises(etree.DocumentInvalid):
            build_xml('UPL', values)

    def test_nip_attorney_and_identity_document_alternatives_do_not_invent_a_birth_date(self):
        values = data()
        values['attorney'].update(pesel='', nip='1234563218')
        with self.assertRaisesRegex(ValueError, 'daty urodzenia'):
            build_xml('UPL', values)
        values['attorney']['birth_date'] = '01.01.1990'
        validate_xml('UPL', build_xml('UPL', values))
        values['attorney'].update(nip='', identity_number='ABC123456', identity_country='PL')
        validate_xml('PEL', build_xml('PEL', values))
        self.assertEqual(pesel_birth_date('00222912345'), '2000-02-29')
        self.assertEqual(pesel_birth_date('00223012345'), '')

    def test_paper_upl_preserves_original_two_pages_and_leaves_signatures_empty(self):
        from app.documents import upl_paper
        from app.documents.print_renderer import paint_overlay
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'UPL.pdf'
            upl_paper.write_pdf(data(), out, lambda operation, pages, dest: paint_overlay(pages, dest))
            result = PdfReader(out)
            self.assertEqual(len(result.pages), 2)
            text = ' '.join('\n'.join(p.extract_text() for p in result.pages).split())
            self.assertIn('PRZYKŁADOWA SPÓŁKA', text)
            self.assertIn('NOWAK', text)
            self.assertIn('PREZES ZARZĄDU', text)
            self.assertIn(OFFICE_EMAIL, text)
            self.assertNotIn('UPL.overlay', text)
            self.assertFalse(out.with_suffix('.overlay.pdf').exists())
            before = PdfReader(upl_paper.TEMPLATE)
            self.assertEqual(result.pages[0].mediabox, before.pages[0].mediabox)

    def test_paper_template_revision_is_checked_before_placing_fields(self):
        from app.documents import upl_paper
        with patch.object(upl_paper, 'TEMPLATE_SHA256', 'different'):
            with self.assertRaisesRegex(ValueError, 'inną wersję'):
                upl_paper.overlay_commands(data())

    def test_expiry_and_electronic_address_stay_empty_even_with_obsolete_inputs(self):
        from app.documents import upl_paper, pel_paper
        values = data()
        values['authority_until'] = 'old invalid expiry'
        values['attorney']['electronic_address'] = '/OLD/ADDRESS'
        for kind in ('UPL', 'PEL', 'PEL-PAPER', 'PEL-Z'):
            normalized = reviewed_data(values, kind)
            self.assertEqual(normalized['authority_until'], '')
            self.assertEqual(normalized['attorney']['electronic_address'], '')
            self.assertEqual(normalized['attorney']['email'], OFFICE_EMAIL)
        upl = upl_paper.overlay_commands(values)
        self.assertFalse(any(y == 593.84 and x >= 429.24 for x, y, t, w in upl[1]['text']))
        self.assertFalse(any(x == 320 and y == 135 for x, y, t, w in upl[0]['text']))
        self.assertIn(OFFICE_EMAIL, [t for x, y, t, w in upl[1]['text']])
        pel = pel_paper.overlay_commands(values)
        self.assertFalse(any(y == 252.1 and x >= 363.96 for x, y, t, w in pel[2]['text']))
        self.assertTrue(any(y == 252.1 and x < 363.96 for x, y, t, w in pel[2]['text']))

    def test_legal_forms_drive_upl_and_modern_pel_checkboxes(self):
        from app.documents.power_of_attorney import principal_kind_for_company
        from app.documents import upl_paper, pel_paper
        forms = {'JDG': 'person', 'Osoba fizyczna (KPiR)': 'person',
                 'Osoba fizyczna (Ryczałt)': 'person', 'Spółka z o.o.': 'legal',
                 'Spółka akcyjna': 'legal', 'Spółka jawna': 'other',
                 'Spółka cywilna': 'other', 'Spółka komandytowa': 'other', 'Spółka partnerska': 'other'}
        for form, kind in forms.items():
            with self.subTest(form=form):
                self.assertEqual(principal_kind_for_company(dict(forma_dzialalnosci=form, krs='123')), kind)
                values = dict(data(), principal_kind=kind, principal_first_name='Anna', principal_surname='Nowak', pel_ezus='full')
                upl = upl_paper.overlay_commands(values)
                marks = [(x, y) for x, y, t, w in upl[0]['text'] if t == 'X']
                self.assertIn((386 if kind == 'person' else 165, 488.5), marks)
                self.assertNotIn((165 if kind == 'person' else 386, 488.5), marks)
                self.assertIn((64.5, 599), marks)  # Ordinary tax-office selection stays independent of legal form.
                pel = pel_paper.overlay_commands(values)
                self.assertEqual(any(t == 'X' and y == 69.4 for x, y, t, w in pel[0]['text']), kind == 'person')
                self.assertEqual(any(t == 'X' and y == 733 for x, y, t, w in pel[1]['text']), kind != 'person')
                ezus_y = 447.3 if kind == 'person' else 378.7
                self.assertIn((46.77, ezus_y, 'X', 12), pel[2]['text'])
        self.assertEqual(principal_kind_for_company({'krs': '123'}), '')
        self.assertEqual(principal_kind_for_company({'forma_dzialalnosci': 'Inna', 'krs': '123'}), '')

    def test_modern_pel_preserves_three_pages_and_employee_office_address(self):
        from app.documents import pel_paper
        from app.documents.print_renderer import paint_overlay
        values = data()
        values['representatives'][0].update(street='Reprezentanta', building='12', city='Szczecin')
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'PEL.pdf'
            pel_paper.write_pdf(values, out, lambda op, pages, path: paint_overlay(pages, path))
            result = PdfReader(out)
            self.assertEqual(len(result.pages), 3)
            self.assertIn('NOWAK', result.pages[0].extract_text())
            self.assertIn('REPREZENTANTA', result.pages[0].extract_text())
            self.assertNotIn('CHOPINA', result.pages[0].extract_text())
            self.assertIn('CHOPINA', result.pages[1].extract_text())
            self.assertIn('PRZYKŁADOWA SPÓŁKA', ' '.join(result.pages[1].extract_text().split()))
            self.assertIn('579/26', result.pages[2].extract_text())
        with patch.object(pel_paper, 'TEMPLATE_SHA256', 'other'):
            with self.assertRaisesRegex(ValueError, 'inną wersję'):
                pel_paper.overlay_commands(values)

    def test_modern_pel_always_grants_full_zus_and_ezus_and_ignores_obsolete_restrictions(self):
        from app.documents.pel_paper import overlay_commands
        for principal in ('person', 'legal', 'other'):
            for old_scope in ('', 'case', 'receive', 'all'):
                with self.subTest(principal=principal, old_scope=old_scope):
                    values = dict(data(), principal_kind=principal, principal_first_name='Anna', principal_surname='Nowak',
                                  pel_scope=old_scope, pel_case='OLD CASE', authority_until='30.11.2026',
                                  pel_ezus='correspondence', pel_ezus_requests='OLD RESTRICTION')
                    pages = overlay_commands(values)
                    self.assertIn((46.77, 270.5, 'X', 12), pages[1]['text'])
                    self.assertNotIn((46.77, 117.8, 'X', 12), pages[1]['text'])
                    marks = {(x, y) for x, y, t, w in pages[2]['text'] if t == 'X'}
                    expected = {(46.77, 733), (46.77, 499.1), (46.77, 447.3)} if principal == 'person' else {(46.77, 733), (46.77, 378.7)}
                    self.assertEqual(marks, expected)
                    self.assertTrue(any(y == 252.1 for x, y, t, w in pages[2]['text']))
                    self.assertFalse(any('OLD' in t for p in pages for x, y, t, w in p['text']))
        pages = overlay_commands(dict(data(), pel_pit=True))
        self.assertIn((46.77, 191.1, 'X', 12), pages[1]['text'])

    def test_modern_pel_requires_representative_and_marks_only_actual_pel_z_attachment(self):
        from app.documents.pel_paper import overlay_commands
        with self.assertRaisesRegex(ValueError, 'przedstawiciela'):
            overlay_commands(dict(data(), representatives=[]))
        values = data()
        values['representatives'] *= 2
        with self.assertRaisesRegex(ValueError, 'PEL-Z'):
            overlay_commands(values)
        pages = overlay_commands(dict(values, pel_z_attached=True))
        self.assertIn((528.94, 733, 'X', 12), pages[1]['text'])
        self.assertNotIn((528.94, 733, 'X', 12), overlay_commands(data())[1]['text'])

    def test_only_empty_browser_tail_pages_are_removed_before_merging(self):
        from app.documents.print_renderer import paint_overlay, trim_empty_browser_tail
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page.pdf'
            paint_overlay([dict(size=(595, 842), text=[(50, 700, 'Treść', 250)])], path)
            writer = PdfWriter()
            writer.add_page(PdfReader(path).pages[0])
            writer.add_blank_page(width=595, height=842)
            source = BytesIO(); writer.write(source); writer.close()
            result = PdfReader(BytesIO(trim_empty_browser_tail(source.getvalue())))
            self.assertEqual(len(result.pages), 1)
            self.assertIn('Treść', result.pages[0].extract_text())

    def test_flat_pel_z_uses_the_checked_template_and_detected_field_borders(self):
        from app.documents import pel_z
        from app.documents.print_renderer import paint_overlay
        values = data()
        values['representatives'] = [dict(imie_pierwsze='Anna', nazwisko='Przykładowa', pesel='90010112345',
            street='Długa', building='5', postcode='71-450', city='Szczecin'),
            dict(imie_pierwsze='Jan', nazwisko='Przykładowy', identity_number='ABC123456',
                 street='Krótka', building='7', postcode='71-450', city='Szczecin')]
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'PEL-Z.pdf'
            pel_z.write_pdf(values, out, lambda op, pages, path: paint_overlay(pages, path))
            result = PdfReader(out)
            self.assertEqual(len(result.pages), 3)
            text = ' '.join(' '.join(p.extract_text() or '' for p in result.pages).split())
            self.assertIn('PRZYKŁADOWA', text)
            self.assertIn('PRZYKŁADOWY', text)
            self.assertIn('ABC123456', text)
            self.assertIn('SZCZECIN', text)
            self.assertFalse(out.with_suffix('.overlay.pdf').exists())
        with patch.object(pel_z, 'TEMPLATE_SHA256', 'other'):
            with self.assertRaisesRegex(ValueError, 'inną wersję'):
                pel_z.overlay_commands(values)


class EmployeeAuthorityTests(DbTestCase):
    def setUp(self):
        super().setUp()
        from app.db.dao_app_settings import set_setting
        for key, value in (('adres', 'Przykładowa 35'), ('kod', '71-450'), ('miasto', 'Szczecin'), ('kraj', 'Polska')):
            set_setting('faktury_sprzedawca_' + key, value)
        set_setting('faktury_sprzedawca_gmina', 'Szczecin')
        set_setting('faktury_sprzedawca_email', OFFICE_EMAIL)
        self.employee = self.make_employee('private@example.test', 'Jan', 'Pracownik')
        self.company = self.make_company('PRZYKŁADOWA SPÓŁKA', nip='1234563218', ulica='Długa 5', miasto='Szczecin', kod_pocztowy='71-450')

    def values(self):
        values = data()
        values['attorney'].update(employee_id=self.employee, email='private@example.test', street='Prywatna', building='99')
        return values

    def test_employee_identity_office_address_and_fixed_email_are_enforced_in_backend(self):
        values = self.values()
        resolved = resolve_employee(values)
        self.assertEqual((resolved['imie_pierwsze'], resolved['nazwisko']), ('Jan', 'Pracownik'))
        self.assertEqual((resolved['street'], resolved['building'], resolved['city']), ('Przykładowa', '35', 'Szczecin'))
        self.assertEqual(resolved['email'], OFFICE_EMAIL)
        self.assertEqual(resolved['municipality'], 'Szczecin')
        self.assertNotIn('private', repr(resolved))
        self.assertEqual(reviewed_data(dict(values, attorney=resolved), 'UPL')['attorney']['email'], OFFICE_EMAIL)
        with self.assertRaisesRegex(ValueError, 'pracownika'):
            resolve_employee(dict(values, attorney={}))

    def test_pack_contains_paper_forms_and_uses_renderer_without_word(self):
        from app.documents.contract_pack import generate_pack
        from app.documents.print_renderer import paint_overlay
        values = self.values()
        rendered_html = []
        def render(operation, source, destination):
            if operation == 'overlay':
                paint_overlay(source, destination)
            else:
                rendered_html.append(source)
                writer = PdfWriter(); writer.add_blank_page(width=595, height=842); writer.write(destination); writer.close()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'forms.pdf'
            generate_pack(self.company, 1, values, ['11_upl', '12_pel'], out, form_renderer=render)
            self.assertEqual(len(PdfReader(out).pages), 5)
            text = '\n'.join(p.extract_text() for p in PdfReader(out).pages)
            self.assertIn(OFFICE_EMAIL, text)
            self.assertNotIn('private@example.test', text)
            self.assertNotIn('PRYWATNA', text)
            self.assertEqual(rendered_html, [])
            archive = Path(directory) / 'forms.zip'
            generate_pack(self.company, 1, values, ['11_upl'], archive, form_renderer=render, docx_only=True)
            with ZipFile(archive) as bundle:
                self.assertEqual(bundle.namelist(), [f'11_upl_pracownik_{self.employee}.pdf'])
            annex = Path(directory) / 'annex.pdf'
            generate_pack(self.company, 1, values, ['13_pel_z'], annex, form_renderer=render)
            self.assertEqual(len(PdfReader(annex).pages), 3)

    def test_changing_employee_clears_previous_personal_identifiers(self):
        from app.documents.contract_pack_ui import ContractPackDialog
        dialog = ContractPackDialog(self.company, 1)
        self.addCleanup(dialog.close)
        dialog.employee.setCurrentIndex(dialog.employee.findData(self.employee))
        dialog.attorney_fields['pesel'].setText('90010112345')
        dialog.employee.setCurrentIndex(dialog.employee.findData(1))
        self.assertFalse(dialog.attorney_fields['pesel'].text())
        self.assertNotIn('email', dialog.attorney_fields)
        self.assertNotIn('street', dialog.attorney_fields)
        self.assertNotIn('electronic_address', dialog.attorney_fields)
        self.assertEqual(dialog.values()['authority_until'], '')
