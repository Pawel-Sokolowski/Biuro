"""JPK_V7M(3): ewidencja sprzedaży z numerami KSeF (do importu w Enovie)."""
from datetime import datetime, timezone
from decimal import Decimal
from xml.etree import ElementTree as ET

from app.invoices.vat_summary import vat_summary

NS = "http://crd.gov.pl/wzor/2025/12/19/14090/"
SCHEMA = "JPK_V7M3.xsd"

# stawka -> (pole netto, pole VAT)
RATE_FIELDS = {
    "23": ("K_19", "K_20"), "22": ("K_19", "K_20"),
    "8": ("K_17", "K_18"), "7": ("K_17", "K_18"),
    "5": ("K_15", "K_16"),
    "0": ("K_13", None), "0 kr": ("K_13", None),
    "zw": ("K_10", None),
}
FIELD_ORDER = ["K_10", "K_13", "K_15", "K_16", "K_17", "K_18", "K_19", "K_20"]


class JpkError(ValueError):
    pass


def _digits(value):
    return "".join(c for c in str(value or "") if c.isdigit())


def _sub(parent, name, text=None, **attrs):
    node = ET.SubElement(parent, "{%s}%s" % (NS, name), attrs)
    if text is not None:
        node.text = str(text)
    return node


def build_jpk_v7m(invoices, seller, year, month, office_code, system_name="Biuro"):
    """invoices: lista słowników faktury z kluczem 'positions'. Zwraca bajty XML."""
    if len(_digits(seller.get("nip"))) != 10:
        raise JpkError("Uzupełnij NIP sprzedawcy w ustawieniach faktur.")
    if not str(seller.get("name") or "").strip():
        raise JpkError("Uzupełnij nazwę sprzedawcy w ustawieniach faktur.")
    if len(_digits(office_code)) != 4:
        raise JpkError("Kod urzędu skarbowego musi mieć 4 cyfry.")
    missing = [i.get("numer") for i in invoices if not i.get("ksef_numer") and _digits(i.get("nabywca_nip"))]
    if missing:
        raise JpkError("Brak numeru KSeF dla faktur: " + ", ".join(str(n) for n in missing))
    if not invoices:
        raise JpkError("Brak faktur do eksportu.")

    ET.register_namespace("", NS)
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root = ET.Element("{%s}JPK" % NS, {
        "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation": "%s %s" % (NS, SCHEMA)})
    head = _sub(root, "Naglowek")
    _sub(head, "KodFormularza", "JPK_VAT", kodSystemowy="JPK_V7M (3)", wersjaSchemy="1-0E")
    _sub(head, "WariantFormularza", "3")
    _sub(head, "DataWytworzeniaJPK", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _sub(head, "NazwaSystemu", system_name)
    _sub(head, "CelZlozenia", "1", poz="P_7")
    _sub(head, "KodUrzedu", _digits(office_code))
    _sub(head, "Rok", int(year))
    _sub(head, "Miesiac", int(month))
    entity = _sub(_sub(root, "Podmiot1", rola="Podatnik"), "OsobaNiefizyczna")
    _sub(entity, "NIP", _digits(seller["nip"]))
    _sub(entity, "PelnaNazwa", seller["name"])
    email = str(seller.get("email") or "").strip()
    if "@" not in email:
        raise JpkError("Podaj poprawny adres e-mail podatnika (wymagany w JPK_V7M).")
    _sub(entity, "Email", email)

    ewidencja = _sub(root, "Ewidencja")
    total_tax = Decimal("0")
    for number, invoice in enumerate(invoices, 1):
        sums = {}
        for group in vat_summary(invoice.get("positions") or []):
            fields = RATE_FIELDS.get(group["code"])
            if not fields:
                raise JpkError("Nieobsługiwana stawka VAT %s na fakturze %s." % (group["code"], invoice.get("numer")))
            sums[fields[0]] = sums.get(fields[0], Decimal("0")) + group["net"]
            if fields[1]:
                sums[fields[1]] = sums.get(fields[1], Decimal("0")) + group["vat"]
                total_tax += group["vat"]
        row = _sub(ewidencja, "SprzedazWiersz")
        _sub(row, "LpSprzedazy", number)
        buyer_nip = _digits(invoice.get("nabywca_nip"))
        if buyer_nip:
            _sub(row, "KodKrajuNadaniaTIN", "PL")
            _sub(row, "NrKontrahenta", buyer_nip)
        else:
            _sub(row, "NrKontrahenta", "brak")
        _sub(row, "NazwaKontrahenta", invoice.get("nabywca_pelna") or invoice.get("nabywca_skrocona") or "brak")
        _sub(row, "DowodSprzedazy", invoice.get("numer"))
        issue_date = str(invoice.get("data_wystawienia") or "")[:10]
        _sub(row, "DataWystawienia", issue_date)
        sale_date = str(invoice.get("data_sprzedazy") or "")[:10]
        if sale_date and sale_date != issue_date:
            _sub(row, "DataSprzedazy", sale_date)
        if invoice.get("ksef_numer"):
            _sub(row, "NrKSeF", invoice["ksef_numer"])
        else:
            _sub(row, "BFK", "1")
        if invoice.get("rodzaj") == "korekta":
            _sub(row, "KorektaPodstawyOpodt", "1")
            if invoice.get("termin_platnosci"):
                _sub(row, "TerminPlatnosci", str(invoice["termin_platnosci"])[:10])
        for field in FIELD_ORDER:
            if field in sums:
                _sub(row, field, "%.2f" % sums[field])
    control = _sub(ewidencja, "SprzedazCtrl")
    _sub(control, "LiczbaWierszySprzedazy", len(invoices))
    _sub(control, "PodatekNalezny", "%.2f" % total_tax)
    purchases = _sub(ewidencja, "ZakupCtrl")
    _sub(purchases, "LiczbaWierszyZakupow", 0)
    _sub(purchases, "PodatekNaliczony", "0.00")
    ET.indent(root)
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)
