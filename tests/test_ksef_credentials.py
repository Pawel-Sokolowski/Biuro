import datetime
import os
from unittest import mock

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from app.invoices import ksef_credentials as creds
from tests.base import DbTestCase


def make_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test KSeF")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(1)
        .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return cert, key


class KsefCredentialsTests(DbTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"BIO_ENCRYPTION_KEY": Fernet.generate_key().decode()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_import_pair_and_info(self):
        cert, key = make_pair()
        creds.import_cert_and_key(
            cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            None,
        )
        info = creds.get_certificate_info()
        self.assertIn("Test KSeF", info["subject"])
        self.assertTrue(info["has_key"])
        self.assertIsNotNone(creds.load_credentials())
        creds.clear_certificate()
        self.assertIsNone(creds.get_certificate_info())

    def test_mismatched_key_rejected(self):
        cert, _ = make_pair()
        _, other = make_pair()
        with self.assertRaises(creds.KsefCertError):
            creds.import_cert_and_key(
                cert.public_bytes(serialization.Encoding.PEM),
                other.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
                None,
            )

    def test_import_pkcs12_and_wrong_password(self):
        cert, key = make_pair()
        data = pkcs12.serialize_key_and_certificates(
            b"x", key, cert, None, serialization.BestAvailableEncryption(b"haslo")
        )
        with self.assertRaises(creds.KsefCertError):
            creds.import_pkcs12(data, "zle")
        creds.import_pkcs12(data, "haslo")
        self.assertTrue(creds.get_certificate_info()["has_key"])
