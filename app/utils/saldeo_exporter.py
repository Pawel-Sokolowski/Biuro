import csv
import sqlite3
from typing import Optional, Dict, Any, List, Tuple

from app.db.dao_companies import get_conn


def _parse_limit_from_key(key: str) -> Optional[int]:
    # np. Ryczałt_N_20 -> 20
    try:
        parts = key.split("_")
        val = int(parts[-1])
        return val
    except Exception:
        return None


def wybierz_cennik(cur: sqlite3.Cursor, typ_rozliczenia: str, vat_czynny: bool, limit_dokumentow: int) -> Optional[str]:
    """
    Wybiera najlepszy klucz cennika na podstawie typu rozliczenia, VAT i limitu dokumentów.
    Typy: 'Ryczałt', 'KPIR', 'KH'
    VAT: True -> sufiks T, False -> N
    """
    base_map = {
        "Ryczałt": "Ryczałt",
        "KPIR": "KPIR",
        "KH": "KH"
    }
    base = base_map.get(typ_rozliczenia, "Ryczałt")
    vat_suffix = "T" if vat_czynny else "N"
    pattern = f"{base}_{vat_suffix}_%"

    cur.execute("SELECT klucz FROM cenniki WHERE klucz LIKE ? ORDER BY klucz", (pattern,))
    keys = [row[0] for row in cur.fetchall()]
    if not keys:
        return None

    best_key = None
    best_limit = None
    for k in keys:
        lim = _parse_limit_from_key(k)
        if lim is None:
            continue
        if lim <= limit_dokumentow and (best_limit is None or lim > best_limit):
            best_limit = lim
            best_key = k

    if best_key is None:
        # jeśli nie ma <= limitu, weź najmniejszy dostępny
        for k in keys:
            lim = _parse_limit_from_key(k)
            if lim is not None:
                best_key = k
                break

    return best_key


def pobierz_cene_za_dokument(cur: sqlite3.Cursor, klucz: str) -> Optional[float]:
    cur.execute("SELECT cena_za_dokument FROM cenniki WHERE klucz = ?", (klucz,))
    row = cur.fetchone()
    if not row:
        return None
    return row[0]


def apply_default_main_pricing(cur: sqlite3.Cursor, firma_id: int, ilosc_dok: int) -> None:
    """
    Ustawia główny cennik firmy na podstawie globalnego cennika i liczby dokumentów.
    Uwzględnia typ rozliczenia, VAT oraz flagę ręcznego nadpisania.
    """
    try:
        cur.execute("""
            SELECT glowny_cennik, limit_dokumentow, forma_opodatkowania,
                   COALESCE(vat_czynny, 0), COALESCE(glowny_cennik_manualny, 0)
            FROM firmy WHERE id = ?
        """, (firma_id,))
    except sqlite3.OperationalError:
        # starsza baza bez dodatkowych kolumn
        return

    row = cur.fetchone()
    if not row:
        return

    glowny_cennik, limit_dokumentow, forma_opodatkowania, vat_czynny, manual = row
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

    klucz = wybierz_cennik(cur, typ, bool(vat_czynny), limit)
    if not klucz:
        return

    cur.execute("UPDATE firmy SET glowny_cennik = ?, limit_dokumentow = ? WHERE id = ?", (klucz, limit, firma_id))


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


def export_saldeo_csv(db_path: Optional[str], output_path: str, miesiac: str) -> None:
    """
    Eksportuje dokumenty do pliku CSV zgodnego z SaldeoSMART.
    miesiac w formacie 'YYYY-MM' (np. '2026-08').
    Jedna faktura może mieć wiele pozycji — LP tylko w pierwszym wierszu.
    """
    conn = get_conn(db_path)
    cur = conn.cursor()

    cur.execute("""
        SELECT d.firma_id, d.numer, d.typ, d.cena_netto, d.data_wyst,
               f.nazwa, f.nip, f.miasto
        FROM dokumenty d
        JOIN firmy f ON d.firma_id = f.id
        WHERE strftime('%Y-%m', d.data_wyst) = ?
        ORDER BY f.nazwa, d.numer, d.id
    """, (miesiac,))
    rows = cur.fetchall()
    conn.close()

    # grupowanie po numerze faktury
    faktury: Dict[str, List[Tuple]] = {}
    for r in rows:
        nr = r[1]
        faktury.setdefault(nr, []).append(r)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(SALDEO_HEADERS)

        lp_counter = 1
        for nr_fv, pozycje in faktury.items():
            first = True
            for r in pozycje:
                firma_id, numer, typ, cena_netto, data_wyst, nazwa_firmy, nip, miasto = r

                lp_val = lp_counter if first else ""
                sufiks = ""  # np. seria numeracji
                opis_faktury = typ
                metoda_kasowa = ""
                vat_marza = ""
                data_wystawienia = data_wyst
                data_dostawy = data_wyst
                termin_platnosci = data_wyst
                wyslij_ksef = "X"
                wyslij_kontrahent = ""
                nabywca_skrocona = nazwa_firmy
                nazwa_pelna = nazwa_firmy
                adres = ""
                kod_pocztowy = ""
                miejscowosc = miasto or ""
                nip_val = nip or ""
                regon = ""
                kraj = "PL"
                email = ""
                odbiorca_skrocona = nazwa_firmy
                odbiorca_pelna = nazwa_firmy
                rola_odbiorcy = "Odbiorca"
                id_wew = ""
                adres_odb = ""
                kod_odb = ""
                miasto_odb = ""
                nip_odb = ""
                regon_odb = ""
                kraj_odb = "PL"
                email_odb = ""
                waluta = "PLN"
                nazwa_towaru = typ
                pkwiu = ""
                ilosc = "1"
                jednostka = "usługa"
                cena_netto_jedn = f"{cena_netto:.2f}"
                cena_brutto_jedn = ""
                stawka_vat = "23%"
                podstawa_zw = ""
                forma_platnosci = "przelew"
                zaplacono = ""
                data_wplaty = ""
                konto_bankowe = ""
                rodzaj_transportu = ""
                data_dopuszczenia = ""
                rbh_przebieg = ""
                uwagi = ""
                wystawca = ""
                grupa_towarowa = ""
                numer_zamowienia = ""
                data_zamowienia = ""
                numer_umowy = ""
                data_umowy = ""
                kategoria = ""
                opis_zdarzenia = ""
                rejestr = ""

                row_out = [
                    lp_val, sufiks, opis_faktury, metoda_kasowa, vat_marza,
                    data_wystawienia, data_dostawy, termin_platnosci, wyslij_ksef,
                    wyslij_kontrahent, nabywca_skrocona, nazwa_pelna, adres,
                    kod_pocztowy, miejscowosc, nip_val, regon, kraj, email,
                    odbiorca_skrocona, odbiorca_pelna, rola_odbiorcy, id_wew,
                    adres_odb, kod_odb, miasto_odb, nip_odb, regon_odb,
                    kraj_odb, email_odb, waluta, nazwa_towaru, pkwiu, ilosc,
                    jednostka, cena_netto_jedn, cena_brutto_jedn, stawka_vat,
                    podstawa_zw, forma_platnosci, zaplacono, data_wplaty,
                    konto_bankowe, rodzaj_transportu, data_dopuszczenia,
                    rbh_przebieg, uwagi, wystawca, grupa_towarowa,
                    numer_zamowienia, data_zamowienia, numer_umowy, data_umowy,
                    kategoria, opis_zdarzenia, rejestr
                ]
                writer.writerow(row_out)
                first = False
            lp_counter += 1
