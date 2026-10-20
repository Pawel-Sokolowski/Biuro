# app/constants.py
"""
Globalne stałe używane w całej aplikacji:
- CompanyForm
- DAO (companies, monthly, invoices, notes)
- monthly_view_logic
- monthly_view_ui
- fakturowanie
- cenniki
"""

# ============================================================
#  FORMY DZIAŁALNOŚCI
# ============================================================

FORMA_DZIALALNOSCI = [
    "Osoba fizyczna (KPiR)",
    "Osoba fizyczna (Ryczałt)",
    "JDG",
    "Spółka z o.o.",
    "Spółka cywilna",
    "Spółka jawna",
    "Spółka komandytowa",
    "Spółka partnerska",
    "Spółka akcyjna",
    "Fundacja",
    "Stowarzyszenie",
    "Inna",
]

# Formy działalności, w których właścicielem jest osoba fizyczna (PESEL wymagany)
FORMY_OSOBY_FIZYCZNEJ = ("JDG", "Osoba fizyczna")

# ============================================================
#  FORMY OPODATKOWANIA
# ============================================================

FORMA_OPODATKOWANIA = [
    "Ryczałt",
    "KPiR",
    "Księgi rachunkowe",
    "Karta podatkowa",
]

# ============================================================
#  STATUS PODATKOWY

STATUS_PODATKOWY = [
    "Zwykły podmiot",
    "Członek grupy VAT",
    "Jednostka organizacyjna JST",
]

# ============================================================
#  URZĘDY SKARBOWE
# ============================================================

URZEDY_SKARBOWE = [

]

# ============================================================
#  MIESIĄCE FAKTUROWANIA
# ============================================================

MIESIACE_FAKTUROWANIE = [
    "Bieżący miesiąc",
    "Poprzedni miesiąc",
    "2 miesiące wstecz",
    "3 miesiące wstecz",
]

# ============================================================
#  SPOSÓB ZAPŁATY
# ============================================================

SPOSOB_ZAPLATY = [
    "Przelew 7 dni",
    "Przelew 10 dni",
    "Przelew 14 dni",
    "Przelew 30 dni",
    "Konkretny dzień (np. 7 następnego miesiąca)",
]

# ============================================================
#  SERIE NUMERACJI FAKTUR
# ============================================================

DEFAULT_SERIA_FV = "FV/####/@@/$$$$"
DEFAULT_SERIA_KFV = "KFV/####/@@/$$$$"

# ============================================================
#  STATUS FIRMY
# ============================================================

STATUS_FIRMY = [
    "Aktywne",
    "Zawieszone",
    "Archiwalne",
]

# ============================================================
#  DOMYŚLNE WARTOŚCI
# ============================================================

DOMYSLNY_DZIEN_DOSTARCZANIA = 7
DOMYSLNY_LIMIT_DOKUMENTOW = 0

# ============================================================
#  POLA ADRESOWE — mapowania aliasów
# ============================================================

ADRES_DZIALALNOSCI_FIELDS = [
    "dz_miasto", "dz_kod", "dz_ulica", "dz_budynek", "dz_lokal",
    "dz_kraj", "dz_wojewodztwo", "dz_powiat", "dz_gmina",
]

ADRES_KORESP_FIELDS = [
    "ko_miasto", "ko_kod", "ko_ulica", "ko_budynek", "ko_lokal",
    "ko_kraj", "ko_wojewodztwo", "ko_powiat", "ko_gmina",
]

ADRES_ZAM_FIELDS = [
    "zam_miasto", "zam_kod", "zam_ulica", "zam_budynek", "zam_lokal",
    "zam_kraj", "zam_wojewodztwo", "zam_powiat", "zam_gmina",
]

# ============================================================
#  KONTAKT
# ============================================================

KONTAKT_FIELDS = [
    "kont_imie",
    "kont_nazw",
    "kont_email",
    "kont_tel",
]

# ============================================================
#  WSPÓLNICY / REPREZENTANCI
# ============================================================

PARTNER_FIELDS = [
    "imie_pierwsze", "imie_drugie", "nazwisko", "nazwa_organizacji",
    "pesel", "nip", "regon", "krs",
    "ulica", "nr_domu", "nr_mieszkania",
    "wojewodztwo", "powiat", "gmina", "miejscowosc",
    "kod_pocztowy", "poczta", "panstwo",
    "email", "telefon", "data_urodzenia",
    "stanowisko", "procent",
]

# ============================================================
#  OSOBY KONTAKTOWE
# ============================================================

CONTACT_FIELDS = ["imie", "nazwisko", "email", "telefon", "rola"]

# ============================================================
#  POLA FIRMY — używane w DAO i CompanyForm
# ============================================================

FIRMA_FIELDS = [
    "nazwa", "nazwa_skrocona", "numer_klienta",
    "nip", "regon", "krs", "pesel",

    "miasto", "kod_pocztowy", "ulica", "adres_budynek", "adres_lokal",
    "kraj", "wojewodztwo", "powiat", "gmina",

    "forma_dzialalnosci", "forma_opodatkowania", "analityka_fk",
    "osoba_kontaktowa", "adres_email", "nr_telefonu",

    "adres_zam_miasto", "adres_zam_kod_pocztowy", "adres_zam_ulica",
    "adres_zam_budynek", "adres_zam_lokal",
    "adres_zam_kraj", "adres_zam_wojewodztwo",
    "adres_zam_powiat", "adres_zam_gmina",

    "adres_koresp_miasto", "adres_koresp_kod_pocztowy", "adres_koresp_ulica",
    "adres_koresp_budynek", "adres_koresp_lokal",
    "adres_koresp_kraj", "adres_koresp_wojewodztwo",
    "adres_koresp_powiat", "adres_koresp_gmina",

    "data_rozpoczecia_dg", "data_rozpoczecia_sprzedazy",
    "data_koniec_zus_start", "data_koniec_zus_pref",
    "data_koniec_maly_zus", "data_koniec_a1",
    "data_rozpoczecia_obslugi", "data_zakonczenia_obslugi",

    "zawieszenia_dzialalnosci", "kody_pkd",

    "cennik_ksiegowy", "cennik_kadrowy", "cennik_czasowy",
    "glowny_cennik", "glowny_cennik_manualny",

    "limit_dokumentow",
    "liczba_pracownikow",

    "uop_ilosc", "uz_ilosc", "uod_ilosc",
    "uop_cennik", "uz_cennik", "uod_cennik",

    "typ_rozliczenia", "vat_czynny", "status", "uslugi_zawieszone",

    "cennik_kpir_auto", "cena_ksiegowosc_auto",
    "cennik_kpir_manual", "manual_cennik_override",

    "zdrowotna", "cena_netto_ksiegowosc", "cena_ponad_limit",

    "wysylac_do_ksef",
    "wysylac_do_kontrahenta",
    "metoda_kasowa",
    "fakturowac",

    "adres_doreczen_elektronicznych",
    "urzad_skarbowy",

    "miesiac_dok_fakturowanie",
    "miesiac_prac_fakturowanie",
    "dzien_dostarczania_dokumentow",

    "seria_fv",
    "seria_kfv",

    "sposob_zaplaty",

    "status_podatkowy",

    "konto_bankowe",
    "uwagi",
    "kategoria_domyslna",
]
