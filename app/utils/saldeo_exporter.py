# app/utils/saldeo_exporter.py
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Dict, Any, Optional

import xlsxwriter

from app.utils.xlsx_safe import safe_cell
from app.db.dao_conn import get_conn, Row
from app.db.dao_app_settings import get_setting_bool, get_setting_first
import logging

logger = logging.getLogger(__name__)

# Nagłówki zgodne z Twoim wzorem
SALDEO_HEADERS = [
    "Lp.", "Sufiks", "Własny opis faktury", "Metoda kasowa", "VAT marża",
    "Data wystawienia", "Data dostawy", "Termin płatności", "Wyślij do KSeF",
    "Wyślij do kontrahenta", "Nabywca (nazwa skrócona kontrahenta)",
    "Nazwa pełna kontrahenta", "Adres", "Kod pocztowy", "Miejscowość",
    "NIP", "REGON", "Kraj - skrót kraju w formacie ISO", "Adres e-mail",
    "Odbiorca (nazwa skrócona kontrahenta)", "Nazwa pełna (odbiorcy)",
    "Rola odbiorcy", "ID WEW", "Adres (odbiorcy)", "Kod pocztowy (odbiorcy)",
    "Miejscowość (odbiorcy)", "NIP (odbiorcy)", "REGON (odbiorcy)",
    "Kraj - skrót kraju w formacie ISO (odbiorcy)", "Adres e-mail (odbiorcy)",
    "Waluta", "Nazwa towaru", "PKWiU", "Ilość", "Jednostka",
    "Cena jedn. netto", "Cena jedn. brutto", "Stawka VAT",
    "Podstawa zastosowania stawki ZW", "Forma płatności", "Zapłacono",
    "Data wpłaty", "Konto bankowe", "Rodzaj środka transportu",
    "Data dopuszczenia", "Liczba rbh / Przebieg pojazdu", "Uwagi",
    "Wystawca faktury", "Grupa Towarowa", "Numer zamówienia",
    "Data zamówienia", "Numer umowy", "Data umowy", "Kategoria",
    "Opis", "Rejestr"
]


class SaldeoExportError(ValueError):
    """Dane nie mogą zostać bezpiecznie zapisane do szablonu faktur Saldeo."""


class SaldeoCorrectionExportError(SaldeoExportError):
    def __init__(self, corrections):
        self.corrections = tuple(corrections)
        labels = []
        for invoice in self.corrections[:5]:
            label = str(_get_field(invoice, "numer") or f"ID {_get_field(invoice, 'id', '?')}")
            original = (_get_field(invoice, "korygowana_faktura_numer")
                        or f"ID {_get_field(invoice, 'korygowana_faktura_id', '?')}")
            labels.append(f"{label} → faktura pierwotna {original}")
        if len(self.corrections) > 5:
            labels.append(f"… i {len(self.corrections) - 5} kolejnych korekt")
        super().__init__(
            "Korekt nie można wyeksportować tym szablonem XLSX do Saldeo. "
            "Szablon tworzy zwykłe faktury i nie zawiera powiązania z fakturą korygowaną. "
            "Nie zmieniaj ujemnych wartości na dodatnie.\n\n"
            + "\n".join(labels)
            + "\n\nW Saldeo otwórz właściwą fakturę pierwotną i wystaw do niej korektę. "
            "Następnie zapisz nadany numer przy lokalnej korekcie. "
            "Jeżeli zaznaczono także zwykłe faktury, wyeksportuj je osobno. "
            "Nie zapisano pliku i nie oznaczono dokumentów jako wystawionych."
        )


def _invoice_positions(invoice):
    value = _get_field(invoice, "pozycje_json", _get_field(invoice, "pozycje", "[]"))
    try:
        positions = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise SaldeoExportError("Nieprawidłowe dane pozycji faktury. Popraw je przed eksportem do Saldeo.") from exc
    if positions is None:
        return []
    if not isinstance(positions, list) or any(not isinstance(position, dict) for position in positions):
        raise SaldeoExportError("Nieprawidłowe dane pozycji faktury. Popraw je przed eksportem do Saldeo.")
    return positions


