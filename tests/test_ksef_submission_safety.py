"""Regresje zgodności XML i trwałego stanu wysyłki KSeF, bez wywołań MF."""
import base64
import datetime as dt
from pathlib import Path
import unittest
from unittest import mock
from xml.etree import ElementTree as ET

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.db import dao_invoices
from app.db.dao_app_settings import set_setting
from app.db.dao_conn import get_conn
from app.invoices import ksef_submission as submission, ksef_sync
from app.invoices.ksef_client import KsefClient, KsefError
from tests.base import DbTestCase


def document(**changes):
    values = dict(number='FV/001/09/2026', seller='1234567890', buyer='5252525252',
                  buyer_name='Nabywca', gross='123.00', currency='PLN', issue_date='2026-09-01', kind='VAT')
    values.update(changes)
    root = ET.Element('Faktura', xmlns='http://crd.gov.pl/wzor/2025/06/25/13775/')
    header = ET.SubElement(root, 'Naglowek')
    ET.SubElement(header, 'KodFormularza', kodSystemowy='FA (3)', wersjaSchemy='1-0E').text = 'FA'
    for tag, nip in (('Podmiot1', values['seller']), ('Podmiot2', values['buyer'])):
        identity = ET.SubElement(ET.SubElement(root, tag), 'DaneIdentyfikacyjne')
        ET.SubElement(identity, 'NIP' if nip else 'BrakID').text = nip or '1'
        ET.SubElement(identity, 'Nazwa').text = values['buyer_name'] if tag == 'Podmiot2' else 'Sprzedawca'
    fa = ET.SubElement(root, 'Fa')
    for tag, key in (('P_2', 'number'), ('P_15', 'gross'), ('KodWaluty', 'currency'),
                     ('P_1', 'issue_date'), ('RodzajFaktury', 'kind')):
        ET.SubElement(fa, tag).text = values[key]
    return ET.tostring(root, encoding='utf-8')


class SubmissionSafetyTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.firma = self.make_company('Nabywca', nip='5252525252')
        self.invoice_id = dao_invoices.create_or_update_invoice(self.firma, '2026-09', [
            {'nazwa': 'Usługa', 'ilosc': 1, 'netto': 100, 'vat': 23, 'brutto': 123}
        ], 100, 123)
        self.update(numer='FV/001/09/2026', data_wystawienia='2026-09-01', eksportowana=1)
        set_setting('ksef_nip', '1234567890')
        set_setting('ksef_srodowisko', 'test')
        self.path = Path(self._tmp) / 'invoice.xml'
        self.path.write_bytes(document())
        self.client = mock.Mock()
        self.client.send_fa3_xml.side_effect = self.upload
        patcher = mock.patch.object(submission, 'client_from_settings', return_value=self.client)
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)

    def update(self, **values):
        conn = get_conn()
        try:
            # Klucze są stałymi testów, nie danymi z dokumentów/użytkownika.
            conn.execute('UPDATE faktury SET ' + ','.join(f'{key} = %s' for key in values) + ' WHERE id = %s',
                         (*values.values(), self.invoice_id))
            conn.commit()
        finally:
            conn.close()

    def invoice(self):
        return dao_invoices.get_invoice_by_id(self.invoice_id)

    def upload(self, xml, *, on_session, on_upload, on_invoice):
        on_session('SESSION')
        on_upload()
        on_invoice('DOCUMENT')
        return {'session_reference': 'SESSION', 'invoice_reference': 'DOCUMENT', 'close_error': None}

    def send(self, **changes):
        self.path.write_bytes(document(**changes))
        return submission.send_invoice_xml(self.invoice_id, str(self.path), 1)

    def test_mismatched_amount_currency_date_kind_and_buyer_never_reach_client(self):
        for change in ({'gross': '124.00'}, {'gross': 'NaN'}, {'gross': ''}, {'currency': 'EUR'},
                       {'issue_date': '2026-09-02'}, {'issue_date': '2026-02-30'}, {'kind': 'KOR'},
                       {'buyer': '9998887766'}, {'seller': '9998887766'}, {'number': 'OTHER'}):
            with self.subTest(change=change), self.assertRaises(KsefError):
                self.send(**change)
        self.factory.assert_not_called()
        self.assertFalse(self.invoice()['ksef_status'])

    def test_draft_is_rejected_by_backend(self):
        self.update(eksportowana=0)
        with self.assertRaisesRegex(KsefError, 'wystawioną'):
            self.send()
        self.factory.assert_not_called()

    def test_certificate_without_token_configures_the_application(self):
        from app.invoices import ksef_credentials
        from tests.test_ksef_credentials import make_pair
        certificate, key = make_pair()
        ksef_credentials.import_cert_and_key(
            certificate.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()), None,
        )
        self.assertTrue(ksef_sync.is_configured())
        client = ksef_sync.client_from_settings()
        self.assertEqual(client.ksef_token, '')
        self.assertEqual(client.certificate.fingerprint(hashes.SHA256()), certificate.fingerprint(hashes.SHA256()))

    def test_buyer_snapshot_takes_precedence_over_changed_company(self):
        self.update(nabywca_pelna='Dawna nazwa', nabywca_nip='9998887766')
        self.send(buyer='9998887766')
        self.assertEqual(self.invoice()['ksef_status'], 'oczekuje')

    def test_manual_invoice_buyer_is_checked(self):
        self.update(firma_id=None, reczna=1, nabywca_pelna='Kontrahent', nabywca_nip='9998887766')
        with self.assertRaisesRegex(KsefError, 'NIP nabywcy'):
            self.send()
        self.factory.assert_not_called()
        self.send(buyer='9998887766')

    def test_individual_buyer_identity_is_checked_without_nip(self):
        self.update(firma_id=None, reczna=1, nabywca_pelna='Osoba Testowa', nabywca_nip=None)
        with self.assertRaisesRegex(KsefError, 'bez NIP'):
            self.send(buyer=None, buyer_name='Inna Osoba')
        with self.assertRaisesRegex(KsefError, 'bez NIP'):
            self.send(buyer='9998887766', buyer_name='Osoba Testowa')
        self.send(buyer=None, buyer_name='Osoba Testowa')

    def test_failure_before_upload_allows_safe_retry(self):
        self.client.send_fa3_xml.side_effect = KsefError('Authentication failed')
        with self.assertRaisesRegex(KsefError, 'Authentication failed'):
            self.send()
        self.assertEqual(self.invoice()['ksef_status'], 'blad_przed_wysylka')
        self.client.send_fa3_xml.side_effect = self.upload
        self.send()
        self.assertEqual(self.invoice()['ksef_status'], 'oczekuje')

    def test_failure_after_session_before_upload_allows_retry(self):
        def fail(xml, **callbacks):
            callbacks['on_session']('EMPTY-SESSION')
            raise KsefError('access expired before upload')
        self.client.send_fa3_xml.side_effect = fail
        with self.assertRaises(KsefError):
            self.send()
        self.assertEqual(self.invoice()['ksef_status'], 'blad_przed_wysylka')
        self.client.send_fa3_xml.side_effect = self.upload
        self.send()

    def test_retry_clears_old_references_before_new_session_and_unknown_upload_blocks(self):
        self.update(ksef_status='odrzucona', ksef_referencja_sesji='OLD-SESSION', ksef_referencja_faktury='OLD-DOCUMENT')
        def lose_response(xml, **callbacks):
            self.assertIsNone(self.invoice()['ksef_referencja_faktury'])
            self.assertIsNone(self.invoice()['ksef_referencja_sesji'])
            callbacks['on_session']('NEW-SESSION')
            callbacks['on_upload']()
            raise KsefError('upload timeout')
        self.client.send_fa3_xml.side_effect = lose_response
        with self.assertRaisesRegex(KsefError, 'upload timeout'):
            self.send()
        row = self.invoice()
        self.assertEqual(row['ksef_status'], 'do_sprawdzenia')
        self.assertEqual(row['ksef_referencja_sesji'], 'NEW-SESSION')
        self.assertIsNone(row['ksef_referencja_faktury'])
        with self.assertRaisesRegex(KsefError, 'już rozpoczęta'):
            self.send()
        self.client.send_fa3_xml.assert_called_once()

    def test_status_is_bound_to_original_environment_and_nip(self):
        self.send()
        set_setting('ksef_srodowisko', 'prod')
        with self.assertRaisesRegex(KsefError, 'TEST'):
            submission.refresh_invoice_submission(self.invoice_id, 1)
        set_setting('ksef_srodowisko', 'test')
        set_setting('ksef_nip', '9998887766')
        with self.assertRaisesRegex(KsefError, '1234567890'):
            submission.refresh_invoice_submission(self.invoice_id, 1)
        self.client.invoice_submission_status.assert_not_called()

    def test_lost_upload_response_is_reconciled_by_hash_without_resending(self):
        def lose_response(xml, **callbacks):
            callbacks['on_session']('SESSION')
            callbacks['on_upload']()
            raise KsefError('upload timeout')
        self.client.send_fa3_xml.side_effect = lose_response
        with self.assertRaises(KsefError):
            self.send()
        document_hash = self.invoice()['ksef_hash_faktury']
        self.client.find_submission_by_hash.return_value = {
            'referenceNumber': 'RECOVERED', 'invoiceHash': document_hash,
            'status': {'code': 200}, 'ksefNumber': 'KSEF-NUMBER',
        }
        submission.refresh_invoice_submission(self.invoice_id, 1)
        self.client.find_submission_by_hash.assert_called_once_with('SESSION', document_hash)
        self.assertEqual(self.invoice()['ksef_numer'], 'KSEF-NUMBER')
        self.assertEqual(self.invoice()['ksef_status'], 'przyjeta')
        self.client.send_fa3_xml.assert_called_once()

    def test_processing_status_does_not_assign_ksef_number(self):
        self.send()
        self.client.invoice_submission_status.return_value = {'status': {'code': 150}}
        submission.refresh_invoice_submission(self.invoice_id, 1)
        self.assertFalse(self.invoice()['ksef_numer'])
        self.assertEqual(self.invoice()['ksef_status'], 'oczekuje')

    def test_transport_completion_after_status_acceptance_does_not_reset_status(self):
        def submit_then_refresh(xml, **callbacks):
            result = self.upload(xml, **callbacks)
            self.client.invoice_submission_status.return_value = {'status': {'code': 200}, 'ksefNumber': 'KSEF-OK'}
            submission.refresh_invoice_submission(self.invoice_id, 1)
            return result
        self.client.send_fa3_xml.side_effect = submit_then_refresh
        self.send()
        self.assertEqual(self.invoice()['ksef_status'], 'przyjeta')

    def test_transport_error_after_status_acceptance_does_not_reset_status(self):
        def submit_then_fail(xml, **callbacks):
            self.upload(xml, **callbacks)
            self.client.invoice_submission_status.return_value = {'status': {'code': 200}, 'ksefNumber': 'KSEF-OK'}
            submission.refresh_invoice_submission(self.invoice_id, 1)
            raise RuntimeError('late close failure')
        self.client.send_fa3_xml.side_effect = submit_then_fail
        with self.assertRaisesRegex(RuntimeError, 'late close failure'):
            self.send()
        self.assertEqual(self.invoice()['ksef_status'], 'przyjeta')

    def test_old_status_response_cannot_overwrite_new_attempt(self):
        self.send()
        def stale_response(*args):
            self.update(ksef_referencja_sesji='NEW-SESSION', ksef_referencja_faktury='NEW-DOC', ksef_status='oczekuje')
            return {'status': {'code': 450}}
        self.client.invoice_submission_status.side_effect = stale_response
        with self.assertRaisesRegex(KsefError, 'zmienił się'):
            submission.refresh_invoice_submission(self.invoice_id, 1)
        self.assertEqual(self.invoice()['ksef_status'], 'oczekuje')


