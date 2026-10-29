"""Bezpieczna wysyłka gotowego XML FA(3) oraz kontrola wyniku KSeF.

Wysyłka jest inicjowana wyłącznie po potwierdzeniu użytkownika.
Status przyjęcia i numer KSeF są pobierane osobno, asynchronicznie po stronie MF.
"""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from app.db.dao_app_settings import get_setting
from app.db.dao_conn import get_conn
from app.db.dao_invoices import can_access_invoice, set_ksef_number
from app.invoices.ksef_client import KsefError
from app.invoices.ksef_sync import client_from_settings


def _child(root, *names) -> str:
    node = root
    for name in names:
        node = next(
            (child for child in node if child.tag.rsplit("}", 1)[-1] == name), None
        ) if node is not None else None
    return (node.text or "").strip() if node is not None else ""


def inspect_fa3_xml(xml: bytes) -> dict[str, str]:
    """Podstawowa kontrola FA3; nie zastępuje walidacji dokumentu XSD przez MF."""
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise KsefError("XML z deklaracją DTD lub encją nie jest obsługiwany.")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise KsefError(f"Niepoprawny XML: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "Faktura":
        raise KsefError("Plik nie jest dokumentem Faktura FA(3).")
    header = next((item for item in root if item.tag.rsplit("}", 1)[-1] == "Naglowek"), None)
    code = next(
        (item for item in header if item.tag.rsplit("}", 1)[-1] == "KodFormularza"),
        None,
    ) if header is not None else None
    if code is None or code.attrib.get("kodSystemowy") != "FA (3)" or code.attrib.get("wersjaSchemy") != "1-0E":
        raise KsefError("Wymagany schemat FA(3), wersja 1-0E.")
    number = _child(root, "Fa", "P_2")
    seller = _child(root, "Podmiot1", "DaneIdentyfikacyjne", "NIP")
    buyer = _child(root, "Podmiot2", "DaneIdentyfikacyjne", "NIP")
    if not number or not seller:
        raise KsefError("Dokument FA(3) musi zawierać numer faktury i NIP wystawcy.")
    return {"number": number, "seller_nip": seller, "buyer_nip": buyer}


def _authorized_invoice(invoice_id: int, actor_id: int):
    from app.security.authorization import can, is_administrator
    if not (is_administrator(actor_id) or can(actor_id, "can_export_data")):
        raise PermissionError("Brak uprawnień do wysyłania faktur do KSeF.")
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM faktury WHERE id = %s", (invoice_id,)).fetchone()
        if not row or not can_access_invoice(actor_id, row["firma_id"]):
            raise PermissionError("Brak dostępu do wybranej faktury.")
        return dict(row)
    finally:
        conn.close()


def _update_attempt(invoice_id: int, status: str, session=None, invoice_ref=None) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE faktury SET ksef_status = %s,
               ksef_referencja_sesji = COALESCE(%s, ksef_referencja_sesji),
               ksef_referencja_faktury = COALESCE(%s, ksef_referencja_faktury)
            WHERE id = %s
            """,
            (status, session, invoice_ref, invoice_id),
        )
        conn.commit()
    finally:
        conn.close()


def send_invoice_xml(invoice_id: int, xml_path: str, actor_id: int) -> dict:
    """Wymaga zatwierdzonego pliku FA3 i nie pozwala na niekontrolowaną ponowną wysyłkę."""
    invoice = _authorized_invoice(invoice_id, actor_id)
    if invoice.get("ksef_numer"):
        raise KsefError("Faktura ma już numer KSeF; nie można wysłać jej ponownie.")
    if invoice.get("ksef_status") in ("wysylanie", "oczekuje", "do_sprawdzenia", "przyjeta"):
        raise KsefError("Wysyłka była już rozpoczęta. Najpierw sprawdź status w KSeF.")
    source = Path(xml_path)
    if source.suffix.lower() != ".xml" or source.stat().st_size > 10 * 1024 * 1024:
        raise KsefError("Wybierz poprawny plik FA(3) XML o rozmiarze do 10 MB.")
    xml = source.read_bytes()
    metadata = inspect_fa3_xml(xml)
    number = str(invoice.get("numer") or "").strip()
    if not number or metadata["number"] != number:
        raise KsefError("Numer faktury w XML musi być identyczny z numerem wybranego dokumentu.")
    seller = "".join(c for c in metadata["seller_nip"] if c.isdigit())
    nip = "".join(c for c in (get_setting("ksef_nip") or "") if c.isdigit())
    if not nip or seller != nip:
        raise KsefError("NIP sprzedawcy w pliku XML różni się od NIP skonfigurowanego w KSeF.")
    if invoice.get("firma_id"):
        conn = get_conn()
        try:
            firm = conn.execute("SELECT nip FROM firmy WHERE id = %s", (invoice["firma_id"],)).fetchone()
        finally:
            conn.close()
        expected_buyer = "".join(c for c in str((firm["nip"] if firm else "") or "") if c.isdigit())
        if expected_buyer and expected_buyer != "".join(c for c in metadata["buyer_nip"] if c.isdigit()):
            raise KsefError("NIP nabywcy z XML nie zgadza się z firmą przypisaną do faktury.")
    _update_attempt(invoice_id, "wysylanie")
    client = client_from_settings()
    try:
        response = client.send_fa3_xml(
            xml,
            on_session=lambda ref: _update_attempt(invoice_id, "do_sprawdzenia", session=ref),
            on_invoice=lambda ref: _update_attempt(invoice_id, "oczekuje", invoice_ref=ref),
        )
        _update_attempt(invoice_id, "oczekuje")
    except Exception:
        # Nawet timeout po HTTP 202 nie gwarantuje, że MF nie otrzymało XML.
        # Celowo blokujemy ponowną wysyłkę do czasu ręcznego wyjaśnienia.
        _update_attempt(invoice_id, "do_sprawdzenia")
        raise
    return response


def refresh_invoice_submission(invoice_id: int, actor_id: int) -> dict:
    invoice = _authorized_invoice(invoice_id, actor_id)
    session = invoice.get("ksef_referencja_sesji")
    ref = invoice.get("ksef_referencja_faktury")
    if not session or not ref:
        raise KsefError("Brak referencji sesji/faktury. Sprawdź stan w panelu KSeF.")
    result = client_from_settings().invoice_submission_status(session, ref)
    state = result.get("status") or {}
    code = state.get("code")
    if code == 200 and result.get("ksefNumber"):
        set_ksef_number(invoice_id, result["ksefNumber"])
        _update_attempt(invoice_id, "przyjeta")
    elif code is not None and code >= 400:
        _update_attempt(invoice_id, "odrzucona")
    else:
        _update_attempt(invoice_id, "oczekuje")
    return result
