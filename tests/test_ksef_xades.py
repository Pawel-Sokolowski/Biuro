import base64
import hashlib
import unittest
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from app.invoices.ksef_client import KsefClient
from app.invoices.ksef_xades import sign_auth_request
from tests.test_ksef_credentials import make_pair

try:
    from lxml import etree
except ImportError:  # lxml tylko do niezależnej weryfikacji kanonizacji
    etree = None

NS = {"ds": "http://www.w3.org/2000/09/xmldsig#", "x": "http://uri.etsi.org/01903/v1.3.2#"}


class XadesTests(unittest.TestCase):
    @unittest.skipIf(etree is None, "brak lxml")
    def test_signature_matches_exclusive_c14n(self):
        cert, key = make_pair()
        root = etree.fromstring(sign_auth_request("CH-1", "5265877635", cert, key).encode())
        sig = root.find("ds:Signature", NS)
        info = sig.find("ds:SignedInfo", NS)
        key.public_key().verify(
            base64.b64decode(sig.find("ds:SignatureValue", NS).text),
            etree.tostring(info, method="c14n", exclusive=True),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        refs = info.findall("ds:Reference", NS)
        props = sig.find(".//x:SignedProperties", NS)
        digest = base64.b64encode(hashlib.sha256(etree.tostring(props, method="c14n", exclusive=True)).digest()).decode()
        self.assertEqual(digest, refs[1].find("ds:DigestValue", NS).text)
        root.remove(sig)
        digest = base64.b64encode(hashlib.sha256(etree.tostring(root, method="c14n", exclusive=True)).digest()).decode()
        self.assertEqual(digest, refs[0].find("ds:DigestValue", NS).text)

    def test_client_uses_xades_endpoint(self):
        cert, key = make_pair()
        calls = []

        def request(method, url, **kwargs):
            calls.append((method, url.rsplit("/v2", 1)[1], kwargs))
            resp = mock.Mock(ok=True, status_code=200)
            path = calls[-1][1]
            if path == "/auth/challenge":
                resp.json.return_value = {"challenge": "C", "timestampMs": 1}
            elif path == "/auth/xades-signature":
                resp.json.return_value = {"referenceNumber": "R", "authenticationToken": {"token": "T"}}
            elif path == "/auth/R":
                resp.json.return_value = {"status": {"code": 200}}
            else:
                resp.json.return_value = {"accessToken": {"token": "A", "validUntil": "2099-01-01T00:00:00Z"}}
            return resp

        session = mock.Mock()
        session.request.side_effect = request
        client = KsefClient("5265877635", "", "test", session=session, certificate=cert, private_key=key)
        self.assertEqual(client.authenticate(), "A")
        sent = next(c for c in calls if c[1] == "/auth/xades-signature")
        self.assertIn(b"<ds:Signature", sent[2]["data"])
        self.assertEqual(sent[2]["headers"]["Content-Type"], "application/xml")
        self.assertFalse(any(c[1] == "/auth/ksef-token" for c in calls))
