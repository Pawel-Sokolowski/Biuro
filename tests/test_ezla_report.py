from unittest import TestCase
from unittest.mock import MagicMock, patch
from tempfile import TemporaryDirectory
from pathlib import Path

from app.integrations.ezla.report import parse_report, read_report, MAX_BYTES
from app.integrations.ezla.preview import preview_report


def certificate(status='Wystawione', number='123456', identifier='00000000000', cancelled='Nie'):
    # Synthetic values. Field structure follows the report example supplied by the user.
    return f'''<dokumentyEzla>
        <daneUbezpieczonego><identyfikator><typ>PESEL</typ><wartosc>{identifier}</wartosc></identyfikator>
        <imie>JAN</imie><nazwisko>PRZYKŁAD</nazwisko></daneUbezpieczonego>
        <daneDokumentu><seria>ZZ</seria><status>{status}</status><numer>{number}</numer>
        <dataWystawienia>2018-04-20</dataWystawienia><dataZus>2018-04-20</dataZus>
        <okresZwolnienia><dataOd>2018-04-20</dataOd><dataDo>2018-04-29</dataDo></okresZwolnienia>
        <okresWSzpitalu><dataOd/><dataDo/></okresWSzpitalu><kodChorobyA/>
        <czyAnulowane>{cancelled}</czyAnulowane></daneDokumentu></dokumentyEzla>'''


def report(*rows, declared=None):
    count = len(rows) if declared is None else declared
    return ('''<?xml version="1.0" encoding="UTF-8"?><raportEzla><daneRaportu>
        <dataWygenerowania>2018-08-01</dataWygenerowania><platnik><nip>1112223344</nip>
        <nazwaSkrocona>FIRMA TESTOWA</nazwaSkrocona>
        <identyfikator><typ>D</typ><wartosc/></identyfikator></platnik>
        <typRaportu>Jednorazowy</typRaportu><dolaczDokumentyXml>false</dolaczDokumentyXml>
        <liczbaDokumentowEzla>''' + str(count) + '</liczbaDokumentowEzla></daneRaportu>' + ''.join(rows) + '</raportEzla>').encode('utf-8')