def validate_saldeo_invoices(invoices: List[Dict[str, Any]]) -> None:
    """Kontroluje cały zestaw przed utworzeniem/nadpisaniem pliku i zmianą statusów.

    SALDEO_HEADERS opisują import zwykłych faktur, bez typu korekty ani numeru
    faktury pierwotnej. Korekta pozostaje odrębnym dokumentem z własnymi znakami.
    """
    corrections = [invoice for invoice in invoices
                   if str(_get_field(invoice, "rodzaj", "")).strip().lower() == "korekta"
                   or _get_field(invoice, "korygowana_faktura_id", None) is not None]
    if corrections:
        raise SaldeoCorrectionExportError(corrections)
    for invoice in invoices:
        values = [_get_field(invoice, key, None) for key in ("kwota_netto", "kwota_brutto")]
        for position in _invoice_positions(invoice):
            values.extend(position.get(key) for key in (
                "ilosc", "quantity", "cena_netto", "netto", "wartosc_netto", "cena_brutto", "brutto",
            ))
        for value in values:
            if value in (None, ""):
                continue
            try:
                amount = Decimal(str(value).strip().replace(",", "."))
            except (InvalidOperation, ValueError) as exc:
                raise SaldeoExportError("Nieprawidłowa ilość lub kwota pozycji przed eksportem do Saldeo.") from exc
            if not amount.is_finite():
                raise SaldeoExportError("Ilości i kwoty w eksporcie Saldeo muszą być skończonymi liczbami.")
            if amount < 0:
                raise SaldeoExportError(
                    "Zwykła faktura zawiera ujemną ilość lub kwotę. Nie można importować jej "
                    "jako zwykłej faktury w Saldeo. Jeżeli to korekta, wystaw ją w Saldeo "
                    "na podstawie faktury pierwotnej i zapisz jej numer w lokalnej korekcie. "
                    "Nie zmieniaj znaków wartości. Nie zapisano pliku."
                )


# -------------------------
# Helpery
# -------------------------
def _get_conn():
    """Zwraca połączenie z DB (deleguje do dao_conn.get_conn)."""
    return get_conn()


