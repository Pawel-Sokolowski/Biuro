"""Import i przechowywanie certyfikatu oraz klucza prywatnego KSeF (zaszyfrowane w ustawieniach)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from app.db.dao_app_settings import get_setting, set_setting
from app.utils.crypto import decrypt_password, encrypt_password

CERT_KEY = "ksef_cert_pem_enc"
PRIVATE_KEY_KEY = "ksef_key_pem_enc"
KEY_PASSWORD_KEY = "ksef_key_pass_enc"


class KsefCertError(ValueError):
    pass


def _load_certificate(data: bytes) -> x509.Certificate:
    try:
        if b"-----BEGIN" in data:
            return x509.load_pem_x509_certificate(data)
        return x509.load_der_x509_certificate(data)
    except ValueError as exc:
        raise KsefCertError("Nieprawidłowy plik certyfikatu (PEM/CRT/CER).") from exc


def _load_private_key(data: bytes, password: Optional[str]):
    pwd = password.encode() if password else None
    try:
        if b"-----BEGIN" in data:
            return serialization.load_pem_private_key(data, pwd)
        return serialization.load_der_private_key(data, pwd)
    except (ValueError, TypeError) as exc:
        raise KsefCertError("Nie można odczytać klucza prywatnego (zły format lub hasło).") from exc


def _store(cert: x509.Certificate, key, password: Optional[str]) -> None:
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    # klucz zapisujemy zawsze bez hasła pliku; ochronę zapewnia szyfrowanie w bazie
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    set_setting(CERT_KEY, encrypt_password(cert_pem.decode()))
    set_setting(PRIVATE_KEY_KEY, encrypt_password(key_pem.decode()))
    set_setting(KEY_PASSWORD_KEY, encrypt_password(password) if password else "")


def import_pkcs12(data: bytes, password: Optional[str]) -> None:
    try:
        key, cert, _ = pkcs12.load_key_and_certificates(data, password.encode() if password else None)
    except ValueError as exc:
        raise KsefCertError("Nie można otworzyć pliku PFX/P12 (zły format lub hasło).") from exc
    if cert is None or key is None:
        raise KsefCertError("Plik PFX/P12 nie zawiera certyfikatu i klucza prywatnego.")
    _store(cert, key, password)


def import_cert_and_key(cert_data: bytes, key_data: bytes, key_password: Optional[str]) -> None:
    cert = _load_certificate(cert_data)
    key = _load_private_key(key_data, key_password)
    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        raise KsefCertError("Klucz prywatny nie pasuje do certyfikatu.")
    _store(cert, key, key_password)


def clear_certificate() -> None:
    for k in (CERT_KEY, PRIVATE_KEY_KEY, KEY_PASSWORD_KEY):
        set_setting(k, "")


def get_certificate_info() -> Optional[Dict[str, Any]]:
    enc = get_setting(CERT_KEY)
    if not enc:
        return None
    cert = x509.load_pem_x509_certificate(decrypt_password(enc).encode())
    return {
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "valid_to": cert.not_valid_after_utc,
        "has_key": bool(get_setting(PRIVATE_KEY_KEY)),
    }


def load_credentials() -> Optional[Dict[str, Any]]:
    """Zwraca (certyfikat, klucz) do podpisu XAdES lub None."""
    cert_enc, key_enc = get_setting(CERT_KEY), get_setting(PRIVATE_KEY_KEY)
    if not cert_enc or not key_enc:
        return None
    return {
        "certificate": x509.load_pem_x509_certificate(decrypt_password(cert_enc).encode()),
        "private_key": serialization.load_pem_private_key(decrypt_password(key_enc).encode(), None),
    }
