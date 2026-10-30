import base64
import datetime as dt
import hashlib
import io
import json
import unittest
import zipfile
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.x509.oid import NameOID

from app.db import dao_invoices
from app.db.dao_app_settings import get_setting
from app.db.dao_conn import get_conn
from app.db.dao_payments import get_invoice_payment_summary, record_cash_payment
from app.invoices import ksef_client, ksef_sync
from app.invoices.ksef import ksef_url
from tests.base import DbTestCase


FA3_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<Faktura xmlns="http://crd.gov.pl/wzor/2025/06/25/13775/">
  <Podmiot1><DaneIdentyfikacyjne><NIP>1234567890</NIP><Nazwa>Biuro Testowe</Nazwa></DaneIdentyfikacyjne></Podmiot1>
  <Podmiot2><DaneIdentyfikacyjne><NIP>5252525252</NIP><Nazwa>Klient z KSeF SA</Nazwa></DaneIdentyfikacyjne>
    <Adres><AdresPol><KodKraju>PL</KodKraju><AdresL1>Testowa 1</AdresL1><AdresL2>00-001 Warszawa</AdresL2></AdresPol></Adres></Podmiot2>
  <Fa><KodWaluty>PLN</KodWaluty><P_1>2026-10-15</P_1><P_2>7/10/2026</P_2><P_6>2026-10-14</P_6><RodzajFaktury>VAT</RodzajFaktury>
    <P_13_1>200.00</P_13_1><P_14_1>46.00</P_14_1><P_15>246.00</P_15>
    <TerminPlatnosci><Termin>2026-10-29</Termin></TerminPlatnosci>
    <FaWiersz><NrWierszaFa>1</NrWierszaFa><P_7>Usługa z KSeF</P_7><P_8A>szt.</P_8A><P_8B>2</P_8B><P_9A>100</P_9A><P_11>200</P_11><P_12>23</P_12><P_11A>246</P_11A></FaWiersz>
  </Fa>
