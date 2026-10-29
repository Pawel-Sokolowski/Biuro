"""MT940 + KSeF FA(3): testy parserów, dopasowania i bezpiecznej wysyłki."""
import base64
import datetime as dt
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.x509.oid import NameOID

from app.db.dao_payments import PaymentError, parse_ing_mt940, suggested_invoice_ids
from app.invoices.ksef_client import KsefClient, KsefError
from app.invoices.ksef_submission import inspect_fa3_xml


FA3 = b'''<?xml version="1.0" encoding="UTF-8"?>
<Faktura xmlns="http://crd.gov.pl/wzor/2025/06/25/13775/">
<Naglowek><KodFormularza kodSystemowy="FA (3)" wersjaSchemy="1-0E">FA</KodFormularza></Naglowek>
<Podmiot1><DaneIdentyfikacyjne><NIP>1234567890</NIP></DaneIdentyfikacyjne></Podmiot1>
<Podmiot2><DaneIdentyfikacyjne><NIP>9998887766</NIP></DaneIdentyfikacyjne></Podmiot2>
<Fa><P_2>FV/001/09/2026</P_2></Fa></Faktura>'''


class Mt940ParserTests(unittest.TestCase):
    def test_ing_multiline_credits_debits_and_dedupe_hash(self):
        raw = (
            ":20:STATEMENT\n:25:PL44105000997603123456789123\n:28C:2026/10\n"
            ":61:2610071007C123,45NTRFABC\n"
            ":86:?00Przelew FV/001/09/2026\n?20NIP 1234567890\n"
            ":61:261008D20,00NTRFDEF\n:86:Oplata bankowa\n"
        ).encode("cp1250")
        records = parse_ing_mt940(raw)
        self.assertEqual(len(records), 2)
        self.assertEqual(str(records[0]["amount"]), "123.45")
        self.assertEqual(records[0]["date"], "2026-10-07")
        self.assertIn("FV/001/09/2026", records[0]["title"])
        self.assertEqual(str(records[1]["amount"]), "-20.00")
        self.assertEqual(records[0]["hash"], parse_ing_mt940(raw)[0]["hash"])

    def test_no_transactions_fails(self):
        with self.assertRaises(PaymentError):
            parse_ing_mt940(b":20:TEST\n:25:123")

    def test_suggest_five_digit_number_nip_and_exact_balance(self):
        transfer = {"tytul": "FV/001/09/2026 1234567890", "remaining": "123.45"}
        inv = [
            {"id": 5, "numer": "FV/001/09/2026", "nip": "1234567890", "balance": 123.45},
            {"id": 6, "numer": "FV/002/09/2026", "nip": "1234567890", "balance": 123.45},
        ]
        self.assertEqual(suggested_invoice_ids(transfer, inv), [5, 6])
        # Propozycja nie jest zatwierdzoną płatnością; rozliczenie wymaga dialogu.


class Response:
    def __init__(self, data):
        self.data = data
        self.ok = True
        self.status_code = 200
        self.text = ""

    def json(self):
        return self.data


class KsefOnlineTransportTests(unittest.TestCase):
    def _make_cert(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        who = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MF Encryption Test")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(who).issuer_name(who).public_key(key.public_key())
            .serial_number(9)
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        return key, cert

    def test_fa3_identity_preflight(self):
        self.assertEqual(inspect_fa3_xml(FA3)["number"], "FV/001/09/2026")
        with self.assertRaises(KsefError):
            inspect_fa3_xml(b"<Faktura></Faktura>")

    def test_encrypted_submission_records_references_and_can_be_decrypted(self):
        key, cert = self._make_cert()
        audit = []
        state = {"decrypted": None}

        class FakeSession:
            def request(self, method, url, headers=None, timeout=None, json=None):
                path = url.split("/v2", 1)[1]
                if path == "/security/public-key-certificates":
                    return Response([{
                        "usage": ["SymmetricKeyEncryption"],
                        "certificate": base64.b64encode(
                            cert.public_bytes(serialization.Encoding.DER)
                        ).decode("ascii"),
                    }])
                if path == "/sessions/online":
                    state["aes"] = key.decrypt(
                        base64.b64decode(json["encryption"]["encryptedSymmetricKey"]),
                        rsa_padding.OAEP(mgf=rsa_padding.MGF1(hashes.SHA256()),
                                         algorithm=hashes.SHA256(), label=None),
                    )
                    state["iv"] = base64.b64decode(json["encryption"]["initializationVector"])
                    return Response({"referenceNumber": "SESSION-123"})
                if path == "/sessions/online/SESSION-123/invoices":
                    ciphertext = base64.b64decode(json["encryptedInvoiceContent"])
                    decryptor = Cipher(algorithms.AES(state["aes"]), modes.CBC(state["iv"])).decryptor()
                    padded = decryptor.update(ciphertext) + decryptor.finalize()
                    unpadder = PKCS7(128).unpadder()
                    state["decrypted"] = unpadder.update(padded) + unpadder.finalize()
                    return Response({"referenceNumber": "INVOICE-456"})
                if path == "/sessions/online/SESSION-123/close":
                    return Response({})
                raise AssertionError((method, path))

        client = KsefClient(
            "1234567890", "", "test", session=FakeSession(),
            certificate=cert, private_key=key,
        )
        client._access = lambda: "FAKE-ACCESS"
        result = client.send_fa3_xml(
            FA3,
            on_session=lambda ref: audit.append(("session", ref)),
            on_invoice=lambda ref: audit.append(("invoice", ref)),
        )
        self.assertEqual(state["decrypted"], FA3)
        self.assertEqual(audit, [("session", "SESSION-123"), ("invoice", "INVOICE-456")])
        self.assertEqual(result["invoice_reference"], "INVOICE-456")


if __name__ == "__main__":
    unittest.main()
