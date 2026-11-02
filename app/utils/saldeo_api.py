"""Klient tylko do odczytu dla SaldeoSMART API (company.list, document.list).

Podpis żądania: MD5( url-encode(posortowane "klucz=wartość" sklejone bez separatorów) + token ).
Token API nie jest nigdy logowany ani dołączany do URL.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

from app.db.dao_app_settings import get_setting

logger = logging.getLogger(__name__)

DEFAULT_SERVER = "https://saldeo.brainshare.pl"
API_VERSION = "1.0"
DOCUMENT_LIST_VERSION = "2.12"
DOCUMENT_BY_MONTH_VERSION = "3.0"
USERNAME_KEY = "saldeo_api_username"
TOKEN_KEY = "saldeo_api_token"
SERVER_KEY = "saldeo_api_server"


class SaldeoApiError(RuntimeError):
    pass


def sign_request(params: Dict[str, str], api_token: str) -> str:
    base = "".join(f"{key}={params[key]}" for key in sorted(params))
    encoded = quote_plus(base, safe="*").replace("~", "%7E")
    return hashlib.md5((encoded + api_token).encode("utf-8")).hexdigest()


def _text(node: Optional[ElementTree.Element]) -> str:
    return (node.text or "").strip() if node is not None else ""


def element_to_dict(node: ElementTree.Element) -> Dict[str, Any]:
    """Płaski słownik z potomków elementu; powtarzające się znaczniki trafiają na listę."""
    result: Dict[str, Any] = {}
    for child in node:
        value: Any = element_to_dict(child) if len(child) else _text(child)
        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    return result


def parse_response(xml: bytes | str) -> ElementTree.Element:
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise SaldeoApiError("Odpowiedź Saldeo zawiera niedozwoloną deklarację DTD.")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise SaldeoApiError(f"Niepoprawny XML z Saldeo: {exc}") from exc
    if root.tag not in ("RESPONSE", "ROOT"):
        raise SaldeoApiError(f"Nieoczekiwana odpowiedź Saldeo: <{root.tag}>")
    status = _text(root.find("STATUS"))
    if status and status.upper() != "OK":
        raise SaldeoApiError(f"Saldeo zwróciło błąd: {status} {_text(root.find('ERROR_MESSAGE'))}".strip())
    return root


class SaldeoClient:
    def __init__(self, username: str, api_token: str, server: str = DEFAULT_SERVER,
                 session: Optional[requests.Session] = None, timeout: int = 30):
        if not username or not api_token:
            raise SaldeoApiError("Uzupełnij login i token API Saldeo w ustawieniach.")
        self.username = username
        self._token = api_token
        self.server = server.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_settings(cls) -> "SaldeoClient":
        import os
        from app.config import get_database  # noqa: F401  (ładuje .env)
        url = (os.getenv("SALDEO_API_URL") or get_setting(SERVER_KEY) or DEFAULT_SERVER).strip()
        return cls(
            (os.getenv("SALDEO_LOGIN_NAME") or get_setting(USERNAME_KEY) or "").strip(),
            (os.getenv("SALDEO_API_TOKEN") or get_setting(TOKEN_KEY) or "").strip(),
            url,
        )

    def _get(self, operation: str, version: str = API_VERSION, **extra: str) -> ElementTree.Element:
        params = {"username": self.username,
                  "req_id": f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}",
                  **{k: str(v) for k, v in extra.items() if v not in (None, "")}}
        params["req_sig"] = sign_request(params, self._token)
        url = f"{self.server}/api/xml/{version}/{operation}"
        try:
            response = self.session.get(url, params=params, headers={"Accept-Encoding": "gzip"}, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise SaldeoApiError(f"Błąd połączenia z Saldeo ({operation}, HTTP {status or 'brak'}).") from None
        return parse_response(response.content)

    def list_companies(self) -> List[Dict[str, Any]]:
        root = self._get("company/list")
        return [element_to_dict(node) for node in root.iter("COMPANY")]

    def _post_command(self, operation: str, version: str, xml_command: str, **extra: str) -> ElementTree.Element:
        import base64
        import gzip
        form = {"command": base64.b64encode(gzip.compress(xml_command.encode("utf-8"))).decode("ascii")}
        params = {"username": self.username,
                  "req_id": f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}",
                  **{k: str(v) for k, v in extra.items() if v not in (None, "")}}
        params["req_sig"] = sign_request({**params, **form}, self._token)
        url = f"{self.server}/api/xml/{version}/{operation}"
        try:
            response = self.session.post(url, params=params, data=form, headers={"Accept-Encoding": "gzip"},
                                         timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise SaldeoApiError(f"Błąd połączenia z Saldeo ({operation}, HTTP {status or 'brak'}).") from None
        return parse_response(response.content)

    def document_id_groups(self, company_program_id: str, year: int, month: int) -> ElementTree.Element:
        """document.getidlist 3.0: identyfikatory dokumentów z folderu rok/miesiąc, pogrupowane wg rodzaju."""
        xml = f"<ROOT><FOLDER><YEAR>{int(year)}</YEAR><MONTH>{int(month)}</MONTH></FOLDER></ROOT>"
        return self._post_command("document/getidlist", DOCUMENT_BY_MONTH_VERSION, xml,
                                  company_program_id=company_program_id)

    def documents_by_group_ids(self, company_program_id: str, groups: ElementTree.Element,
                               limit: int = 100) -> List[Dict[str, Any]]:
        """document.listbyid 3.0: pełne rekordy dla ID z odpowiedzi getidlist (do `limit` sztuk)."""
        root = ElementTree.Element("ROOT")
        left = limit
        for group in groups:
            if group.tag in ("METAINF", "STATUS") or not len(group):
                continue
            kept = ElementTree.SubElement(root, group.tag)
            for item in list(group)[:max(left, 0)]:
                kept.append(item)
                left -= 1
        xml = ElementTree.tostring(root, encoding="unicode")
        response = self._post_command("document/listbyid", DOCUMENT_BY_MONTH_VERSION, xml,
                                      company_program_id=company_program_id)
        return [element_to_dict(node) for node in response.iter("DOCUMENT")]

    def list_documents(self, company_program_id: str, policy: str = "LAST_10_DAYS") -> List[Dict[str, Any]]:
        """policy: LAST_10_DAYS, LAST_10_DAYS_OCRED lub SALDEO."""
        root = self._get("document/list", DOCUMENT_LIST_VERSION,
                         company_program_id=company_program_id, policy=policy)
        return [element_to_dict(node) for node in root.iter("DOCUMENT")]


def probe_connection(sample_company: Optional[str] = None) -> Dict[str, Any]:
    """Test odczytu: lista firm + pola dostępne w company.list i document.list (bez zapisu do bazy)."""
    client = SaldeoClient.from_settings()
    companies = client.list_companies()
    report: Dict[str, Any] = {
        "companies": len(companies),
        "company_fields": sorted({key for company in companies for key in company}),
        "document_fields": [],
    }
    target = sample_company or next((c.get("COMPANY_PROGRAM_ID") for c in companies if c.get("COMPANY_PROGRAM_ID")), None)
    if target:
        report["sample_company"] = target
        try:
            documents = client.list_documents(target)
            report["documents"] = len(documents)
            report["document_fields"] = sorted({key for doc in documents for key in doc})
        except SaldeoApiError as exc:
            report["document_error"] = str(exc)
    return report