class EzlaReportTests(TestCase):
    def test_report_maps_payer_and_preserves_each_status_without_importing(self):
        value = parse_report(report(certificate(), certificate('Anulowane', '2', cancelled='Tak'),
                                    certificate('Skorygowane', '3')))
        self.assertEqual(value.nip, '1112223344')
        self.assertEqual(value.generated_at, '2018-08-01')
        self.assertEqual([row.status for row in value.certificates], ['Wystawione', 'Anulowane', 'Skorygowane'])
        self.assertEqual(value.certificates[0].employee_name, 'JAN PRZYKŁAD')
        self.assertEqual(value.certificates[0].start, '2018-04-20')
        self.assertEqual(value.certificates[0].masked_identifier, '*******0000')
        self.assertIn('nie zaimportowano', value.certificates[0].review)
        self.assertIn('anulowania', value.certificates[1].review)
        self.assertIn('powiązany', value.certificates[2].review)
        self.assertNotIn('00000000000', repr(value.certificates[0]))

    def test_official_backdated_status_is_recognized_but_still_requires_review(self):
        row = parse_report(report(certificate('Wsteczne – uzasadnione'))).certificates[0]
        self.assertEqual(row.status, 'Wsteczne - uzasadnione')
        self.assertFalse(row.issues)
        self.assertIn('wsteczne', row.review)

    def test_missing_identity_unknown_status_and_conflict_need_review(self):
        value = parse_report(report(certificate(identifier=''), certificate('Nowy status', '2'),
                                    certificate(number='3', cancelled='Tak')))
        self.assertIn('PESEL', value.certificates[0].review)
        self.assertIn('Nieznany status', value.certificates[1].review)
        self.assertIn('sprzeczne', value.certificates[2].review)

    def test_duplicate_or_revised_identity_does_not_discard_either_record(self):
        rows = parse_report(report(certificate(), certificate('Anulowane'))).certificates
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('Powtórzony' in row.review for row in rows))
        other_employee = parse_report(report(certificate(), certificate(identifier='11111111111')))
        self.assertFalse(other_employee.certificates[1].issues)

    def test_broken_pdf_copy_dtd_and_wrong_document_count_are_rejected(self):
        valid = report(certificate())
        examples = [valid[:-13] + b'<raportEzla>',
                    valid.replace(b'</okresZwolnienia>', b'</okresZwolnienia> 6'),
                    valid.replace(b'<wartosc>00000000000</wartosc>', '<wartosc/>…<wartosc/>'.encode()),
                    valid.replace(b'<raportEzla>', b'<!DOCTYPE raportEzla [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><raportEzla>'),
                    report(certificate(), declared=3)]
        for data in examples:
            with self.subTest(data_length=len(data)), self.assertRaises(ValueError):
                parse_report(data)
        self.assertEqual(parse_report(report()).certificates, [])

    def test_invalid_period_is_flagged_instead_of_becoming_a_valid_absence(self):
        invalid = certificate().replace('2018-04-29', '2018-04-01')
        value = parse_report(report(invalid)).certificates[0]
        self.assertIn('wcześniejszy', value.review)
        invalid = certificate().replace('2018-04-29', '2018-02-31')
        self.assertIn('Nieprawidłowa data', parse_report(report(invalid)).certificates[0].review)

    def test_native_windows_xml_encoding_and_bounded_file_read(self):
        data = report(certificate()).decode().replace('UTF-8', 'windows-1250').encode('cp1250')
        self.assertEqual(parse_report(data).certificates[0].employee_name, 'JAN PRZYKŁAD')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'raport.xml'
            path.write_bytes(b' ' * (MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                read_report(path)


class EzlaPreviewTests(TestCase):
    def _connection(self, companies):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = companies
        factory = MagicMock()
        factory.return_value.__enter__.return_value = conn
        return factory

    def test_company_and_database_are_resolved_only_by_payer_nip(self):
        parsed = parse_report(report(certificate()))
        company = {'id': 9, 'nazwa': 'Test', 'nip': '111-222-33-44'}
        config = {'sources': [{'nip': parsed.nip, 'database': 'EnovaTest'}], 'password_enc': 'never-return-this'}
        with patch('app.integrations.ezla.preview.require_access'), \
             patch('app.integrations.ezla.preview.read_report', return_value=parsed), \
             patch('app.integrations.ezla.preview.assigned_company_ids', return_value={9}), \
             patch('app.integrations.ezla.preview.get_conn', self._connection([company])), \
             patch('app.integrations.enova.connection_settings.load_connection', return_value=config):
            value = preview_report(5, 'local.xml')
        self.assertEqual(value['company']['id'], 9)
        self.assertEqual(value['database'], 'EnovaTest')
        self.assertNotIn('never-return-this', repr(value))

    def test_inaccessible_or_ambiguous_company_is_not_auto_selected(self):
        parsed = parse_report(report(certificate()))
        company = {'id': 9, 'nazwa': 'Test', 'nip': parsed.nip}
        for companies in ([], [company, {**company, 'id': 10}]):
            with patch('app.integrations.ezla.preview.require_access'), \
                 patch('app.integrations.ezla.preview.read_report', return_value=parsed), \
                 patch('app.integrations.ezla.preview.assigned_company_ids', return_value={9, 10}), \
                 patch('app.integrations.ezla.preview.get_conn', self._connection(companies)), \
                 self.assertRaises(ValueError):
                preview_report(5, 'local.xml')

    def test_access_is_checked_before_reading_the_report(self):
        with patch('app.integrations.ezla.preview.require_access', side_effect=PermissionError), \
             patch('app.integrations.ezla.preview.read_report') as read, self.assertRaises(PermissionError):
            preview_report(5, 'local.xml')
        read.assert_not_called()
