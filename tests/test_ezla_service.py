import base64
from datetime import date, timedelta
from io import BytesIO
from unittest import TestCase
from unittest.mock import MagicMock, patch
import pyzipper
from lxml import etree
import requests
from app.integrations.ezla.service import (fetch_reports, parse_response, decode_archive, request_body,
    ServiceError, SOAP_NS, ZUS_NS, ENDPOINT, TRANSPORT_LOGIN)
from app.integrations.ezla.sync import since_date
from tests.test_ezla_report import report, certificate

NIP = '1112223344'


def encrypted_zip(xml, password='zip-secret'):
    out = BytesIO()
    with pyzipper.AESZipFile(out, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(password.encode())
        archive.writestr('raport.xml', xml)
        archive.writestr('copy.xml', '<notTheReport/>')
    return out.getvalue()


def soap(data=None, code='0'):
    contents = '' if data is None else ('<raporty><raport><dataWygenerowania>2018-08-01</dataWygenerowania>'
        '<zawartosc>' + base64.b64encode(data).decode() + '</zawartosc></raport></raporty>')
    return (f'<s:Envelope xmlns:s="{SOAP_NS}"><s:Body><z:PobierzRaportyResponse xmlns:z="{ZUS_NS}">'
            f'<kod>{code}</kod><opis>Untrusted remote error</opis>{contents}</z:PobierzRaportyResponse>'
            '</s:Body></s:Envelope>').encode()


class EzlaSoapTests(TestCase):
    def test_request_uses_dedicated_credentials_and_escapes_xml(self):
        today = date(2026, 11, 12)
        xml = request_body(NIP, 'mail&office', '<service-secret>', today - timedelta(days=30), today)
        root = etree.fromstring(xml)
        operation = root.find('{' + SOAP_NS + '}Body')[0]
        self.assertEqual(operation.tag, '{' + ZUS_NS + '}PobierzRaporty')
        self.assertEqual(operation.findtext('login'), 'mail&office')
        self.assertEqual(operation.findtext('haslo'), '<service-secret>')
        self.assertEqual(operation.findtext('dataOd'), '2026-10-13')
        for since in (today - timedelta(days=31), today + timedelta(days=1)):
            with self.assertRaises(ValueError):
                request_body(NIP, 'login', 'password', since, today)

    def test_official_response_aes_archive_and_payer_are_validated(self):
        xml = report(certificate())
        results = parse_response(soap(encrypted_zip(xml)), 'zip-secret', NIP)
        self.assertEqual(results[0].xml, xml)
        self.assertEqual(results[0].generated_at, '2018-08-01')
        self.assertEqual(parse_response(soap(), 'zip-secret', NIP), [])
        with self.assertRaises(ServiceError) as wrong:
            parse_response(soap(encrypted_zip(xml)), 'wrong-zip-secret', NIP)
        self.assertEqual(wrong.exception.code, 'archive')
        with self.assertRaises(ServiceError) as payer:
            decode_archive(encrypted_zip(xml), 'zip-secret', '9999999999')
        self.assertEqual(payer.exception.code, 'payer')

    def test_zus_errors_do_not_expose_untrusted_remote_descriptions(self):
        for code in ('100', '101', '200', 'unknown'):
            with self.subTest(code=code), self.assertRaises(ServiceError) as error:
                parse_response(soap(code=code), 'zip-secret', NIP)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn('Untrusted remote error', str(error.exception))
        for data in (b'<broken', soap().replace(b'<s:Envelope', b'<!DOCTYPE x><s:Envelope')):
            with self.assertRaises(ServiceError):
                parse_response(data, 'zip-secret', NIP)

    def test_transport_is_bounded_uses_public_basic_auth_and_never_redirects(self):
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [soap()]
        with patch('app.integrations.ezla.service.requests.post') as post:
            post.return_value.__enter__.return_value = response
            self.assertEqual(fetch_reports(NIP, 'dedicated-login', 'payer-secret', 'zip-secret',
                                          date(2026, 11, 1), today=date(2026, 11, 12)), [])
        args, kwargs = post.call_args
        self.assertEqual(args, (ENDPOINT,))
        self.assertEqual(kwargs['auth'], (TRANSPORT_LOGIN, TRANSPORT_LOGIN))
        self.assertFalse(kwargs['allow_redirects'])
        self.assertNotIn('verify', kwargs)  # Requests verifies TLS by default.
        self.assertNotIn('payer-secret', repr(kwargs['headers']))
        with patch('app.integrations.ezla.service.requests.post', side_effect=requests.Timeout('payer-secret')):
            with self.assertRaises(ServiceError) as error:
                fetch_reports(NIP, 'login', 'payer-secret', 'zip-secret', date(2026, 11, 1), today=date(2026, 11, 12))
        self.assertEqual(error.exception.code, 'timeout')
        self.assertNotIn('payer-secret', str(error.exception))

    def test_cursor_overlaps_and_is_clamped_to_the_30_day_window(self):
        today = date(2026, 11, 12)
        self.assertEqual(since_date({}, today), date(2026, 10, 13))
        self.assertEqual(since_date({'last_success': '2026-11-10T12:00:00'}, today), date(2026, 11, 9))
        self.assertEqual(since_date({'last_success': '2020-01-01'}, today), date(2026, 10, 13))
        self.assertEqual(since_date({'last_success': '2099-01-01'}, today), today)

    def test_http_status_and_safe_guidance_survive_without_echoing_server_body(self):
        for status in (301,401,403,404,407,429,500,502,503,504):
            response = MagicMock(status_code=status)
            response.iter_content.return_value = [b'<html>payer-secret zip-secret proxy-token</html>']
            with self.subTest(status=status), patch('app.integrations.ezla.service.requests.post') as post:
                post.return_value.__enter__.return_value = response
                with self.assertRaises(ServiceError) as caught:
                    fetch_reports(NIP,'login','payer-secret','zip-secret',date(2026,11,1),today=date(2026,11,12))
                error = caught.exception
                self.assertEqual(error.code,f'http_{status}')
                self.assertEqual(error.http_status,status)
                self.assertIn(f'HTTP {status}',str(error))
                for secret in ('payer-secret','zip-secret','proxy-token','<html>'):
                    self.assertNotIn(secret,str(error))
                self.assertEqual(str(ServiceError(error.code)),str(error))

    def test_http_500_soap_fault_is_classified_without_exposing_fault_text(self):
        data = (f'<s:Envelope xmlns:s="{SOAP_NS}"><s:Body><s:Fault>'
                '<faultcode>s:Client</faultcode><faultstring>payer-secret</faultstring>'
                '</s:Fault></s:Body></s:Envelope>').encode()
        response = MagicMock(status_code=500)
        response.iter_content.return_value = [data]
        with patch('app.integrations.ezla.service.requests.post') as post:
            post.return_value.__enter__.return_value = response
            with self.assertRaises(ServiceError) as caught:
                fetch_reports(NIP,'login','payer-secret','zip-secret',date(2026,11,1),today=date(2026,11,12))
        self.assertEqual(caught.exception.code,'soap_client')
        self.assertEqual(caught.exception.http_status,500)
        self.assertNotIn('payer-secret',str(caught.exception))
        reconstructed = ServiceError(caught.exception.code,http_status=caught.exception.http_status)
        self.assertEqual(str(reconstructed),str(caught.exception))

    def test_non_200_never_returns_success_and_error_body_is_bounded(self):
        for data in (soap(), b'x'*(1024*1024+1)):
            response = MagicMock(status_code=503)
            response.iter_content.return_value = [data]
            with patch('app.integrations.ezla.service.requests.post') as post:
                post.return_value.__enter__.return_value = response
                with self.assertRaises(ServiceError) as caught:
                    fetch_reports(NIP,'login','secret','zip',date(2026,11,1),today=date(2026,11,12))
            self.assertEqual(caught.exception.code,'http_503')

    def test_tls_failure_is_distinct_from_application_login_failure(self):
        with patch('app.integrations.ezla.service.requests.post',side_effect=requests.exceptions.SSLError('secret')):
            with self.assertRaises(ServiceError) as caught:
                fetch_reports(NIP,'login','secret','zip',date(2026,11,1),today=date(2026,11,12))
        self.assertEqual(caught.exception.code,'tls')
        self.assertNotIn('secret',str(caught.exception))
