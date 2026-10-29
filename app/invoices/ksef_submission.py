"""Bezpieczna wysyłka gotowego XML FA(3) oraz kontrola wyniku KSeF.

Wysyłka jest inicjowana wyłącznie po potwierdzeniu użytkownika.
Status przyjęcia i numer KSeF są pobierane osobno, asynchronicznie po stronie MF.
"""
from __future__ import annotations

from pathlib import Path
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import base64
from xml.etree import ElementTree

from app.db.dao_app_settings import get_setting
from app.db.dao_conn import get_conn
from app.db.dao_invoices import can_access_invoice
from app.invoices.ksef_client import KsefError
from app.invoices.ksef_sync import client_from_settings, ENV_KEY, NIP_KEY


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
    return {
        "number": number, "seller_nip": seller, "buyer_nip": buyer,
        "buyer_name": _child(root, "Podmiot2", "DaneIdentyfikacyjne", "Nazwa"),
        "buyer_no_id": _child(root, "Podmiot2", "DaneIdentyfikacyjne", "BrakID"),
        "gross": _child(root, "Fa", "P_15"),
        "currency": _child(root, "Fa", "KodWaluty"),
        "issue_date": _child(root, "Fa", "P_1"),
        "kind": _child(root, "Fa", "RodzajFaktury"),
    }


def _digits(value) -> str:
    return "".join(c for c in str(value or "") if c.isdigit())


def _money(value) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result != result.quantize(Decimal("0.01")):
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise KsefError("Kwota brutto faktury w XML jest nieprawidłowa.") from exc


def _validate_invoice_xml(invoice: dict, metadata: dict) -> str:
    if not invoice.get("eksportowana"):
        raise KsefError("Do KSeF można wysłać wyłącznie fakturę wystawioną.")
    number = str(invoice.get("numer") or "").strip()
    if not number or metadata["number"] != number:
        raise KsefError("Numer faktury w XML musi być identyczny z numerem wybranego dokumentu.")
    if _money(metadata["gross"]) != _money(invoice.get("kwota_brutto")):
        raise KsefError("Kwota brutto w XML nie zgadza się z wybraną fakturą.")
    if metadata["currency"] != (invoice.get("waluta") or "PLN"):
        raise KsefError("Waluta w XML nie zgadza się z wybraną fakturą.")
    try:
        xml_date = date.fromisoformat(metadata["issue_date"])
        invoice_date = date.fromisoformat(str(invoice.get("data_wystawienia") or ""))
    except ValueError as exc:
        raise KsefError("Nieprawidłowa data wystawienia w XML lub wybranej fakturze.") from exc
    if xml_date != invoice_date:
        raise KsefError("Data wystawienia w XML nie zgadza się z wybraną fakturą.")
    expected_kind = {"faktura": "VAT", "korekta": "KOR"}.get(invoice.get("rodzaj") or "faktura")
    if metadata["kind"] != expected_kind:
        raise KsefError("Rodzaj faktury w XML nie zgadza się z wybranym dokumentem.")
    nip = _digits(get_setting(NIP_KEY))
    if not nip or _digits(metadata["seller_nip"]) != nip:
        raise KsefError("NIP sprzedawcy w pliku XML różni się od NIP skonfigurowanego w KSeF.")
    # Dane utrwalone na fakturze mają pierwszeństwo przed późniejszą edycją
    # kartoteki. Dotyczy to również faktur ręcznych i osób bez NIP.
    if invoice.get("nabywca_pelna") or invoice.get("nabywca_nip") is not None:
        buyer = {"nip": invoice.get("nabywca_nip"), "nazwa": invoice.get("nabywca_pelna")}
    else:
        conn = get_conn()
        try:
            if invoice.get("firma_id"):
                buyer = conn.execute("SELECT nip, nazwa FROM firmy WHERE id = %s", (invoice["firma_id"],)).fetchone()
            elif invoice.get("kontrahent_id"):
                buyer = conn.execute("SELECT nip, nazwa FROM kontrahenci WHERE id = %s", (invoice["kontrahent_id"],)).fetchone()
            else:
                buyer = None
        finally:
            conn.close()
    if not buyer:
        raise KsefError("Brak danych nabywcy do porównania z XML.")
    expected_buyer = _digits(buyer["nip"])
    if expected_buyer:
        if expected_buyer != _digits(metadata["buyer_nip"]):
            raise KsefError("NIP nabywcy z XML nie zgadza się z wybraną fakturą.")
    else:
        expected_name = " ".join(str(buyer["nazwa"] or "").split()).casefold()
        actual_name = " ".join(metadata["buyer_name"].split()).casefold()
        if metadata["buyer_nip"] or metadata["buyer_no_id"] != "1" or not expected_name or actual_name != expected_name:
            raise KsefError("Dane nabywcy bez NIP w XML nie zgadzają się z wybraną fakturą.")
    return nip


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


