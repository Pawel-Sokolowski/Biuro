"""Podpis XAdES-BES (enveloped) dokumentu AuthTokenRequest dla uwierzytelniania certyfikatem w KSeF.

Drzewo XML jest kanonizowane przez lxml (Exclusive C14N) przed obliczeniem skrótów i podpisu.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Optional
from lxml import etree as E

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


def _node(parent, namespace, name, text=None, **attrs):
    node = E.SubElement(parent, E.QName(namespace, name), **attrs)
    if text is not None:
        node.text = str(text)
    return node


def _auth_tree(challenge, nip):
    root = E.Element(E.QName(NS_AUTH, "AuthTokenRequest"), nsmap={None: NS_AUTH})
    _node(root, NS_AUTH, "Challenge", challenge)
    context = _node(root, NS_AUTH, "ContextIdentifier")
    _node(context, NS_AUTH, "Nip", nip)
    _node(root, NS_AUTH, "SubjectIdentifierType", "certificateSubject")
    return root


def _canonical(node):
    return E.tostring(node, method="c14n", exclusive=True, with_comments=False)


def build_auth_request(challenge: str, nip: str) -> str:
    return E.tostring(_auth_tree(challenge, nip), encoding="unicode")


def sign_auth_request(challenge: str, nip: str, certificate: x509.Certificate,
                      private_key, signing_time: Optional[datetime] = None) -> str:
    root = _auth_tree(challenge, nip)
    doc_digest = _b64(_sha256(_canonical(root)))
    cert_der = certificate.public_bytes(serialization.Encoding.DER)
    when = (signing_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # Keep an explicit ds prefix: reference/profile interoperability is unchanged.
    signature = E.SubElement(root, E.QName(NS_DS, "Signature"), nsmap={"ds": NS_DS}, Id="Signature")
    info = _node(signature, NS_DS, "SignedInfo")
    _node(info, NS_DS, "CanonicalizationMethod", Algorithm=ALG_C14N)
    algorithm = ALG_RSA_SHA256 if isinstance(private_key, rsa.RSAPrivateKey) else ALG_ECDSA_SHA256
    _node(info, NS_DS, "SignatureMethod", Algorithm=algorithm)
    doc_ref = _node(info, NS_DS, "Reference", URI="")
    transforms = _node(doc_ref, NS_DS, "Transforms")
    for transform in (ALG_ENVELOPED, ALG_C14N):
        _node(transforms, NS_DS, "Transform", Algorithm=transform)
    _node(doc_ref, NS_DS, "DigestMethod", Algorithm=ALG_SHA256)
    _node(doc_ref, NS_DS, "DigestValue", doc_digest)
    props_ref = _node(info, NS_DS, "Reference", Type="http://uri.etsi.org/01903#SignedProperties", URI="#SignedProperties")
    transforms = _node(props_ref, NS_DS, "Transforms")
    _node(transforms, NS_DS, "Transform", Algorithm=ALG_C14N)
    _node(props_ref, NS_DS, "DigestMethod", Algorithm=ALG_SHA256)
    props_digest = _node(props_ref, NS_DS, "DigestValue")
    sig_value = _node(signature, NS_DS, "SignatureValue")
    cert_data = _node(_node(signature, NS_DS, "KeyInfo"), NS_DS, "X509Data")
    _node(cert_data, NS_DS, "X509Certificate", _b64(cert_der))
    obj = _node(signature, NS_DS, "Object")
    qual = E.SubElement(obj, E.QName(NS_XADES, "QualifyingProperties"), nsmap={"xades": NS_XADES}, Target="#Signature")
    props = _node(qual, NS_XADES, "SignedProperties", Id="SignedProperties")
    signed = _node(props, NS_XADES, "SignedSignatureProperties")
    _node(signed, NS_XADES, "SigningTime", when.strftime("%Y-%m-%dT%H:%M:%SZ"))
    cert = _node(_node(signed, NS_XADES, "SigningCertificate"), NS_XADES, "Cert")
    digest = _node(cert, NS_XADES, "CertDigest")
    _node(digest, NS_DS, "DigestMethod", Algorithm=ALG_SHA256)
    _node(digest, NS_DS, "DigestValue", _b64(_sha256(cert_der)))
    issuer = _node(cert, NS_XADES, "IssuerSerial")
    _node(issuer, NS_DS, "X509IssuerName", certificate.issuer.rfc4514_string())
    _node(issuer, NS_DS, "X509SerialNumber", certificate.serial_number)
    props_digest.text = _b64(_sha256(_canonical(props)))
    _, value = _sign(private_key, _canonical(info))
    sig_value.text = _b64(value)
    return E.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
