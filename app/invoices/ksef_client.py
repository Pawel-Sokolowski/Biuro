"""Klient API KSeF 2.0 (uwierzytelnianie tokenem KSeF, odczyt faktur)."""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

ENVIRONMENTS = {
    "prod": "https://api.ksef.mf.gov.pl/v2",
    "demo": "https://api-demo.ksef.mf.gov.pl/v2",
    "test": "https://api-test.ksef.mf.gov.pl/v2",
}
TIMEOUT = 30
AUTH_POLL_SECONDS = 1.0
AUTH_POLL_ATTEMPTS = 30
PAGE_SIZE = 100
# Typy podmiotu: Subject1 = sprzedawca (faktury wystawione), Subject2 = nabywca (zakupowe)
SUBJECT_ISSUED = "Subject1"
SUBJECT_RECEIVED = "Subject2"


class KsefError(RuntimeError):
    pass


def encrypt_token(token: str, timestamp_ms: int, certificate_der: bytes) -> str:
    """RSA-OAEP (SHA-256/MGF1) z 'token|timestampMs' kluczem publicznym MF, w Base64."""
    public_key = x509.load_der_x509_certificate(certificate_der).public_key()
    encrypted = public_key.encrypt(
        f"{token}|{timestamp_ms}".encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(encrypted).decode("ascii")


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class KsefClient:
    def __init__(self, nip: str, ksef_token: str, environment: str = "prod", session: Optional[requests.Session] = None, certificate=None, private_key=None):
        if environment not in ENVIRONMENTS:
            raise KsefError(f"Nieznane środowisko KSeF: {environment}")
        self.nip = "".join(ch for ch in (nip or "") if ch.isdigit())
        self.ksef_token = (ksef_token or "").strip()
        self.certificate = certificate
        self.private_key = private_key
        if not self.nip or not (self.ksef_token or (certificate is not None and private_key is not None)):
            raise KsefError("Brak NIP oraz tokena lub certyfikatu KSeF.")
        self.base_url = ENVIRONMENTS[environment]
        self.http = session or requests.Session()
        self._access_token: Optional[str] = None
        self._access_valid_until: Optional[datetime] = None

    def _request(self, method: str, path: str, *, token: Optional[str] = None, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self.http.request(method, self.base_url + path, headers=headers, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise KsefError(f"Błąd połączenia z KSeF: {exc}") from exc
        if response.status_code == 429:
            raise KsefError("KSeF: przekroczono limit zapytań, spróbuj za chwilę.")
        if not response.ok:
            raise KsefError(f"KSeF {method} {path}: HTTP {response.status_code} {response.text[:300]}")
        return response

    def _token_encryption_certificate(self) -> bytes:
        certs = self._request("GET", "/security/public-key-certificates").json()
        for item in certs:
            if "KsefTokenEncryption" in (item.get("usage") or []):
                return base64.b64decode(item["certificate"])
        raise KsefError("KSeF nie zwrócił klucza do szyfrowania tokena.")

    def _init_with_certificate(self, challenge: dict) -> dict:
        from app.invoices.ksef_xades import sign_auth_request

        xml = sign_auth_request(challenge["challenge"], self.nip, self.certificate, self.private_key)
        return self._request(
            "POST",
            "/auth/xades-signature",
            data=xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
        ).json()

    def _init_with_token(self, challenge: dict) -> dict:
        encrypted = encrypt_token(self.ksef_token, int(challenge["timestampMs"]), self._token_encryption_certificate())
        return self._request(
            "POST",
            "/auth/ksef-token",
            json={
                "challenge": challenge["challenge"],
                "contextIdentifier": {"type": "Nip", "value": self.nip},
                "encryptedToken": encrypted,
            },
        ).json()

    def authenticate(self) -> str:
        challenge = self._request("POST", "/auth/challenge").json()
        if self.certificate is not None and self.private_key is not None:
            init = self._init_with_certificate(challenge)
        else:
            init = self._init_with_token(challenge)
        temp_token = init["authenticationToken"]["token"]
        reference = init["referenceNumber"]
        for _ in range(AUTH_POLL_ATTEMPTS):
            status = self._request("GET", f"/auth/{reference}", token=temp_token).json().get("status", {})
            code = status.get("code")
            if code == 200:
                break
            if code and code >= 400:
                raise KsefError(f"Uwierzytelnianie KSeF nie powiodło się: {status.get('description')}")
            time.sleep(AUTH_POLL_SECONDS)
        else:
            raise KsefError("Uwierzytelnianie KSeF trwa zbyt długo.")
        tokens = self._request("POST", "/auth/token/redeem", token=temp_token).json()
        access = tokens["accessToken"]
        self._access_token = access["token"]
        self._access_valid_until = datetime.fromisoformat(access["validUntil"].replace("Z", "+00:00"))
        return self._access_token

    def _access(self) -> str:
        soon = datetime.now(timezone.utc) + timedelta(seconds=60)
        if not self._access_token or not self._access_valid_until or self._access_valid_until <= soon:
            self.authenticate()
        return self._access_token

    def query_invoices(
        self,
        date_from: datetime,
        date_to: Optional[datetime] = None,
        subject_type: str = SUBJECT_ISSUED,
        date_type: str = "Issue",
    ) -> Iterator[Dict[str, Any]]:
        """Metadane faktur (ksefNumber, invoiceNumber, buyer, kwoty...) z podanego zakresu dat."""
        date_range: Dict[str, Any] = {"dateType": date_type, "from": _iso(date_from)}
        if date_to:
            date_range["to"] = _iso(date_to)
        body = {"subjectType": subject_type, "dateRange": date_range}
        offset = 0
        while True:
            data = self._request(
                "POST",
                "/invoices/query/metadata",
                token=self._access(),
                params={"sortOrder": "Asc", "pageOffset": offset, "pageSize": PAGE_SIZE},
                json=body,
            ).json()
            yield from data.get("invoices", [])
            if not data.get("hasMore"):
                return
            offset += 1

    def download_invoice_xml(self, ksef_number: str) -> str:
        response = self._request("GET", f"/invoices/ksef/{ksef_number}", token=self._access())
        return response.content.decode("utf-8")

    def send_fa3_xml(self, xml_bytes: bytes) -> Dict[str, Any]:
        """Wysyła gotowy XML FA(3) do KSeF 2.0 z szyfrowaniem AES-256-CBC.

        Zwrócony numer referencyjny nie oznacza jeszcze przyjęcia faktury.
        Należy odczytać status faktury i pobrać UPO.
        """
        import hashlib
        import os
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7

        if self.certificate is None or self.private_key is None:
            raise KsefError("Do wysyłki wymagane jest uwierzytelnienie certyfikatem KSeF.")
        if not xml_bytes or len(xml_bytes) > 10 * 1024 * 1024:
            raise KsefError("Plik XML jest pusty lub przekracza 10 MB.")

        response = self._request("GET", "/security/public-key-certificates").json()
        certificate = next(
            (item for item in response if "SymmetricKeyEncryption" in (item.get("usage") or [])),
            None,
        )
        if not certificate:
            raise KsefError("KSeF nie udostępnił klucza do szyfrowania faktur.")
        public_key = x509.load_der_x509_certificate(
            base64.b64decode(certificate["certificate"])
        ).public_key()
        key = os.urandom(32)
        iv = os.urandom(16)
        encrypted_key = public_key.encrypt(
            key,
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        b64 = lambda value: base64.b64encode(value).decode("ascii")
        session = self._request(
            "POST", "/sessions/online", token=self._access(),
            json={
                "formCode": {"systemCode": "FA (3)", "schemaVersion": "1-0E", "value": "FA"},
                "encryption": {
                    "encryptedSymmetricKey": b64(encrypted_key),
                    "initializationVector": b64(iv),
                    "publicKeyId": certificate.get("publicKeyId"),
                },
            },
        ).json()
        session_ref = session["referenceNumber"]
        padder = PKCS7(128).padder()
        padded = padder.update(xml_bytes) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        result = {
            "session_reference": session_ref,
            "invoice_reference": None,
            "close_error": None,
        }
        try:
            sent = self._request(
                "POST", f"/sessions/online/{session_ref}/invoices", token=self._access(),
                json={
                    "invoiceHash": b64(hashlib.sha256(xml_bytes).digest()),
                    "invoiceSize": len(xml_bytes),
                    "encryptedInvoiceHash": b64(hashlib.sha256(encrypted).digest()),
                    "encryptedInvoiceSize": len(encrypted),
                    "encryptedInvoiceContent": b64(encrypted),
                    "offlineMode": False,
                },
            ).json()
            result["invoice_reference"] = sent["referenceNumber"]
        finally:
            try:
                self._request(
                    "POST", f"/sessions/online/{session_ref}/close",
                    token=self._access(),
                )
            except KsefError as exc:
                result["close_error"] = str(exc)
                logger.warning("KSeF: nie zamknięto sesji %s", session_ref)
        if result["invoice_reference"] is None:
            raise KsefError(
                f"Wysłanie XML nie zostało potwierdzone. Sprawdź sesję KSeF {session_ref} "
                "przed ponowną próbą, aby nie zdublować faktury."
            )
        return result

    def invoice_submission_status(self, session_reference: str, invoice_reference: str) -> dict:
        return self._request(
            "GET", f"/sessions/{session_reference}/invoices/{invoice_reference}",
            token=self._access(),
        ).json()

    def list_invoices(self, date_from: datetime, date_to: Optional[datetime] = None, **kwargs) -> List[Dict[str, Any]]:
        return list(self.query_invoices(date_from, date_to, **kwargs))
