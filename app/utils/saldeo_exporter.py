# app/utils/saldeo_exporter.py
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

import xlsxwriter

from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting_bool


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


# -------------------------
# Główna funkcja eksportu (XLSX)
# -------------------------
def export_saldeo_xlsx(
    invoices: List[Dict[str, Any]],
    output_path: str,
    metoda_kasowa: Optional[bool] = None,
    wysylac_do_ksef: Optional[bool] = None,
    wysylac_do_kontrahenta: Optional[bool] = None,
) -> None:

    # jeśli parametry None -> pobierz wartości domyślne z ustawień
    default_metoda_kasowa = _get_export_bool("eksport_kasowa", "faktury_metoda_kasowa", "domyslnie_metoda_kasowa")
    default_wysylac_do_ksef = _get_export_bool("eksport_do_ksef", "faktury_ksef", "domyslnie_wysylac_do_ksef")
    default_wysylac_do_kontrahenta = _get_export_bool(
        "eksport_do_kontrahenta",
        "faktury_wysylka_kontrahent",
        "domyslnie_wysylac_do_kontrahenta",
    )

    # helper: bezpieczne rzutowanie na bool (obsługa "1","true","True","X", 1, True)
    def _to_bool(v: Any) -> Optional[bool]:
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        try:
            s = str(v).strip()
        except Exception:
            return None
        if s == "":
            return None
        if s in ("1", "True", "true", "X", "x", "yes", "tak", "Tak"):
            return True
        if s in ("0", "False", "false", "No", "no", "Nie", "nie"):
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

    # nagłówki
    for col, header in enumerate(SALDEO_HEADERS):
        worksheet.write(0, col, header, fmt_bold)

    conn = _get_conn()
    cur = conn.cursor()

    row_idx = 1
    lp_counter = 1

    for fv in invoices:
        # pobierz firma_id z faktury (najpierw 'firma_id', potem 'id' jako fallback)
        firma_id = _get_field(fv, "firma_id", _get_field(fv, "id", None))
        # data wystawienia
        miesiac = _get_field(fv, "miesiac", _get_field(fv, "data", ""))
        data_wyst = (
            f"{miesiac}-01"
            if miesiac and re.match(r"^\d{4}-\d{2}$", str(miesiac))
            else _get_field(fv, "data_wystawienia", _get_field(fv, "data", ""))
        )

        # pobierz dane firmy z DB (defensywnie)
        fr = {}
        if firma_id is not None:
            try:
                cur.execute(
                    """
                    SELECT
                        nazwa, nazwa_skrocona, nip, regon,
                        miasto, kod_pocztowy, ulica,
                        adres_budynek, adres_lokal, adres_email,
                        konto_bankowe, uwagi, kategoria_domyslna,
                        forma_opodatkowania, vat_czynny, glowny_cennik, limit_dokumentow,
                        wysylac_do_ksef, wysylac_do_kontrahenta, metoda_kasowa
                    FROM firmy WHERE id = ?
                    """,
                    (firma_id,),
                )
                row = cur.fetchone()
                if row:
                    try:
                        fr = dict(row)
                    except Exception:
                        # fallback: zbuduj dict z cur.description
                        fr = {col[0]: row[idx] for idx, col in enumerate(cur.description)} if row else {}
            except Exception:
                fr = {}

        # jeśli brak danych firmy w DB, użyj pól z faktury
        nazwa_pelna = fr.get("nazwa") or _get_field(fv, "firma_nazwa", _get_field(fv, "nazwa_firmy", ""))
        nazwa_skrocona = fr.get("nazwa_skrocona") or nazwa_pelna
        nip = fr.get("nip") or _get_field(fv, "firma_nip", _get_field(fv, "nabywca_nip", ""))
        regon = fr.get("regon") or ""
        miasto = fr.get("miasto") or _get_field(fv, "firma_miasto", "")
        kod = fr.get("kod_pocztowy") or _get_field(fv, "firma_kod", "")
        ulica = fr.get("ulica") or _get_field(fv, "firma_ulica", "")
        budynek = fr.get("adres_budynek") or ""
        lokal = fr.get("adres_lokal") or ""
        email = fr.get("adres_email") or _get_field(fv, "firma_email", "")
        konto_bankowe = fr.get("konto_bankowe") or _get_field(fv, "konto_bankowe", "")
        uwagi_firma = fr.get("uwagi") or ""
        kategoria_domyslna = fr.get("kategoria_domyslna") or ""
        forma_opodatkowania = fr.get("forma_opodatkowania") or ""
        vat_czynny = bool(fr.get("vat_czynny"))
        glowny_cennik = fr.get("glowny_cennik")
        limit_dokumentow = int(fr.get("limit_dokumentow") or 0)

        adres = " ".join(p for p in [ulica, budynek, f"/{lokal}" if lokal else ""] if p).replace(" /", "/").strip()

        # pozycje faktury: obsługa 'pozycje' (lista) lub 'pozycje_json' (string)
        pozycje_json = _get_field(fv, "pozycje_json", _get_field(fv, "pozycje", "[]"))
        try:
            pozycje = json.loads(pozycje_json) if isinstance(pozycje_json, str) else pozycje_json
            if pozycje is None:
                pozycje = []
        except Exception:
            pozycje = []

        # jeśli brak cennika głównego, opcjonalnie ustaw automatycznie (jeśli masz taką funkcję)
        # tutaj tylko defensywnie próbujemy wywołać, jeśli funkcja jest dostępna
        try:
            if not glowny_cennik:
                # apply_default_main_pricing może być zaimplementowane w tym module lub innym
                from app.utils.saldeo_exporter import apply_default_main_pricing  # noqa: F401
                apply_default_main_pricing(cur, firma_id, len(pozycje))
                cur.execute("SELECT glowny_cennik, limit_dokumentow FROM firmy WHERE id = ?", (firma_id,))
                row_after = cur.fetchone()
                if row_after:
                    try:
                        row_after = dict(row_after)
                        glowny_cennik = row_after.get("glowny_cennik") or glowny_cennik
                        limit_dokumentow = int(row_after.get("limit_dokumentow") or limit_dokumentow)
                    except Exception:
                        pass
        except Exception:
            pass

        # numeracja Lp.: tylko w pierwszym wierszu faktury
        first = True

        # --- per-invoice export flags (priorytet: faktura -> firma -> ustawienia domyślne) ---
        inv_metoda_kasowa = _get_field(fv, "metoda_kasowa", None)
        inv_wysylac_do_ksef = _get_field(fv, "wysylac_do_ksef", None)
        inv_wysylac_do_kontrahenta = _get_field(fv, "wysylac_do_kontrahenta", None)

        comp_metoda_kasowa = fr.get("metoda_kasowa") if isinstance(fr, dict) else None
        comp_wysylac_do_ksef = fr.get("wysylac_do_ksef") if isinstance(fr, dict) else None
        comp_wysylac_do_kontrahenta = fr.get("wysylac_do_kontrahenta") if isinstance(fr, dict) else None

        resolved_metoda = _to_bool(inv_metoda_kasowa)
        if resolved_metoda is None:
            resolved_metoda = _to_bool(comp_metoda_kasowa)
        if resolved_metoda is None:
            resolved_metoda = bool(default_metoda_kasowa)

        resolved_ksef = _to_bool(inv_wysylac_do_ksef)
        if resolved_ksef is None:
            resolved_ksef = _to_bool(comp_wysylac_do_ksef)
        if resolved_ksef is None:
            resolved_ksef = bool(default_wysylac_do_ksef)

        resolved_kontr = _to_bool(inv_wysylac_do_kontrahenta)
        if resolved_kontr is None:
            resolved_kontr = _to_bool(comp_wysylac_do_kontrahenta)
        if resolved_kontr is None:
            resolved_kontr = bool(default_wysylac_do_kontrahenta)

        mark_kasowa = "X" if resolved_metoda else ""
        mark_ksef = "X" if resolved_ksef else ""
        mark_kontr = "X" if resolved_kontr else ""

        for poz in pozycje:
            # defensywne pola pozycji
            nazwa_towaru = poz.get("nazwa", poz.get("opis", "Usługa"))
            ilosc = poz.get("ilosc", poz.get("quantity", 1))
            jednostka = poz.get("jednostka", poz.get("unit", "szt."))
            netto = float(poz.get("netto", poz.get("cena_netto", 0)) or 0)
            brutto = float(poz.get("brutto", poz.get("cena_brutto", 0)) or 0)
            if not brutto and netto:
                vat_pct = float(str(poz.get("vat", poz.get("stawka_vat", 23))).replace("%", "") or 23)
                brutto = netto * (1 + vat_pct / 100)
            vat = str(poz.get("vat", poz.get("stawka_vat", "")))
            pkwiu = poz.get("pkwiu", "")
            gtu = poz.get("gtu", "")
            kategoria = poz.get("kategoria", kategoria_domyslna)
            opis_poz = poz.get("opis", "")
            rejestr = poz.get("rejestr", "")
            zam_nr = poz.get("zamowienie_nr", "")
            zam_data = poz.get("zamowienie_data", "")
            um_nr = poz.get("umowa_nr", "")
            um_data = poz.get("umowa_data", "")
            transport = poz.get("transport", "")
            data_dopuszczenia = poz.get("data_dopuszczenia", "")
            przebieg = poz.get("przebieg", "")
            uwagi_poz = poz.get("uwagi", "")

            cena_netto_fmt = format_amount_pl(float(netto or 0))
            cena_brutto_fmt = format_amount_pl(float(brutto or 0))
            lp_val = lp_counter if first else ""

            forma_platnosci = _get_field(fv, "forma_platnosci", "przelew")
            zaplacono = "Tak" if (_get_field(fv, "zapłacono", _get_field(fv, "zaplacono", False))) else ""
            data_wplaty = _get_field(fv, "data_wplaty", "")

            # budujemy wiersz zgodny z SALDEO_HEADERS
            row_out = [
                lp_val,  # Lp.
                _get_field(fv, "sufiks", ""),  # Sufiks
                _get_field(fv, "opis_faktury", _get_field(fv, "opis", "")),  # Własny opis faktury
                mark_kasowa,  # Metoda kasowa
                _get_field(fv, "vat_marza", ""),  # VAT marża
                data_wyst,  # Data wystawienia
                _get_field(fv, "data_sprzedazy", data_wyst),  # Data dostawy
                _get_field(fv, "termin_platnosci", ""),  # Termin płatności
                mark_ksef,  # Wyślij do KSeF
                mark_kontr,  # Wyślij do kontrahenta
                nazwa_skrocona,  # Nabywca (nazwa skrócona kontrahenta)
                nazwa_pelna,  # Nazwa pełna kontrahenta
                adres,  # Adres
                kod,  # Kod pocztowy
                miasto,  # Miejscowość
                nip,  # NIP
                regon,  # REGON
                "PL",  # Kraj
                email,  # Adres e-mail
                _get_field(fv, "odbiorca_nazwa_skrocona", nazwa_skrocona),  # Odbiorca skrócona
                _get_field(fv, "odbiorca_nazwa_pelna", nazwa_pelna),  # Nazwa pełna odbiorcy
                _get_field(fv, "odbiorca_rola", "Odbiorca"),  # Rola odbiorcy
                _get_field(fv, "id_wew", ""),  # ID WEW
                _get_field(fv, "odbiorca_adres", adres),  # Adres odbiorcy
                _get_field(fv, "odbiorca_kod", kod),  # Kod pocztowy odbiorcy
                _get_field(fv, "odbiorca_miasto", miasto),  # Miejscowość odbiorcy
                _get_field(fv, "odbiorca_nip", nip),  # NIP odbiorcy
                _get_field(fv, "odbiorca_regon", regon),  # REGON odbiorcy
                _get_field(fv, "odbiorca_kraj", "PL"),  # Kraj odbiorcy
                _get_field(fv, "odbiorca_email", email),  # Email odbiorcy
                _get_field(fv, "waluta", "PLN"),  # Waluta
                nazwa_towaru,  # Nazwa towaru
                pkwiu,  # PKWiU
                str(ilosc),  # Ilość
                jednostka,  # Jednostka
                cena_netto_fmt,  # Cena jedn. netto
                cena_brutto_fmt,  # Cena jedn. brutto
                f"{vat}%" if vat and "%" not in str(vat) else str(vat),  # Stawka VAT
                _get_field(fv, "podstawa_zw", ""),  # Podstawa ZW
                forma_platnosci,  # Forma płatności
                zaplacono,  # Zapłacono
                data_wplaty,  # Data wpłaty
                konto_bankowe,  # Konto bankowe
                transport,  # Rodzaj środka transportu
                data_dopuszczenia,  # Data dopuszczenia
                przebieg,  # Liczba rbh / Przebieg pojazdu
                uwagi_poz or uwagi_firma,  # Uwagi
                _get_field(fv, "wystawca", nazwa_pelna),  # Wystawca faktury
                _get_field(fv, "grupa_towarowa", ""),  # Grupa Towarowa
                zam_nr,  # Numer zamówienia
                zam_data,  # Data zamówienia
                um_nr,  # Numer umowy
                um_data,  # Data umowy
                kategoria,  # Kategoria
                opis_poz,  # Opis
                rejestr,  # Rejestr
            ]

            # zapisz wiersz
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
