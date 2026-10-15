"""Podpis XAdES-BES (enveloped) dokumentu AuthTokenRequest dla uwierzytelniania certyfikatem w KSeF.

Dokument jest budowany ręcznie w postaci kanonicznej (Exclusive C14N), dzięki czemu
nie potrzeba dodatkowych bibliotek XML.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Optional
from xml.sax.saxutils import escape

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

NS_AUTH = "http://ksef.mf.gov.pl/auth/token/2.0"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"
NS_XADES = "http://uri.etsi.org/01903/v1.3.2#"
ALG_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ALG_ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
ALG_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
ALG_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
ALG_ECDSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _sha256(data: bytes) -> bytes:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(data)
    return digest.finalize()


def _sign(private_key, data: bytes) -> tuple[str, bytes]:
    if isinstance(private_key, rsa.RSAPrivateKey):
        return ALG_RSA_SHA256, private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        r, s = utils.decode_dss_signature(private_key.sign(data, ec.ECDSA(hashes.SHA256())))
        size = (private_key.curve.key_size + 7) // 8
        return ALG_ECDSA_SHA256, r.to_bytes(size, "big") + s.to_bytes(size, "big")
    raise ValueError("Nieobsługiwany typ klucza (wymagany RSA lub EC).")


def build_auth_request(challenge: str, nip: str) -> str:
    return (
        f'<AuthTokenRequest xmlns="{NS_AUTH}"><Challenge>{escape(challenge)}</Challenge>'
        f"<ContextIdentifier><Nip>{escape(nip)}</Nip></ContextIdentifier>"
        "<SubjectIdentifierType>certificateSubject</SubjectIdentifierType>"
    )


def sign_auth_request(
    challenge: str,
    nip: str,
    certificate: x509.Certificate,
    private_key,
    signing_time: Optional[datetime] = None,
) -> str:
    body = build_auth_request(challenge, nip)
    document_c14n = body + "</AuthTokenRequest>"
    doc_digest = _b64(_sha256(document_c14n.encode("utf-8")))

    cert_der = certificate.public_bytes(serialization.Encoding.DER)
    when = (signing_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issuer = escape(certificate.issuer.rfc4514_string())
    signed_props = (
        f'<xades:SignedProperties xmlns:xades="{NS_XADES}" Id="SignedProperties">'
        "<xades:SignedSignatureProperties>"
        f"<xades:SigningTime>{when:%Y-%m-%dT%H:%M:%SZ}</xades:SigningTime>"
        "<xades:SigningCertificate><xades:Cert><xades:CertDigest>"
        f'<ds:DigestMethod xmlns:ds="{NS_DS}" Algorithm="{ALG_SHA256}"></ds:DigestMethod>'
        f'<ds:DigestValue xmlns:ds="{NS_DS}">{_b64(_sha256(cert_der))}</ds:DigestValue>'
        "</xades:CertDigest><xades:IssuerSerial>"
        f'<ds:X509IssuerName xmlns:ds="{NS_DS}">{issuer}</ds:X509IssuerName>'
        f'<ds:X509SerialNumber xmlns:ds="{NS_DS}">{certificate.serial_number}</ds:X509SerialNumber>'
        "</xades:IssuerSerial></xades:Cert></xades:SigningCertificate>"
        "</xades:SignedSignatureProperties></xades:SignedProperties>"
    )
    props_digest = _b64(_sha256(signed_props.encode("utf-8")))

    sig_alg = ALG_RSA_SHA256 if isinstance(private_key, rsa.RSAPrivateKey) else ALG_ECDSA_SHA256
    signed_info = (
        f'<ds:SignedInfo xmlns:ds="{NS_DS}">'
        f'<ds:CanonicalizationMethod Algorithm="{ALG_C14N}"></ds:CanonicalizationMethod>'
        f'<ds:SignatureMethod Algorithm="{sig_alg}"></ds:SignatureMethod>'
        '<ds:Reference URI="">'
        f'<ds:Transforms><ds:Transform Algorithm="{ALG_ENVELOPED}"></ds:Transform>'
        f'<ds:Transform Algorithm="{ALG_C14N}"></ds:Transform></ds:Transforms>'
        f'<ds:DigestMethod Algorithm="{ALG_SHA256}"></ds:DigestMethod>'
        f"<ds:DigestValue>{doc_digest}</ds:DigestValue></ds:Reference>"
        '<ds:Reference Type="http://uri.etsi.org/01903#SignedProperties" URI="#SignedProperties">'
        f'<ds:Transforms><ds:Transform Algorithm="{ALG_C14N}"></ds:Transform></ds:Transforms>'
        f'<ds:DigestMethod Algorithm="{ALG_SHA256}"></ds:DigestMethod>'
        f"<ds:DigestValue>{props_digest}</ds:DigestValue></ds:Reference>"
        "</ds:SignedInfo>"
    )
    _, signature = _sign(private_key, signed_info.encode("utf-8"))

    signature_xml = (
        f'<ds:Signature xmlns:ds="{NS_DS}" Id="Signature">'
        f"{signed_info}"
        f"<ds:SignatureValue>{_b64(signature)}</ds:SignatureValue>"
        f"<ds:KeyInfo><ds:X509Data><ds:X509Certificate>{_b64(cert_der)}</ds:X509Certificate></ds:X509Data></ds:KeyInfo>"
        f'<ds:Object><xades:QualifyingProperties xmlns:xades="{NS_XADES}" Target="#Signature">'
        f"{signed_props}</xades:QualifyingProperties></ds:Object></ds:Signature>"
    )
    return '<?xml version="1.0" encoding="utf-8"?>' + body + signature_xml + "</AuthTokenRequest>"