def _update_attempt(invoice_id: int, status: str, document_hash: str, *, expected_session=None,
                    session=None, invoice_ref=None) -> bool:
    conn = get_conn()
    try:
        updated = conn.execute(
            """
            UPDATE faktury SET ksef_status = %s,
               ksef_referencja_sesji = COALESCE(%s, ksef_referencja_sesji),
               ksef_referencja_faktury = COALESCE(%s, ksef_referencja_faktury)
            WHERE id = %s
              AND ksef_referencja_sesji IS NOT DISTINCT FROM %s
              AND ksef_hash_faktury = %s
              AND ksef_status IN ('wysylanie', 'oczekuje', 'do_sprawdzenia')
            RETURNING id
            """,
            (status, session, invoice_ref, invoice_id, expected_session, document_hash),
        ).fetchone()
        conn.commit()
        return updated is not None
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
    nip = _validate_invoice_xml(invoice, metadata)
    environment = get_setting(ENV_KEY) or "prod"
    document_hash = base64.b64encode(hashlib.sha256(xml).digest()).decode("ascii")
    client = client_from_settings()
    # Atomowo rezerwujemy dokument przed pierwszym wywołaniem HTTP do KSeF.
    # Dwie jednoczesne akcje użytkowników nie mogą wysłać tej samej faktury.
    conn = get_conn()
    try:
        updated = conn.execute(
            """
            UPDATE faktury SET ksef_status = 'wysylanie',
              ksef_referencja_sesji = NULL, ksef_referencja_faktury = NULL,
              ksef_srodowisko_wysylki = %s, ksef_nip_wysylki = %s, ksef_hash_faktury = %s
            WHERE id = %s
              AND COALESCE(eksportowana, 0) = 1
              AND numer = %s AND kwota_brutto = %s
              AND NULLIF(BTRIM(COALESCE(ksef_numer, '')), '') IS NULL
              AND COALESCE(ksef_status, '') NOT IN
                  ('wysylanie', 'oczekuje', 'do_sprawdzenia', 'przyjeta')
            RETURNING id
            """,
            (environment, nip, document_hash, invoice_id, invoice["numer"], invoice["kwota_brutto"]),
        ).fetchone()
        if not updated:
            raise KsefError("Faktura jest już wysyłana lub została przyjęta w KSeF.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    upload_started = False
    session_reference = None
    invoice_reference = None

    def session_opened(ref):
        nonlocal session_reference
        if not _update_attempt(invoice_id, "wysylanie", document_hash, session=ref):
            raise KsefError("Stan wysyłki zmienił się przed otwarciem sesji. Sprawdź status ponownie.")
        session_reference = ref

    def invoice_uploaded(ref):
        nonlocal invoice_reference
        if not _update_attempt(invoice_id, "oczekuje", document_hash,
                               expected_session=session_reference, invoice_ref=ref):
            raise KsefError("Stan wysyłki zmienił się po przekazaniu faktury. Sprawdź status ponownie.")
        invoice_reference = ref

    def before_upload():
        nonlocal upload_started
        # Zapis następuje przed HTTP POST faktury, więc timeout lub przerwanie
        # procesu po tym punkcie nigdy nie otwiera automatycznie drogi do duplikatu.
        if not session_reference or not _update_attempt(
            invoice_id, "do_sprawdzenia", document_hash, expected_session=session_reference
        ):
            raise KsefError("Stan wysyłki zmienił się przed wysłaniem faktury. Sprawdź status ponownie.")
        upload_started = True

    try:
        response = client.send_fa3_xml(
            xml,
            on_session=session_opened,
            on_invoice=invoice_uploaded,
            on_upload=before_upload,
        )
    except Exception:
        # Błąd autoryzacji lub otwarcia sesji nie jest wysyłką dokumentu.
        # Po rozpoczęciu POST nie zakładamy, że brak odpowiedzi oznacza brak faktury.
        if not invoice_reference:
            _update_attempt(invoice_id, "do_sprawdzenia" if upload_started else "blad_przed_wysylka",
                            document_hash, expected_session=session_reference)
        raise
    return response


def refresh_invoice_submission(invoice_id: int, actor_id: int) -> dict:
    invoice = _authorized_invoice(invoice_id, actor_id)
    session = invoice.get("ksef_referencja_sesji")
    ref = invoice.get("ksef_referencja_faktury")
    environment = invoice.get("ksef_srodowisko_wysylki")
    nip = invoice.get("ksef_nip_wysylki")
    if not environment or not nip:
        raise KsefError("Brak zapisanego środowiska i NIP tej wysyłki. Sprawdź stan w panelu KSeF.")
    if environment != (get_setting(ENV_KEY) or "prod") or nip != _digits(get_setting(NIP_KEY)):
        raise KsefError(f"Ta wysyłka dotyczy środowiska {environment.upper()} i NIP {nip}. Przywróć te ustawienia, aby sprawdzić status.")
    if not session:
        raise KsefError("Brak referencji sesji. Jeżeli błąd wystąpił przed wysyłką, możesz ponowić wysyłkę dokumentu.")
    client = client_from_settings()
    if not ref:
        document_hash = invoice.get("ksef_hash_faktury")
        result = client.find_submission_by_hash(session, document_hash) if document_hash else None
        if not result:
            raise KsefError("Nie ustalono wyniku poprzedniej wysyłki. Sprawdź sesję w panelu KSeF przed kolejną próbą.")
        ref = result["referenceNumber"]
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE faktury SET ksef_referencja_faktury = %s WHERE id = %s AND ksef_referencja_sesji = %s",
                (ref, invoice_id, session),
            )
            conn.commit()
        finally:
            conn.close()
    else:
        result = client.invoice_submission_status(session, ref)
    state = result.get("status") or {}
    code = state.get("code")
    accepted = code == 200 and bool(result.get("ksefNumber"))
    status = "przyjeta" if accepted else "odrzucona" if code is not None and code >= 400 else "oczekuje"
    conn = get_conn()
    try:
        updated = conn.execute(
            """UPDATE faktury SET ksef_status = %s,
                   ksef_numer = CASE WHEN %s THEN %s ELSE ksef_numer END,
                   ksef_data = CASE WHEN %s THEN to_char(now(), 'YYYY-MM-DD HH24:MI:SS') ELSE ksef_data END
               WHERE id = %s AND ksef_referencja_sesji = %s AND ksef_referencja_faktury = %s
                 AND ksef_srodowisko_wysylki = %s AND ksef_nip_wysylki = %s RETURNING id""",
            (status, accepted, result.get("ksefNumber"), accepted, invoice_id, session, ref, environment, nip),
        ).fetchone()
        if not updated:
            raise KsefError("Stan wysyłki zmienił się podczas pobierania odpowiedzi. Sprawdź status ponownie.")
        conn.commit()
    finally:
        conn.close()
    return result
