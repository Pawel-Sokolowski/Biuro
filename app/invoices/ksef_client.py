"""Klient API KSeF 2.0 (uwierzytelnianie tokenem KSeF, odczyt faktur)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

logger = logging.getLogger(__name__)

ENVIRONMENTS = {
    "prod": "https://api.ksef.mf.gov.pl/v2",
    "demo": "https://api-demo.ksef.mf.gov.pl/v2",
    "test": "https://api-test.ksef.mf.gov.pl/v2",
}
TIMEOUT = 30
MIN_REQUEST_INTERVAL = 0.25
INVOICE_DOWNLOAD_INTERVAL = 3.1
RATE_LIMIT_ATTEMPTS = 6
RATE_LIMIT_MAX_WAIT = 30.0
RATE_LIMIT_MAX_RETRY_AFTER = 300.0
AUTH_POLL_SECONDS = 1.0
AUTH_POLL_ATTEMPTS = 30
PAGE_SIZE = 100
EXPORT_POLL_SECONDS = 2.0
EXPORT_POLL_ATTEMPTS = 150
MAX_EXPORT_BYTES = 256 * 1024 * 1024
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
        self._last_read_request_at: Optional[float] = None
        self._sleep = time.sleep
        self._clock = time.monotonic

    @staticmethod
    def _can_retry_rate_limit(method: str, path: str) -> bool:
        # Odczyty oraz zapytanie o metadane są idempotentne. Wysyłki faktury
        # nie ponawiamy automatycznie, bo po utracie odpowiedzi mogłaby powstać
        # druga faktura.
        return method.upper() == "GET" or path in ("/invoices/query/metadata", "/invoices/exports")

    def _throttle_read(self, method: str, path: str) -> None:
        if not self._can_retry_rate_limit(method, path):
            return
        now = self._clock()
        interval = INVOICE_DOWNLOAD_INTERVAL if path.startswith("/invoices/ksef/") else MIN_REQUEST_INTERVAL
        if self._last_read_request_at is not None:
            wait = interval - (now - self._last_read_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_read_request_at = self._clock()

    @staticmethod
    def _retry_after_seconds(response: requests.Response, attempt: int) -> float:
        value = str(getattr(response, "headers", {}).get("Retry-After", "")).strip()
        try:
            # Retry-After z KSeF bywa dłuższy niż 30 s. Jego obcięcie
            # powodowało przedwczesne ponowienie (np. 56 s -> 30 s, potem 26 s).
            return min(RATE_LIMIT_MAX_RETRY_AFTER, max(0.0, float(value)))
        except ValueError:
            # Jeżeli KSeF nie poda liczby sekund (lub nagłówka wcale),
            # stosujemy ograniczony backoff: 1, 2, 4, 8, 16, 30 s.
            return min(RATE_LIMIT_MAX_WAIT, float(2 ** attempt))

    def _request(self, method: str, path: str, *, token: Optional[str] = None, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = None
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            self._throttle_read(method, path)
            try:
                response = self.http.request(method, self.base_url + path, headers=headers, timeout=TIMEOUT, **kwargs)
            except requests.RequestException as exc:
                raise KsefError(f"Błąd połączenia z KSeF: {exc}") from exc
            if response.status_code != 429:
                break
            if not self._can_retry_rate_limit(method, path):
                raise KsefError("KSeF ograniczył zapytania podczas operacji zapisu. Sprawdź stan przed ponowieniem.")
            if attempt + 1 >= RATE_LIMIT_ATTEMPTS:
                raise KsefError("KSeF nadal ogranicza zapytania po automatycznych ponowieniach. Spróbuj ponownie później.")
            wait = self._retry_after_seconds(response, attempt)
            logger.warning("KSeF HTTP 429 dla %s %s; ponowienie za %.1f s", method, path, wait)
            self._sleep(wait)
        assert response is not None
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

    def download_invoice_bytes(self, ksef_number: str) -> bytes:
        """Zwraca oryginalne bajty FA(3); są wymagane do skrótu linku weryfikacyjnego."""
        response = self._request("GET", f"/invoices/ksef/{ksef_number}", token=self._access())
        return response.content

    def download_invoice_xml(self, ksef_number: str) -> str:
        return self.download_invoice_bytes(ksef_number).decode("utf-8-sig")

    def export_invoice_package(
        self, date_from: datetime, date_to: datetime,
        subject_type: str = SUBJECT_ISSUED, progress: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Pobiera wiele faktur jednym eksportem paczkowym zalecanym przez KSeF."""
        certificates = self._request("GET", "/security/public-key-certificates").json()
        selected = next((item for item in certificates if "SymmetricKeyEncryption" in (item.get("usage") or [])), None)
        if not selected:
            raise KsefError("KSeF nie udostępnił klucza szyfrowania eksportu faktur.")
        public_key = x509.load_der_x509_certificate(base64.b64decode(selected["certificate"])).public_key()
        key, iv = os.urandom(32), os.urandom(16)
        encrypted_key = public_key.encrypt(
            key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        body = {
            "encryption": {
                "encryptedSymmetricKey": base64.b64encode(encrypted_key).decode("ascii"),
                "initializationVector": base64.b64encode(iv).decode("ascii"),
                "publicKeyId": selected.get("publicKeyId"),
            },
            "onlyMetadata": False,
            "compressionType": "Zip",
            "filters": {
                "subjectType": subject_type,
                "dateRange": {"dateType": "Issue", "from": _iso(date_from), "to": _iso(date_to)},
            },
        }
        if not body["encryption"]["publicKeyId"]:
            body["encryption"].pop("publicKeyId")
        started = self._request("POST", "/invoices/exports", token=self._access(), json=body).json()
        reference = started.get("referenceNumber")
        if not reference:
            raise KsefError("KSeF nie zwrócił numeru referencyjnego eksportu faktur.")
        if progress:
            progress(f"KSeF przygotowuje paczkę faktur ({reference})")
        package = None
        for _ in range(EXPORT_POLL_ATTEMPTS):
            status = self._request("GET", f"/invoices/exports/{reference}", token=self._access()).json()
            code = (status.get("status") or {}).get("code")
            if code == 200:
                package = status.get("package")
                break
            if code and code >= 400:
                description = (status.get("status") or {}).get("description") or "nieznany błąd"
                raise KsefError(f"Eksport faktur KSeF nie powiódł się: {description}")
            self._sleep(EXPORT_POLL_SECONDS)
        if not package:
            raise KsefError("KSeF zbyt długo przygotowuje paczkę faktur. Spróbuj wznowić synchronizację później.")
        if package.get("isTruncated"):
            raise KsefError("Paczka KSeF została obcięta. Zawęź zakres synchronizacji.")
        if int(package.get("size") or 0) > MAX_EXPORT_BYTES:
            raise KsefError("Paczka faktur KSeF przekracza bezpieczny limit 256 MB.")
        decrypted_parts = []
        for part in sorted(package.get("parts") or [], key=lambda item: item.get("ordinalNumber", 0)):
            if progress:
                progress(f"Pobieranie paczki KSeF: część {part.get('ordinalNumber', '?')}")
            try:
                response = self.http.request(part.get("method") or "GET", part["url"], timeout=TIMEOUT)
            except requests.RequestException as exc:
                raise KsefError(f"Błąd pobierania paczki KSeF: {exc}") from exc
            if not response.ok:
                raise KsefError(f"Nie udało się pobrać paczki KSeF: HTTP {response.status_code}")
            encrypted = response.content
            if len(encrypted) != int(part["encryptedPartSize"]) or base64.b64encode(hashlib.sha256(encrypted).digest()).decode("ascii") != part["encryptedPartHash"]:
                raise KsefError("Zaszyfrowana paczka KSeF ma nieprawidłowy rozmiar lub skrót.")
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            padded = decryptor.update(encrypted) + decryptor.finalize()
            try:
                unpadder = PKCS7(128).unpadder()
                raw = unpadder.update(padded) + unpadder.finalize()
            except ValueError as exc:
                raise KsefError("Nie udało się odszyfrować paczki KSeF.") from exc
            if len(raw) != int(part["partSize"]) or base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii") != part["partHash"]:
                raise KsefError("Odszyfrowana paczka KSeF ma nieprawidłowy rozmiar lub skrót.")
            decrypted_parts.append(raw)
        archive = b"".join(decrypted_parts)
        files: Dict[str, bytes] = {}
        metadata: List[Dict[str, Any]] = []
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as package_zip:
                for info in package_zip.infolist():
                    if info.is_dir() or info.file_size > 10 * 1024 * 1024:
                        continue
                    content = package_zip.read(info)
                    name = info.filename.rsplit("/", 1)[-1]
                    if name.lower() == "_metadata.json":
                        metadata.extend((json.loads(content.decode("utf-8-sig")) or {}).get("invoices", []))
                    elif name.lower().endswith(".xml"):
                        files[name[:-4]] = content
        except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise KsefError("Paczka eksportu KSeF jest uszkodzona.") from exc
        return {"reference": reference, "metadata": metadata, "files": files}

    def send_fa3_xml(
        self, xml_bytes: bytes, *,
        on_session: Optional[Callable[[str], None]] = None,
        on_invoice: Optional[Callable[[str], None]] = None,
        on_upload: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
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
        certificate = None
        now = datetime.now(timezone.utc)
        for item in response:
            if "SymmetricKeyEncryption" not in (item.get("usage") or []):
                continue
            parsed = x509.load_der_x509_certificate(base64.b64decode(item["certificate"]))
            if parsed.not_valid_before_utc <= now <= parsed.not_valid_after_utc:
                certificate = item
                break
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
            if on_session:
                on_session(session_ref)
            # Odświeżenie uwierzytelnienia może się nie udać przed wysłaniem
            # dokumentu; callback oznacza wyłącznie rozpoczęcie jego HTTP POST.
            upload_access = self._access()
            if on_upload:
                on_upload()
            sent = self._request(
                "POST", f"/sessions/online/{session_ref}/invoices", token=upload_access,
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
            if on_invoice:
                on_invoice(result["invoice_reference"])
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

    def find_submission_by_hash(self, session_reference: str, invoice_hash: str) -> Optional[dict]:
        """Odzyskuje referencję po utracie odpowiedzi HTTP 202, bez ponownej wysyłki."""
        continuation = None
        while True:
            response = self._request(
                "GET", f"/sessions/{session_reference}/invoices", token=self._access(),
                params={"pageSize": 100},
                headers={"x-continuation-token": continuation} if continuation else {},
            ).json()
            for item in response.get("invoices", []):
                if item.get("invoiceHash") == invoice_hash:
                    return item
            continuation = response.get("continuationToken")
            if not continuation:
                return None

    def list_invoices(self, date_from: datetime, date_to: Optional[datetime] = None, **kwargs) -> List[Dict[str, Any]]:
        return list(self.query_invoices(date_from, date_to, **kwargs))