</Faktura>'''


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
    def __init__(self, data=None, content=b"", status=200, headers=None):
        self._data, self.content = data, content
        self.status_code, self.ok, self.text = status, status < 400, ""
        self.headers = headers or {}

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
            xml = FA3_XML
            if "CCCCCC" in path:
                xml = xml.replace("5252525252", "9999999999").replace("7/10/2026", "OBCA/1")
            return FakeResponse(content=xml.encode())
        raise AssertionError(path)


class RateLimitedSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


class KsefRateLimitTests(unittest.TestCase):
    def client(self, responses):
        client = ksef_client.KsefClient("1234567890", "TOKEN", "test", session=RateLimitedSession(responses))
        client._access_token = "access"
        client._access_valid_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
        client._sleep = mock.Mock()
        return client

    def test_download_retries_429_using_retry_after_without_reauthenticating(self):
        client = self.client([
            FakeResponse(status=429, headers={"Retry-After": "2"}),
            FakeResponse(content=b"<Faktura/>")
        ])
        self.assertEqual(client.download_invoice_xml("KSEF-1"), "<Faktura/>")
        self.assertEqual(client.http.calls, 2)
        self.assertTrue(any(call.args == (2.0,) for call in client._sleep.call_args_list))

    def test_retry_after_is_not_truncated_to_thirty_seconds(self):
        response = FakeResponse(status=429, headers={"Retry-After": "56"})
        self.assertEqual(ksef_client.KsefClient._retry_after_seconds(response, 0), 56.0)

    def test_metadata_query_retries_429_but_invoice_submission_does_not(self):
        client = self.client([
            FakeResponse(status=429),
            FakeResponse(data={"hasMore": False, "invoices": []}),
        ])
        self.assertEqual(list(client.query_invoices(dt.datetime(2026, 10, 1))), [])
        self.assertEqual(client.http.calls, 2)
        self.assertTrue(any(call.args == (1.0,) for call in client._sleep.call_args_list))

        client = self.client([FakeResponse(status=429)])
        with self.assertRaisesRegex(ksef_client.KsefError, "operacji zapisu"):
            client._request("POST", "/sessions/online/X/invoices", token="access", json={})
        self.assertEqual(client.http.calls, 1)

    def test_batch_export_decrypts_and_verifies_zip_package(self):
        _private, der = _make_cert()
        key, iv = b"K" * 32, b"I" * 16
        archive_buffer = io.BytesIO()
        metadata = {"invoices": [{"ksefNumber": "KSEF-1"}]}
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("KSEF-1.xml", FA3_XML.encode("utf-8"))
            archive.writestr("_metadata.json", json.dumps(metadata).encode("utf-8"))
        raw = archive_buffer.getvalue()
        padder = PKCS7(128).padder()
        padded = padder.update(raw) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        part = {
            "ordinalNumber": 1, "partName": "part.zip.aes", "method": "GET",
            "url": "https://storage.test/part", "partSize": len(raw),
            "partHash": base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii"),
            "encryptedPartSize": len(encrypted),
            "encryptedPartHash": base64.b64encode(hashlib.sha256(encrypted).digest()).decode("ascii"),
            "expirationDate": "2026-10-30T12:00:00Z",
        }

        class ExportSession:
            def request(self, method, url, **kwargs):
                if url.endswith("/security/public-key-certificates"):
                    return FakeResponse([{
                        "certificate": base64.b64encode(der).decode("ascii"),
                        "usage": ["SymmetricKeyEncryption"], "publicKeyId": "KEY-1",
                    }])
                if url.endswith("/invoices/exports") and method == "POST":
                    return FakeResponse({"referenceNumber": "EXPORT-1"}, status=201)
                if url.endswith("/invoices/exports/EXPORT-1"):
                    return FakeResponse({"status": {"code": 200}, "package": {
                        "invoiceCount": 1, "size": len(raw), "parts": [part],
                        "isTruncated": False, "compressionType": "Zip",
                    }})
                if url == "https://storage.test/part":
                    return FakeResponse(content=encrypted)
                raise AssertionError((method, url))

        client = ksef_client.KsefClient("1234567890", "TOKEN", "test", session=ExportSession())
        client._access_token = "access"
        client._access_valid_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
        with mock.patch("app.invoices.ksef_client.os.urandom", side_effect=[key, iv]):
            result = client.export_invoice_package(
                dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
                dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc),
            )
        self.assertEqual(result["files"]["KSEF-1"], FA3_XML.encode("utf-8"))
        self.assertEqual(result["metadata"], metadata["invoices"])


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
        conn.execute("UPDATE firmy SET nip = %s WHERE id = %s", ("525-252-52-52", firma_id))
        conn.commit()
        conn.close()
        dao_invoices.mark_exported([invoice_id], 1)
        record_cash_payment(invoice_id, "2026-10-20", "50.00", 1)
        session = FakeSession(der, [
            {"ksefNumber": "1234567890-20261015-AAAAAA-BB", "invoiceNumber": "7/10/2026", "grossAmount": 123.0,
             "buyer": {"identifier": {"type": "Nip", "value": "5252525252"}}},
            {"ksefNumber": "1234567890-20261015-CCCCCC-DD", "invoiceNumber": "OBCA/1", "grossAmount": 123.0,
             "buyer": {"identifier": {"type": "Nip", "value": "9999999999"}}},
        ])
        client = ksef_client.KsefClient("123-456-78-90", "TOKEN", "test", session=session)
        result = ksef_sync.sync_issued_invoices(dt.datetime(2026, 10, 1), dt.datetime(2026, 10, 30), client=client)
        self.assertEqual(result["dopasowane"], ["1234567890-20261015-AAAAAA-BB"])
        self.assertEqual(result["niedopasowane"], ["OBCA/1 — brak firmy o NIP-ie nabywcy"])
        row = dao_invoices.get_invoice_by_id(invoice_id)
        self.assertEqual(row["ksef_numer"], "1234567890-20261015-AAAAAA-BB")
        self.assertEqual(row["numer"], "7/10/2026")
        self.assertEqual(row["kwota_netto"], 200.0)
        self.assertEqual(row["kwota_brutto"], 246.0)
        self.assertEqual(row["nabywca_pelna"], "Klient z KSeF SA")
        self.assertEqual(row["ksef_nip_wysylki"], "1234567890")
        self.assertTrue(row["ksef_hash_faktury"])
        self.assertEqual(
            row["ksef_hash_faktury"],
            base64.b64encode(hashlib.sha256(FA3_XML.encode("utf-8")).digest()).decode("ascii"),
        )
        self.assertTrue(ksef_url(row["ksef_numer"], invoice=row).startswith(
            "https://qr.ksef.mf.gov.pl/invoice/1234567890/15-10-2026/"
        ))
        self.assertEqual(dao_invoices.get_invoice_positions(invoice_id)[0]["nazwa"], "Usługa z KSeF")
        self.assertEqual(get_invoice_payment_summary(invoice_id, 1)["paid"], 50)
        self.assertEqual(result["zaktualizowane"], ["1234567890-20261015-AAAAAA-BB"])
        self.assertEqual(client.download_invoice_xml("X"), FA3_XML)

    def test_sync_creates_invoice_for_company_matched_only_by_nip(self):
        _key, der = _make_cert()
        firma_id = self.make_company("Lokalna nazwa", nip="525-252-52-52")
        session = FakeSession(der, [{
            "ksefNumber": "1234567890-20261015-AAAAAA-BB", "invoiceNumber": "7/10/2026",
            "buyer": {"identifier": {"type": "Nip", "value": "5252525252"}},
        }])
        client = ksef_client.KsefClient("1234567890", "TOKEN", "test", session=session)
        progress = []
        result = ksef_sync.sync_month(
            "2026-10", client=client,
            progress=lambda done, total, text: progress.append((done, total, text)),
        )
        self.assertEqual(result["utworzone"], ["1234567890-20261015-AAAAAA-BB"])
        self.assertIn((0, 1, "Znaleziono 1 faktur sprzedaży w KSeF"), progress)
        self.assertEqual(progress[-1][:2], (1, 1))
        rows = dao_invoices.list_invoices(1, True, "2026-10", firma_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["numer"], "7/10/2026")
        self.assertEqual(rows[0]["kwota_brutto"], 246.0)

        # Ponowne uruchomienie pobiera listę, ale nie pobiera ponownie XML-a
        # dokumentu już zapisanego lokalnie. Powtórzona metadana także jest
        # deduplikowana po numerze KSeF.
        repeated = [{
            "ksefNumber": "1234567890-20261015-AAAAAA-BB", "invoiceNumber": "7/10/2026",
            "buyer": {"identifier": {"type": "Nip", "value": "5252525252"}},
        }] * 2
        session = FakeSession(der, repeated)
        client = ksef_client.KsefClient("1234567890", "TOKEN", "test", session=session)
        resumed = ksef_sync.sync_month("2026-10", client=client)
        self.assertEqual(resumed["znalezione"], 1)
        self.assertEqual(resumed["pominiete"], ["1234567890-20261015-AAAAAA-BB"])
        self.assertFalse(any(path.startswith("/invoices/ksef/") for _method, path in session.calls))

    def test_sync_does_not_import_when_buyer_nip_is_not_a_unique_company(self):
        _key, der = _make_cert()
        self.make_company("Pierwsza", nip="5252525252")
        self.make_company("Druga", nip="525-252-52-52")
        session = FakeSession(der, [{"ksefNumber": "KSEF-1", "invoiceNumber": "7/10/2026"}])
        client = ksef_client.KsefClient("1234567890", "TOKEN", "test", session=session)
        result = ksef_sync.sync_month("2026-10", client=client)
        self.assertEqual(result["dopasowane"], [])
        self.assertIn("kilka firm ma ten sam NIP", result["niedopasowane"][0])

    def test_sync_uses_one_export_package_for_a_larger_set(self):
        _key, der = _make_cert()
        firma_id = self.make_company("Lokalna nazwa", nip="5252525252")
        metadata = [{
            "ksefNumber": "1234567890-20261015-AAAAAA-BB", "invoiceNumber": "7/10/2026",
            "buyer": {"identifier": {"type": "Nip", "value": "5252525252"}},
        }]
        session = FakeSession(der, metadata)
        client = ksef_client.KsefClient("1234567890", "TOKEN", "test", session=session)
        with mock.patch.object(ksef_sync, "BATCH_EXPORT_THRESHOLD", 0), \
             mock.patch.object(client, "export_invoice_package", return_value={
                 "files": {"1234567890-20261015-AAAAAA-BB": FA3_XML.encode("utf-8")},
                 "metadata": metadata, "reference": "EXPORT-1",
             }) as export:
            result = ksef_sync.sync_month("2026-10", client=client)
        export.assert_called_once()
        self.assertEqual(result["utworzone"], ["1234567890-20261015-AAAAAA-BB"])
        self.assertFalse(any(path.startswith("/invoices/ksef/") for _method, path in session.calls))
        self.assertEqual(dao_invoices.list_invoices(1, True, "2026-10", firma_id)[0]["numer"], "7/10/2026")

    def test_imports_correction_and_links_it_to_original_invoice(self):
        firma_id = self.make_company("Klient korekty", nip="5252525252")
        original_id = dao_invoices.create_or_update_invoice(
            firma_id, "2026-09",
            [{"nazwa": "Usługa", "ilosc": 1, "cena_netto": 100, "vat": 23}], 100, 123,
        )
        conn = get_conn()
        conn.execute("UPDATE faktury SET numer = %s WHERE id = %s", ("FV/09/2026", original_id))
        conn.commit()
        conn.close()
        correction_xml = (FA3_XML
            .replace("7/10/2026", "KOR/1/10/2026")
            .replace("<RodzajFaktury>VAT</RodzajFaktury>",
                     "<RodzajFaktury>KOR</RodzajFaktury><PrzyczynaKorekty>Rabat</PrzyczynaKorekty>"
                     "<DaneFaKorygowanej><NrFaKorygowanej>FV/09/2026</NrFaKorygowanej></DaneFaKorygowanej>"))
        data = ksef_sync.parse_fa3_invoice(correction_xml)
        data["invoice_hash"] = base64.b64encode(hashlib.sha256(correction_xml.encode()).digest()).decode()
        action, correction_id = ksef_sync._upsert_downloaded_invoice(data, "1234567890-20261015-KOREKTA-AA")
        self.assertEqual(action, "utworzone")
        correction = dao_invoices.get_invoice_by_id(correction_id)
        self.assertEqual(correction["rodzaj"], "korekta")
        self.assertEqual(correction["korygowana_faktura_id"], original_id)
        self.assertEqual(correction["przyczyna_korekty"], "Rabat")

    def test_nan_ksef_number_is_cleared_instead_of_matching_another_invoice(self):
        firma_id = self.make_company("Klient NaN", nip="5252525252")
        invoice_id = dao_invoices.create_or_update_invoice(
            firma_id, "2026-10", [{"nazwa": "Usługa", "ilosc": 1, "cena_netto": 10, "vat": 23}], 10, 12.3,
        )
        dao_invoices.set_ksef_number(invoice_id, "NaN")
        self.assertIsNone(dao_invoices.get_invoice_by_id(invoice_id)["ksef_numer"])
        self.assertEqual(ksef_sync._valid_ksef_number(float("nan")), "")

    def test_imports_multiple_ksef_invoices_for_one_company_and_month(self):
        firma_id = self.make_company("Klient wielu faktur", nip="5252525252")
        for suffix in ("AA", "BB"):
            xml = FA3_XML.replace("7/10/2026", f"FV/{suffix}/10/2026")
            data = ksef_sync.parse_fa3_invoice(xml)
            data["invoice_hash"] = base64.b64encode(hashlib.sha256(xml.encode()).digest()).decode()
            action, _invoice_id = ksef_sync._upsert_downloaded_invoice(
                data, f"1234567890-20261015-{suffix}AAAA-BB",
            )
            self.assertEqual(action, "utworzone")
        rows = dao_invoices.list_invoices(1, True, "2026-10", firma_id)
        self.assertEqual({row["numer"] for row in rows}, {"FV/AA/10/2026", "FV/BB/10/2026"})

    def test_credentials_are_stored_encrypted(self):
        ksef_sync.save_ksef_credentials("123-456-78-90", "SEKRET", "test")
        self.assertFalse(ksef_sync.is_configured())  # token nie zastępuje wymaganego certyfikatu
        self.assertNotIn("SEKRET", get_setting(ksef_sync.TOKEN_KEY))
        self.assertEqual(get_setting(ksef_sync.NIP_KEY), "1234567890")


if __name__ == "__main__":
    unittest.main()