class MetadataMatchingTests(unittest.TestCase):
    def setUp(self):
        self.meta = {'invoiceNumber': 'FV/09', 'grossAmount': 123, 'issueDate': '2026-09-01',
                     'buyer': {'identifier': {'value': '5252525252'}}}
        self.rows = [{'id': 1, 'numer': 'FV/08', 'firma_nip': '5252525252', 'kwota_brutto': 123, 'miesiac': '2026-08'},
                     {'id': 2, 'numer': 'FV/09', 'firma_nip': '5252525252', 'kwota_brutto': 123, 'miesiac': '2026-09'}]

    def test_repeated_monthly_amount_matches_exact_invoice_number(self):
        self.assertEqual(ksef_sync._take_match(self.rows, self.meta)['id'], 2)

    def test_different_existing_number_is_not_overwritten(self):
        self.meta['invoiceNumber'] = 'OTHER'
        self.assertIsNone(ksef_sync._take_match(self.rows, self.meta))

    def test_unnumbered_fallback_requires_unique_candidate_in_month(self):
        for row in self.rows:
            row['numer'] = ''
        self.assertEqual(ksef_sync._take_match(self.rows, self.meta)['id'], 2)
        self.rows.append(dict(self.rows[0], id=3))
        self.meta['issueDate'] = '2026-08-01'
        self.assertIsNone(ksef_sync._take_match(self.rows, self.meta))