def _get_field(obj: Any, key: str, default: Any = "") -> Any:
    """Defensywny getter: działa z dict i Row."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except Exception:
        try:
            return obj.get(key, default)
        except Exception:
            return default


def _get_export_bool(*keys: str, default: bool = False) -> bool:
    return get_setting_bool(keys, default=default)

def format_amount_pl(amount: float) -> str:
    """Format kwoty w stylu polskim: 1 234,56"""
    try:
        return f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(amount)

def _default_due_date(issue_date: Any, days_raw: Any) -> str:
    try:
        from app.utils.payment_terms import default_due_date
        issue = datetime.strptime(str(issue_date)[:10], "%Y-%m-%d").date()
        return default_due_date(issue, int(days_raw)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _parse_vat(raw: Any, default: Any):
    """Zwraca (etykieta dla Saldeo, procent). Obsługuje 23%, 0 WDT, zw., np."""
    text = str(raw).strip() if raw not in (None, "") else ""
    low = text.lower().replace(" ", "")
    if low.startswith("zw"):
        return "zw.", 0.0
    if low.startswith("np"):
        return text.strip(), 0.0
    if low.startswith("0") and re.search(r"[a-z]", low):
        return text.upper().replace("%", "").strip(), 0.0
    try:
        pct = float(text.replace("%", "").replace(",", ".")) if text else float(default)
    except Exception:
        pct = float(default)
    return (f"{int(pct)}%" if pct == int(pct) else f"{pct}%"), pct


def _fmt_paid(value: Any) -> str:
    try:
        return format_amount_pl(float(value)) if value not in ("", None) else ""
    except Exception:
        return str(value)


def export_saldeo_xlsx(
    invoices: List[Dict[str, Any]],
    output_path: str,
    metoda_kasowa: Optional[bool] = None,
    wysylac_do_ksef: Optional[bool] = None,
    wysylac_do_kontrahenta: Optional[bool] = None,
) -> None:
    validate_saldeo_invoices(invoices)
    default_metoda_kasowa = _get_export_bool("eksport_kasowa", "faktury_metoda_kasowa", "domyslnie_metoda_kasowa")
    default_wysylac_do_ksef = _get_export_bool("eksport_do_ksef", "faktury_ksef", "domyslnie_wysylac_do_ksef")
    default_wysylac_do_kontrahenta = _get_export_bool(
        "eksport_do_kontrahenta",
        "faktury_wysylka_kontrahent",
        "domyslnie_wysylac_do_kontrahenta",
    )

    default_vat_str = get_setting_first(("domyslna_stawka_vat", "default_vat"), "23")
    default_gtu_str = get_setting_first(("domyslne_gtu", "default_gtu"), "")
    default_wystawca = get_setting_first(("wystawca_faktur", "wystawca", "faktury_wystawca"), "")
    default_konto = get_setting_first(("konto_bankowe_biura", "faktury_konto_bankowe"), "")
    default_termin_dni = get_setting_first(("termin_platnosci_dni", "faktury_termin_dni"), "14")

    def _to_bool(v: Any) -> Optional[bool]:
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        try:
            s = str(v).strip().lower()
        except Exception:
            return None
        if s in ("1", "true", "x", "yes", "tak"):
            return True
        if s in ("0", "false", "no", "nie"):
            return False
        try:
            return bool(int(s))
        except Exception:
            return None

    p = Path(output_path)
    workbook = xlsxwriter.Workbook(str(p))
    worksheet = workbook.add_worksheet("Eksport")

    fmt = workbook.add_format({"font_name": "Calibri", "font_size": 11})
    fmt_bold = workbook.add_format({"bold": True, "font_name": "Calibri", "font_size": 11})

    for col, header in enumerate(SALDEO_HEADERS):
        worksheet.write(0, col, header, fmt_bold)

    conn = _get_conn()
    cur = conn.cursor()

    row_idx = 1
    lp_counter = 1

    for fv in invoices:
        firma_id = _get_field(fv, "firma_id", _get_field(fv, "id", None))

        miesiac = _get_field(fv, "miesiac", _get_field(fv, "data", ""))
        data_wyst = _get_field(fv, "data_wystawienia", "") or (
            f"{miesiac}-01"
            if miesiac and re.match(r"^\d{4}-\d{2}$", str(miesiac))
            else _get_field(fv, "data", "")
        )
        termin = _get_field(fv, "termin_platnosci", "") or _default_due_date(data_wyst, default_termin_dni)

        fr = {}
        if firma_id is not None:
            try:
                cur.execute("""
                    SELECT
                        nazwa, nazwa_skrocona, nazwa_saldeo, nip, regon,
                        miasto, kod_pocztowy, ulica,
                        adres_budynek, adres_lokal, adres_email,
                        konto_bankowe, uwagi, kategoria_domyslna,
                        forma_opodatkowania, vat_czynny, glowny_cennik, limit_dokumentow,
                        wysylac_do_ksef, wysylac_do_kontrahenta, metoda_kasowa,
                        adres_koresp_ulica, adres_koresp_budynek, adres_koresp_lokal,
                        adres_koresp_kod_pocztowy, adres_koresp_miasto,
                        adres_zam_ulica, adres_zam_budynek, adres_zam_lokal,
                        adres_zam_kod_pocztowy, adres_zam_miasto
                    FROM firmy WHERE id = %s
                """, (firma_id,))
                row = cur.fetchone()
                if row:
                    fr = dict(row)
            except Exception:
                logger.exception("Błąd odczytu danych firmy %s podczas eksportu Saldeo", firma_id)
                raise

        firm_name = fr.get("nazwa") or _get_field(fv, "firma_nazwa", _get_field(fv, "nazwa_firmy", ""))
        nazwa_pelna = _get_field(fv, "nabywca_pelna", "") or firm_name
        # Kontrahent w Saldeo ma własną nazwę skróconą; nazwa rejestrowa pozostaje niezależna.
        nazwa_skrocona = (fr.get("nazwa_saldeo") or _get_field(fv, "nabywca_skrocona", "")
                         or fr.get("nazwa_skrocona") or nazwa_pelna)
        nip = _get_field(fv, "nabywca_nip", "") or fr.get("nip") or _get_field(fv, "firma_nip", "")
        regon = _get_field(fv, "nabywca_regon", "") or fr.get("regon") or ""
        def _alt(field):
            return fr.get(field) or fr.get(field.replace("adres_koresp_", "adres_zam_", 1)) or ""

        miasto = (_get_field(fv, "nabywca_miasto", "") or fr.get("miasto")
                  or _get_field(fv, "firma_miasto", "") or _alt("adres_koresp_miasto"))
        kod = (_get_field(fv, "nabywca_kod", "") or fr.get("kod_pocztowy")
               or _get_field(fv, "firma_kod", "") or _alt("adres_koresp_kod_pocztowy"))
        inv_adres = _get_field(fv, "nabywca_adres", "")
        if inv_adres:
            adres = inv_adres
        else:
            ulica = fr.get("ulica") or _get_field(fv, "firma_ulica", "")
            budynek = fr.get("adres_budynek") or ""
            lokal = fr.get("adres_lokal") or ""
            if not ulica and not budynek:
                ulica = _alt("adres_koresp_ulica")
                budynek = _alt("adres_koresp_budynek")
                lokal = _alt("adres_koresp_lokal")
            adres = " ".join(p for p in [ulica, budynek, f"/{lokal}" if lokal else ""] if p).replace(" /", "/").strip()
        if not adres:
            # Saldeo wymaga adresu; dla miejscowości bez ulic adresem jest sama miejscowość
            adres = miasto
        email = _get_field(fv, "nabywca_email", "") or fr.get("adres_email") or _get_field(fv, "firma_email", "")
        kraj = _get_field(fv, "nabywca_kraj", "") or "PL"
        # Konto bankowe w Saldeo to rachunek wystawcy, nie nabywcy.
        konto_bankowe = default_konto or _get_field(fv, "konto_bankowe", "")
        wystawca = _get_field(fv, "wystawca", "") or default_wystawca
        kategoria_domyslna = fr.get("kategoria_domyslna") or ""
        odb_skrocona = _get_field(fv, "odbiorca_skrocona", "") or ""
        odb_pelna = _get_field(fv, "odbiorca_pelna", "") or ""
        has_odbiorca = bool(odb_skrocona or odb_pelna)

        pozycje = _invoice_positions(fv)

        price_mode = "netto"
        if pozycje and not any(
            p.get("netto") or p.get("cena_netto") for p in pozycje
        ) and any(p.get("brutto") or p.get("cena_brutto") for p in pozycje):
            price_mode = "brutto"

        inv_metoda_kasowa = _get_field(fv, "metoda_kasowa", None)
        inv_wysylac_do_ksef = _get_field(fv, "wyslij_do_ksef", _get_field(fv, "wysylac_do_ksef", None))
        inv_wysylac_do_kontrahenta = _get_field(fv, "wyslij_do_kontrahenta", _get_field(fv, "wysylac_do_kontrahenta", None))

        comp_metoda_kasowa = fr["metoda_kasowa"] if "metoda_kasowa" in fr else None
        comp_wysylac_do_ksef = fr["wysylac_do_ksef"] if "wysylac_do_ksef" in fr else None
        comp_wysylac_do_kontrahenta = fr["wysylac_do_kontrahenta"] if "wysylac_do_kontrahenta" in fr else None

        def _first(*vals, default=False):
            for v in vals:
                b = _to_bool(v)
                if b is not None:
                    return b
            return bool(default)

        resolved_metoda = _first(inv_metoda_kasowa, comp_metoda_kasowa, default=default_metoda_kasowa)
        resolved_ksef = _first(inv_wysylac_do_ksef, comp_wysylac_do_ksef, default=default_wysylac_do_ksef)
        resolved_kontr = _first(inv_wysylac_do_kontrahenta, comp_wysylac_do_kontrahenta, default=default_wysylac_do_kontrahenta)

        mark_kasowa = "X" if resolved_metoda else ""
        mark_ksef = "X" if resolved_ksef else ""
        mark_kontr = "X" if resolved_kontr else ""

        first = True

        for poz in pozycje:
            nazwa_towaru = poz.get("nazwa", poz.get("opis", "Usługa"))
            ilosc = poz.get("ilosc", poz.get("quantity", 1))
            jednostka = poz.get("jednostka", poz.get("unit", "szt."))

            raw_netto = poz.get("netto", poz.get("cena_netto", None))
            raw_unit_netto = poz.get("cena_netto")
            raw_brutto = poz.get("brutto", poz.get("cena_brutto", None))
            vat_raw = poz.get("vat", poz.get("stawka_vat", ""))

            vat_label, vat_pct = _parse_vat(vat_raw, default_vat_str)

            if price_mode == "netto":
                # Invoice JSON stores ``netto`` as the line total.  Saldeo
                # expects the unit value, while manual positions may only
                # provide a unit ``cena_netto``.
                if raw_unit_netto is not None:
                    netto = float(raw_unit_netto or 0)
                else:
                    netto = float(raw_netto or 0) / max(float(ilosc or 1), 1)
                brutto = netto * (1 + vat_pct / 100)
            else:
                brutto = float(raw_brutto or 0)
                brutto = brutto / max(float(ilosc or 1), 1)
                netto = brutto / (1 + vat_pct / 100)

            # Saldeo: wszystkie pozycje faktury muszą być podane od netto albo od brutto
            cena_netto_fmt = format_amount_pl(netto) if price_mode == "netto" else ""
            cena_brutto_fmt = format_amount_pl(brutto) if price_mode == "brutto" else ""
            vat = vat_label

            forma_platnosci = _get_field(fv, "forma_platnosci", "przelew")

            gtu = poz.get("gtu") or _get_field(fv, "gtu", "") or default_gtu_str
            if first:
                row_out = [
                    lp_counter,
                    _get_field(fv, "sufiks", ""),
                    _get_field(fv, "opis_faktury", _get_field(fv, "opis", "")),
                    mark_kasowa,
                    _get_field(fv, "vat_marza", ""),
                    data_wyst,
                    _get_field(fv, "data_sprzedazy", data_wyst),
                    termin,
                    mark_ksef,
                    mark_kontr,
                    nazwa_skrocona,
                    nazwa_pelna,
                    adres,
                    kod,
                    miasto,
                    nip,
                    regon,
                    kraj,
                    email,
                    odb_skrocona,
                    odb_pelna,
                    (_get_field(fv, "odbiorca_rola", "") or "Odbiorca") if has_odbiorca else "",
                    _get_field(fv, "id_wew", ""),
                    _get_field(fv, "odbiorca_adres", ""),
                    _get_field(fv, "odbiorca_kod", ""),
                    _get_field(fv, "odbiorca_miasto", ""),
                    _get_field(fv, "odbiorca_nip", ""),
                    _get_field(fv, "odbiorca_regon", ""),
                    (_get_field(fv, "odbiorca_kraj", "") or "PL") if has_odbiorca else "",
                    _get_field(fv, "odbiorca_email", ""),
                    _get_field(fv, "waluta", "") or "PLN",
                    nazwa_towaru,
                    poz.get("pkwiu", ""),
                    str(ilosc),
                    jednostka,
                    cena_netto_fmt,
                    cena_brutto_fmt,
                    vat,
                    poz.get("podstawa_zw", "") if vat_label.lower().startswith("zw") else "",
                    forma_platnosci,
                    _fmt_paid(_get_field(fv, "zaplacono", "")),
                    _get_field(fv, "data_wplaty", ""),
                    konto_bankowe,
                    poz.get("transport", ""),
                    poz.get("data_dopuszczenia", ""),
                    poz.get("przebieg", ""),
                    poz.get("uwagi", "") or _get_field(fv, "uwagi", ""),
                    wystawca,
                    gtu,
                    poz.get("zamowienie_nr", ""),
                    poz.get("zamowienie_data", ""),
                    poz.get("umowa_nr", ""),
                    poz.get("umowa_data", ""),
                    "",  # Kategoria Saldeo: pozostaw puste pole
                    _get_field(fv, "opis_zdarzenia", "") or (poz.get("opis", "") if poz.get("opis", "") != nazwa_towaru else ""),
                    poz.get("rejestr", "") or _get_field(fv, "rejestr", ""),
                ]
            else:
                row_out = [""] * len(SALDEO_HEADERS)
                row_out[10] = nazwa_skrocona
                row_out[30] = _get_field(fv, "waluta", "") or "PLN"
                row_out[31] = nazwa_towaru
                row_out[32] = poz.get("pkwiu", "")
                row_out[33] = str(ilosc)
                row_out[34] = jednostka
                row_out[35] = cena_netto_fmt
                row_out[36] = cena_brutto_fmt
                row_out[37] = vat
                row_out[39] = forma_platnosci
                row_out[47] = wystawca

            for col, val in enumerate(row_out):
                if isinstance(val, str):
                    # write_string, bo write() zamienia tekst zaczynający się od "=" na formułę
                    worksheet.write_string(row_idx, col, safe_cell(val), fmt)
                else:
                    worksheet.write(row_idx, col, val, fmt)

            row_idx += 1
            first = False

        lp_counter += 1

    try:
        conn.close()
    except Exception:
        logger.warning("Zignorowany błąd", exc_info=True)

    workbook.close()


def apply_default_main_pricing(cur, firma_id: int, ilosc_dok: int) -> None:
    """
    Pomocnicza funkcja wyboru cennika głównego (jeśli chcesz automatycznie ustawiać).
    Została dołączona jako helper — możesz ją usunąć lub zastąpić własną implementacją.
    """
    try:
        cur.execute("""
            SELECT glowny_cennik, limit_dokumentow, forma_opodatkowania,
                   COALESCE(vat_czynny, 0) AS vat_czynny, COALESCE(glowny_cennik_manualny, 0) AS manual
            FROM firmy WHERE id = %s
        """, (firma_id,))
    except Exception:
        return

    row = cur.fetchone()
    if not row:
        return

    try:
        # próbujemy odczytać jako dict (Row)
        glowny_cennik = row["glowny_cennik"]
        limit_dokumentow = row["limit_dokumentow"]
        forma_opodatkowania = row["forma_opodatkowania"]
        vat_czynny = row["vat_czynny"]
        manual = row["glowny_cennik_manualny"]
    except Exception:
        # fallback dla tuple
        try:
            glowny_cennik, limit_dokumentow, forma_opodatkowania, vat_czynny, manual = row[0], row[1], row[2], row[3], row[4]
        except Exception:
            return

    if manual:
        return

    limit = limit_dokumentow or ilosc_dok or 0
    if limit <= 0:
        return

    typ = "Ryczałt"
    if forma_opodatkowania:
        txt = str(forma_opodatkowania).lower()
        if "kpir" in txt:
            typ = "KPIR"
        elif "kh" in txt or "pełna" in txt:
            typ = "KH"

    vat_suffix = "T" if bool(vat_czynny) else "N"
    pattern = f"{typ}_{vat_suffix}_%"

    cur.execute("SELECT klucz FROM cenniki WHERE klucz ILIKE %s ORDER BY klucz", (pattern,))
    keys = [r[0] for r in cur.fetchall()] if cur.description else []
    if not keys:
        return

    best_key = None
    best_limit = None
    for k in keys:
        parts = re.split(r"[_\-]", k)
        lim = None
        for p in reversed(parts):
            if p.isdigit():
                lim = int(p)
                break
        if lim is None:
            continue
        if lim <= limit and (best_limit is None or lim > best_limit):
            best_limit = lim
            best_key = k

    if best_key is None:
        for k in keys:
            parts = re.split(r"[_\-]", k)
            for p in reversed(parts):
                if p.isdigit():
                    best_key = k
                    break
            if best_key:
                break

    if best_key:
        cur.execute("UPDATE firmy SET glowny_cennik = %s, limit_dokumentow = %s WHERE id = %s", (best_key, limit, firma_id))
