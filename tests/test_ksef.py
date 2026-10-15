import base64
import datetime as dt
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from app.db import dao_invoices
from app.db.dao_app_settings import get_setting
from app.db.dao_conn import get_conn
from app.invoices import ksef_client, ksef_sync
from tests.base import DbTestCase


def _make_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ksef-test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.DER)


class FakeResponse:
    def __init__(self, data=None, content=b""):
        self._data, self.content, self.ok, self.status_code, self.text = data, content, True, 200, ""

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, certificate_der, invoices):
        self.cert = certificate_der
        self.invoices = invoices
        self.calls = []
        self.encrypted = None

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        path = url.split("/v2", 1)[1]
        self.calls.append((method, path))
        if path == "/auth/challenge":
            return FakeResponse({"challenge": "c" * 36, "timestampMs": 1700000000000})
        if path == "/security/public-key-certificates":
            return FakeResponse([{"certificate": base64.b64encode(self.cert).decode(), "usage": ["KsefTokenEncryption"]}])
        if path == "/auth/ksef-token":
            self.encrypted = json["encryptedToken"]
            assert json["contextIdentifier"] == {"type": "Nip", "value": "1234567890"}
            return FakeResponse({"referenceNumber": "R1", "authenticationToken": {"token": "tmp"}})
        if path == "/auth/R1":
            return FakeResponse({"status": {"code": 200}})
        if path == "/auth/token/redeem":
            valid = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15)).isoformat()
            return FakeResponse({"accessToken": {"token": "acc", "validUntil": valid}, "refreshToken": {"token": "r"}})
        if path == "/invoices/query/metadata":
            assert headers["Authorization"] == "Bearer acc"
            return FakeResponse({"hasMore": False, "invoices": self.invoices})
        if path.startswith("/invoices/ksef/"):
            return FakeResponse(content="<Faktura/>".encode())
        raise AssertionError(path)


class KsefTests(DbTestCase):
    def test_token_encryption_roundtrip(self):
        key, der = _make_cert()
        encrypted = ksef_client.encrypt_token("TOKEN", 123, der)
        plain = key.decrypt(
            base64.b64decode(encrypted),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        self.assertEqual(plain, b"TOKEN|123")

    def test_authenticate_and_sync(self):
        key, der = _make_cert()
        firma_id = self.make_company("Klient SA")
        invoice_id = dao_invoices.create_or_update_invoice(
            firma_id, "2026-10", [{"nazwa": "Usługa", "ilosc": 1, "cena_netto": 100, "netto": 100, "vat": 23, "brutto": 123}], 100, 123
        )
        conn = get_conn()
        conn.execute("UPDATE firmy SET nip = ? WHERE id = ?", ("525-252-52-52", firma_id))
        conn.commit()
        conn.close()
        dao_invoices.mark_exported([invoice_id], 1)
        session = FakeSession(der, [
            {"ksefNumber": "1234567890-20261015-AAAAAA-BB", "invoiceNumber": "7/10/2026", "grossAmount": 123.0,
             "buyer": {"identifier": {"type": "Nip", "value": "5252525252"}}},
            {"ksefNumber": "1234567890-20261015-CCCCCC-DD", "invoiceNumber": "OBCA/1", "grossAmount": 123.0,
             "buyer": {"identifier": {"type": "Nip", "value": "9999999999"}}},
        ])
        client = ksef_client.KsefClient("123-456-78-90", "TOKEN", "test", session=session)
        result = ksef_sync.sync_issued_invoices(dt.datetime(2026, 10, 1), dt.datetime(2026, 10, 30), client=client)
        self.assertEqual(result["dopasowane"], ["1234567890-20261015-AAAAAA-BB"])
        self.assertEqual(result["niedopasowane"], ["OBCA/1"])
        row = dao_invoices.get_invoice_by_id(invoice_id)
        self.assertEqual(row["ksef_numer"], "1234567890-20261015-AAAAAA-BB")
        self.assertEqual(row["numer"], "7/10/2026")
        self.assertEqual(client.download_invoice_xml("X"), "<Faktura/>")

    def test_credentials_are_stored_encrypted(self):
        ksef_sync.save_ksef_credentials("123-456-78-90", "SEKRET", "test")
        self.assertTrue(ksef_sync.is_configured())
        self.assertNotIn("SEKRET", get_setting(ksef_sync.TOKEN_KEY))
        self.assertEqual(get_setting(ksef_sync.NIP_KEY), "1234567890")


if __name__ == "__main__":
    unittest.main()
