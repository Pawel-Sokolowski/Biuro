"""The plain-HTTP trial connection stays off unless it is asked for."""
import unittest
from unittest import mock

from app.remote.client import OfficeClient, normalize_server


class PlainHttpSwitchTests(unittest.TestCase):
    def test_https_remains_the_default_for_a_bare_address(self):
        self.assertEqual(normalize_server('serwer.biura:8443'), 'https://serwer.biura:8443')

    def test_http_address_is_rejected_without_the_switch(self):
        with mock.patch.dict('os.environ', {'BIO_ALLOW_PLAIN_HTTP': ''}):
            with self.assertRaises(ValueError):
                OfficeClient('http://127.0.0.1:8443')

    def test_http_address_is_accepted_with_the_switch(self):
        connection = OfficeClient('http://127.0.0.1:8443', plain_http=True)
        try:
            self.assertTrue(connection.plain_http)
            self.assertTrue(connection.server.startswith('http://'))
            # No certificate is verified on a plain connection.
            self.assertIsNotNone(connection._new_session())
        finally:
            connection.close()

    def test_server_requires_a_certificate_unless_plain_http_is_allowed(self):
        from app.remote.http_server import create_server
        with mock.patch('app.remote.http_server.OfficeService') as service:
            service.return_value = mock.Mock()
            with self.assertRaises(ValueError):
                create_server('127.0.0.1', 0, '', '', '.')
            server = create_server('127.0.0.1', 0, '', '', '.', allow_plain_http=True)
            try:
                self.assertIsNone(server.tls_context)
            finally:
                server.server_close()


if __name__ == '__main__':
    unittest.main()


class LanOnlyBindTests(unittest.TestCase):
    """Plain HTTP must never listen on a public or wildcard address."""

    def test_private_addresses_are_allowed(self):
        from app.remote.http_server import _private_bind
        for host in ('127.0.0.1', 'localhost', '192.168.1.116', '10.4.0.9', '172.16.31.7'):
            with self.subTest(host=host):
                self.assertTrue(_private_bind(host))

    def test_wildcard_and_public_addresses_are_refused(self):
        from app.remote.http_server import _private_bind
        for host in ('', '0.0.0.0', '::', '*', '8.8.8.8', 'example.com'):
            with self.subTest(host=host):
                self.assertFalse(_private_bind(host))

    def test_server_refuses_plain_http_on_a_wildcard_bind(self):
        import tempfile
        from app.remote.http_server import create_server
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'sieci lokalnej'):
                create_server('0.0.0.0', 0, '', '', directory, allow_plain_http=True)