class TransportSafetyTests(unittest.TestCase):
    def client(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        who = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'MF test')])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(who).issuer_name(who).public_key(key.public_key())
                .serial_number(1).not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
        client = KsefClient('1234567890', '', 'test', certificate=cert, private_key=key)
        client._access = lambda: 'ACCESS'
        return client, cert

    def test_upload_callback_precedes_invoice_post_and_failure_closes_session(self):
        client, cert = self.client()
        events = []
        def request(method, path, **kwargs):
            if path == '/security/public-key-certificates':
                return mock.Mock(json=lambda: [{'usage': ['SymmetricKeyEncryption'],
                    'certificate': base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()}])
            if path == '/sessions/online':
                return mock.Mock(json=lambda: {'referenceNumber': 'S'})
            events.append(path)
            if path.endswith('/invoices'):
                raise KsefError('connection lost')
            return mock.Mock()
        client._request = request
        with self.assertRaisesRegex(KsefError, 'connection lost'):
            client.send_fa3_xml(b'<xml/>', on_upload=lambda: events.append('upload'))
        self.assertEqual(events, ['upload', '/sessions/online/S/invoices', '/sessions/online/S/close'])

    def test_callback_failure_before_upload_still_closes_open_session(self):
        client, cert = self.client()
        paths = []
        def request(method, path, **kwargs):
            paths.append(path)
            if path == '/security/public-key-certificates':
                return mock.Mock(json=lambda: [{'usage': ['SymmetricKeyEncryption'],
                    'certificate': base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()}])
            return mock.Mock(json=lambda: {'referenceNumber': 'S'})
        client._request = request
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
            client.send_fa3_xml(b'<xml/>', on_session=mock.Mock(side_effect=RuntimeError('database unavailable')))
        self.assertNotIn('/sessions/online/S/invoices', paths)
        self.assertIn('/sessions/online/S/close', paths)

    def test_session_hash_lookup_follows_continuation(self):
        client, _ = self.client()
        client._request = mock.Mock(side_effect=[
            mock.Mock(json=lambda: {'invoices': [], 'continuationToken': 'NEXT'}),
            mock.Mock(json=lambda: {'invoices': [{'referenceNumber': 'FOUND', 'invoiceHash': 'HASH'}]}),
        ])
        self.assertEqual(client.find_submission_by_hash('S', 'HASH')['referenceNumber'], 'FOUND')
        self.assertEqual(client._request.call_args.kwargs['headers'], {'x-continuation-token': 'NEXT'})
