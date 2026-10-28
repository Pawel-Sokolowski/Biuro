"""Klient GUS BIR1 (wyszukiwarka REGON) - dane firmy po NIP.

Konfiguracja w pliku .env:
    GUS_API_KEY  klucz użytkownika (po rejestracji w GUS)
    GUS_API_URL  opcjonalnie adres usługi (domyślnie produkcyjny; dla klucza
                 testowego użyj https://wyszukiwarkaregontest.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc)
"""
import html
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

import requests

from app.utils.env_config import load_environment
from app.companies.address_parsing import split_street_address

load_environment()

DEFAULT_GUS_URL = "https://wyszukiwarkaregon.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc"
_NS = "http://CIS/BIR/PUBL/2014/07"
_DATA_NS = "http://CIS/BIR/PUBL/2014/07/DataContract"
_TIMEOUT = 15


class GusError(Exception):
    pass


def _config():
    key = (os.getenv("GUS_API_KEY") or "").strip()
    url = (os.getenv("GUS_API_URL") or "").strip() or DEFAULT_GUS_URL
    return key, url


def _envelope(url: str, action: str, body: str) -> bytes:
    return (
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" '
        f'xmlns:ns="{_NS}" xmlns:dat="{_DATA_NS}">'
        '<soap:Header xmlns:wsa="http://www.w3.org/2005/08/addressing">'
        f"<wsa:To>{url}</wsa:To>"
        f"<wsa:Action>{_NS}/IUslugaBIRzewnPubl/{action}</wsa:Action>"
        f"</soap:Header><soap:Body>{body}</soap:Body></soap:Envelope>"
    ).encode("utf-8")


def _call(url: str, action: str, body: str, sid: Optional[str] = None) -> str:
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    if sid:
        headers["sid"] = sid
    response = requests.post(url, data=_envelope(url, action, body), headers=headers, timeout=_TIMEOUT)
    if response.status_code != 200:
        raise GusError(f"Usługa GUS zwróciła błąd HTTP {response.status_code}.")
    return response.text


def _result(text: str, tag: str) -> str:
    """Wyciąga treść <tag> z odpowiedzi SOAP (także w formie multipart/MTOM)."""
    match = re.search(rf"<(?:\w+:)?{tag}[^>]*>(.*?)</(?:\w+:)?{tag}>", text, re.S)
    return html.unescape(match.group(1)).strip() if match else ""


def _text(node: ET.Element, name: str) -> str:
    child = node.find(name)
    return (child.text or "").strip() if child is not None else ""


def parse_search_result(xml_text: str) -> Optional[Dict[str, Any]]:
    """Zamienia odpowiedź DaneSzukajPodmioty na słownik pól formularza firmy."""
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    node = root.find("dane")
    if node is None:
        return None

    kod = _text(node, "KodPocztowy")
    if re.fullmatch(r"\d{5}", kod):
        kod = f"{kod[:2]}-{kod[2:]}"

    ulica = _text(node, "Ulica")
    budynek = _text(node, "NrNieruchomosci")
    lokal = _text(node, "NrLokalu")
    ulica, budynek, lokal = split_street_address(ulica, budynek, lokal)

    miasto = _text(node, "Miejscowosc")
    return {
        "nazwa": _text(node, "Nazwa"),
        "nip": _text(node, "Nip"),
        "regon": _text(node, "Regon"),
        "dz_miasto": miasto,
        "dz_kod": kod,
        "dz_ulica": ulica,
        "dz_budynek": budynek,
        "dz_lokal": lokal,
        "dz_wojewodztwo": _text(node, "Wojewodztwo").lower(),
        "dz_powiat": _text(node, "Powiat"),
        "dz_gmina": _text(node, "Gmina"),
        "dz_kraj": "Polska",
        "typ": _text(node, "Typ"),
    }


def get_gus_firma_by_nip(nip: str) -> Dict[str, Any]:
    """Zwraca dane firmy z GUS albo {"error": True, "message": ...}."""
    key, url = _config()
    if not key:
        return {"error": True, "message": "Brak klucza GUS_API_KEY w pliku .env."}

    nip = "".join(ch for ch in (nip or "") if ch.isdigit())
    if len(nip) != 10:
        return {"error": True, "message": "NIP musi składać się z 10 cyfr."}

    try:
        login = _call(url, "Zaloguj", f"<ns:Zaloguj><ns:pKluczUzytkownika>{html.escape(key)}</ns:pKluczUzytkownika></ns:Zaloguj>")
        sid = _result(login, "ZalogujResult")
        if not sid:
            return {"error": True, "message": "GUS odrzucił klucz API (logowanie nie powiodło się)."}

        search = _call(
            url,
            "DaneSzukajPodmioty",
            "<ns:DaneSzukajPodmioty><ns:pParametryWyszukiwania>"
            f"<dat:Nip>{nip}</dat:Nip>"
            "</ns:pParametryWyszukiwania></ns:DaneSzukajPodmioty>",
            sid=sid,
        )
        data = parse_search_result(_result(search, "DaneSzukajPodmiotyResult"))
    except requests.RequestException as exc:
        return {"error": True, "message": f"Brak połączenia z GUS: {exc}"}
    except GusError as exc:
        return {"error": True, "message": str(exc)}

    if not data:
        return {"error": True, "message": "Nie znaleziono firmy o podanym NIP w GUS."}
    return data
