# app/utils/saldeo_exporter.py
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

import xlsxwriter

from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting_bool, get_setting_first

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


# -------------------------
# Helpery
# -------------------------
def _get_conn():
    """Zwraca połączenie z DB (deleguje do dao_conn.get_conn)."""
    return get_conn()


def _get_field(obj: Any, key: str, default: Any = "") -> Any:
    """Defensywny getter: działa z dict i sqlite3.Row."""
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

def export_saldeo_xlsx(
    invoices: List[Dict[str, Any]],
    output_path: str,
    metoda_kasowa: Optional[bool] = None,
    wysylac_do_ksef: Optional[bool] = None,
    wysylac_do_kontrahenta: Optional[bool] = None,
) -> None:

    default_metoda_kasowa = _get_export_bool("eksport_kasowa", "faktury_metoda_kasowa", "domyslnie_metoda_kasowa")
    default_wysylac_do_ksef = _get_export_bool("eksport_do_ksef", "faktury_ksef", "domyslnie_wysylac_do_ksef")
    default_wysylac_do_kontrahenta = _get_export_bool(
        "eksport_do_kontrahenta",
        "faktury_wysylka_kontrahent",
        "domyslnie_wysylac_do_kontrahenta",
    )

    default_vat_str = get_setting_first(("domyslna_stawka_vat", "default_vat"), "23")
    default_gtu_str = get_setting_first(("domyslne_gtu", "default_gtu"), "")

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
        data_wyst = (
            f"{miesiac}-01"
            if miesiac and re.match(r"^\d{4}-\d{2}$", str(miesiac))
            else _get_field(fv, "data_wystawienia", _get_field(fv, "data", ""))
        )

        fr = {}
        if firma_id is not None:
            try:
                cur.execute("""
                    SELECT
                        nazwa, nazwa_skrocona, nip, regon,
                        miasto, kod_pocztowy, ulica,
                        adres_budynek, adres_lokal, adres_email,
                        konto_bankowe, uwagi, kategoria_domyslna,
                        forma_opodatkowania, vat_czynny, glowny_cennik, limit_dokumentow,
                        wysylac_do_ksef, wysylac_do_kontrahenta, metoda_kasowa
                    FROM firmy WHERE id = ?
                """, (firma_id,))
                row = cur.fetchone()
                if row:
                    fr = dict(row)
            except Exception:
                fr = {}

        # zamiana fr.get → fr["pole"]
        nazwa_pelna = fr["nazwa"] if "nazwa" in fr else _get_field(fv, "firma_nazwa", _get_field(fv, "nazwa_firmy", ""))
        nazwa_skrocona = fr["nazwa_skrocona"] if "nazwa_skrocona" in fr else nazwa_pelna
        nip = fr["nip"] if "nip" in fr else _get_field(fv, "firma_nip", _get_field(fv, "nabywca_nip", ""))
        regon = fr["regon"] if "regon" in fr else ""
        miasto = fr["miasto"] if "miasto" in fr else _get_field(fv, "firma_miasto", "")
        kod = fr["kod_pocztowy"] if "kod_pocztowy" in fr else _get_field(fv, "firma_kod", "")
        ulica = fr["ulica"] if "ulica" in fr else _get_field(fv, "firma_ulica", "")
        budynek = fr["adres_budynek"] if "adres_budynek" in fr else ""
        lokal = fr["adres_lokal"] if "adres_lokal" in fr else ""
        email = fr["adres_email"] if "adres_email" in fr else _get_field(fv, "firma_email", "")
        konto_bankowe = fr["konto_bankowe"] if "konto_bankowe" in fr else _get_field(fv, "konto_bankowe", "")
        uwagi_firma = fr["uwagi"] if "uwagi" in fr else ""
        kategoria_domyslna = fr["kategoria_domyslna"] if "kategoria_domyslna" in fr else ""

        adres = " ".join(p for p in [ulica, budynek, f"/{lokal}" if lokal else ""] if p).replace(" /", "/").strip()

        pozycje_json = _get_field(fv, "pozycje_json", _get_field(fv, "pozycje", "[]"))
        try:
            pozycje = json.loads(pozycje_json) if isinstance(pozycje_json, str) else pozycje_json
            if pozycje is None:
                pozycje = []
        except Exception:
            pozycje = []

        price_mode = "netto"
        if pozycje and pozycje[0].get("brutto") and not pozycje[0].get("netto"):
            price_mode = "brutto"

        inv_metoda_kasowa = _get_field(fv, "metoda_kasowa", None)
        inv_wysylac_do_ksef = _get_field(fv, "wysylac_do_ksef", None)
        inv_wysylac_do_kontrahenta = _get_field(fv, "wysylac_do_kontrahenta", None)

        comp_metoda_kasowa = fr["metoda_kasowa"] if "metoda_kasowa" in fr else None
        comp_wysylac_do_ksef = fr["wysylac_do_ksef"] if "wysylac_do_ksef" in fr else None
        comp_wysylac_do_kontrahenta = fr["wysylac_do_kontrahenta"] if "wysylac_do_kontrahenta" in fr else None

        resolved_metoda = _to_bool(inv_metoda_kasowa) or _to_bool(comp_metoda_kasowa) or bool(default_metoda_kasowa)
        resolved_ksef = _to_bool(inv_wysylac_do_ksef) or _to_bool(comp_wysylac_do_ksef) or bool(default_wysylac_do_ksef)
        resolved_kontr = _to_bool(inv_wysylac_do_kontrahenta) or _to_bool(comp_wysylac_do_kontrahenta) or bool(default_wysylac_do_kontrahenta)

        mark_kasowa = "X" if resolved_metoda else ""
        mark_ksef = "X" if resolved_ksef else ""
        mark_kontr = "X" if resolved_kontr else ""

        first = True

        for poz in pozycje:
            nazwa_towaru = poz.get("nazwa", poz.get("opis", "Usługa"))
            ilosc = poz.get("ilosc", poz.get("quantity", 1))
            jednostka = poz.get("jednostka", poz.get("unit", "szt."))

            raw_netto = poz.get("netto", poz.get("cena_netto", None))
            raw_brutto = poz.get("brutto", poz.get("cena_brutto", None))
            vat_raw = poz.get("vat", poz.get("stawka_vat", ""))

            try:
                vat_pct = float(str(vat_raw).replace("%", "").strip()) if vat_raw not in ("", None) else None
            except Exception:
                vat_pct = None

            if vat_pct is None:
                vat_pct = float(default_vat_str)

            if price_mode == "netto":
                netto = float(raw_netto or 0)
                brutto = netto * (1 + vat_pct / 100)
            else:
                brutto = float(raw_brutto or 0)
                netto = brutto / (1 + vat_pct / 100)

            cena_netto_fmt = format_amount_pl(netto)
            cena_brutto_fmt = format_amount_pl(brutto)
            vat = f"{int(vat_pct)}%"

            forma_platnosci = _get_field(fv, "forma_platnosci", "przelew")

            if first:
                row_out = [
                    lp_counter,
                    _get_field(fv, "sufiks", ""),
                    _get_field(fv, "opis_faktury", _get_field(fv, "opis", "")),
                    mark_kasowa,
                    _get_field(fv, "vat_marza", ""),
                    data_wyst,
                    _get_field(fv, "data_sprzedazy", data_wyst),
                    _get_field(fv, "termin_platnosci", ""),
                    mark_ksef,
                    mark_kontr,
                    nazwa_skrocona,
                    nazwa_pelna,
                    adres,
                    kod,
                    miasto,
                    nip,
                    regon,
                    "PL",
                    email,
                    nazwa_skrocona,
                    nazwa_pelna,
                    _get_field(fv, "odbiorca_rola", "Odbiorca"),
                    _get_field(fv, "id_wew", ""),
                    adres,
                    kod,
                    miasto,
                    nip,
                    regon,
                    "PL",
                    email,
                    "PLN",
                    nazwa_towaru,
                    poz.get("pkwiu", ""),
                    str(ilosc),
                    jednostka,
                    cena_netto_fmt,
                    cena_brutto_fmt,
                    vat,
                    "",
                    forma_platnosci,
                    "",
                    "",
                    konto_bankowe,
                    poz.get("transport", ""),
                    poz.get("data_dopuszczenia", ""),
                    poz.get("przebieg", ""),
                    poz.get("uwagi", "") or uwagi_firma,
                    nazwa_pelna,
                    "",
                    poz.get("zamowienie_nr", ""),
                    poz.get("zamowienie_data", ""),
                    poz.get("umowa_nr", ""),
                    poz.get("umowa_data", ""),
                    poz.get("kategoria", kategoria_domyslna),
                    poz.get("opis", ""),
                    poz.get("rejestr", ""),
                ]
            else:
                row_out = [""] * 58
                row_out[10] = nazwa_skrocona
                row_out[30] = "PLN"
                row_out[31] = nazwa_towaru
                row_out[32] = poz.get("pkwiu", "")
                row_out[33] = str(ilosc)
                row_out[34] = jednostka
                row_out[35] = cena_netto_fmt
                row_out[36] = cena_brutto_fmt
                row_out[37] = vat
                row_out[39] = forma_platnosci

            for col, val in enumerate(row_out):
                worksheet.write(row_idx, col, val, fmt)

            row_idx += 1
            first = False

        lp_counter += 1

    try:
        conn.close()
    except Exception:
        pass

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
            FROM firmy WHERE id = ?
        """, (firma_id,))
    except Exception:
        return

    row = cur.fetchone()
    if not row:
        return

    try:
        # próbujemy odczytać jako dict (sqlite3.Row)
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

    cur.execute("SELECT klucz FROM cenniki WHERE klucz LIKE ? ORDER BY klucz", (pattern,))
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
        cur.execute("UPDATE firmy SET glowny_cennik = ?, limit_dokumentow = ? WHERE id = ?", (best_key, limit, firma_id))
