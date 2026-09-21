import csv
import sqlite3
from typing import Optional, Dict, Any, List, Tuple

from app.db.dao_companies import get_conn


# ============================================================
# Pomocnicze funkcje cenników (Twoja oryginalna logika)
# ============================================================

def _parse_limit_from_key(key: str) -> Optional[int]:
    try:
        parts = key.split("_")
        val = int(parts[-1])
        return val
    except Exception:
        return None


def wybierz_cennik(cur: sqlite3.Cursor, typ_rozliczenia: str, vat_czynny: bool, limit_dokumentow: int) -> Optional[str]:
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
    try:
        cur.execute("""
            SELECT glowny_cennik, limit_dokumentow, forma_opodatkowania,
                   COALESCE(vat_czynny, 0), COALESCE(glowny_cennik_manualny, 0)
            FROM firmy WHERE id = ?
        """, (firma_id,))
    except sqlite3.OperationalError:
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


# ============================================================
# Formatowanie kwot zgodnie z SaldeoSMART (A1)
# ============================================================

def format_amount_pl(amount: float) -> str:
    """
    Formatowanie kwot zgodnie z polskim standardem:
    - separator tysięcy: kropka
    - separator dziesiętny: przecinek
    - zawsze 2 miejsca po przecinku
    """
    return f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ============================================================
# Nagłówki SaldeoSMART (pełne, 60+ kolumn)
# ============================================================

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


# ============================================================
# PROFESJONALNY EKSPORT SALDEO SMART
# ============================================================
def export_saldeo_csv(output_path: str, miesiac: str) -> None:
    conn = get_conn()
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

    faktury: Dict[str, List[sqlite3.Row]] = {}
    for r in rows:
        nr = r["numer"]
        faktury.setdefault(nr, []).append(r)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(SALDEO_HEADERS)

        lp_counter = 1

        for nr_fv, pozycje in faktury.items():
            first = True

            for r in pozycje:
                firma_id = r["firma_id"]
                numer = r["numer"]
                typ = r["typ"]
                cena_netto = float(r["cena_netto"])
                data_wyst = r["data_wyst"]
                nazwa_firmy = r["nazwa"]
                nip = r["nip"]
                miasto = r["miasto"]

                lp_val = lp_counter if first else ""
                cena_netto_fmt = format_amount_pl(cena_netto)

                row_out = [
                    lp_val, "", typ, "", "",
                    data_wyst, data_wyst, data_wyst, "X",
                    "", nazwa_firmy, nazwa_firmy, "",
                    "", miasto or "", nip or "", "",
                    "PL", "", nazwa_firmy, nazwa_firmy,
                    "Odbiorca", "", "", "", "", "", "",
                    "PL", "", "PLN", typ, "", "1",
                    "usługa", cena_netto_fmt, "", "23%",
                    "", "przelew", "", "", "", "",
                    "", "", "", "", "", "",
                    "", "", "", "", "", "", ""
                ]

                writer.writerow(row_out)
                first = False

            lp_counter += 1

