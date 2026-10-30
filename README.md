# Biuro – program dla biura rachunkowego

Aplikacja desktopowa (PyQt6 + PostgreSQL) do prowadzenia biura rachunkowego: klienci, faktury, e-mail, kalendarz, zadania, raporty i kontrola kopii zapasowych.

> Zrzuty ekranu pochodzą z tymczasowej bazy z danymi fikcyjnymi (`scripts\make_screenshots.py`).

## Moduły

| Moduł | Opis |
|---|---|
| **Pulpit** | Podsumowanie dnia, czas pracy, stan kopii zapasowych |
| **Widok miesięczny** | Status firm w wybranym miesiącu (wybór miesiąca i roku) |
| **Klienci** | Lista firm, formularz firmy, pełnomocnictwa (PEL/UPL), data ostatniej kopii bazy |
| **E-mail** | Wiele kont IMAP/SMTP, foldery, reguły, etykiety, przypisywanie wiadomości do firm |
| **Czat** | Wewnętrzna komunikacja pracowników |
| **Faktury** | Wystawianie z cennika, usługi dodatkowe, KSeF, eksport XLSX do Saldeo |
| **Raporty** | Import dokumentów i czasu pracy, rentowność, czas według klienta i pracownika |
| **Zadania** | Zadania z czasomierzem |
| **Kalendarz** | Wydarzenia, import ICS, synchronizacja CalDAV |
| **Ustawienia** | Panel administratora (poniżej) |

## Zrzuty ekranu

![Pulpit](docs/screenshots/pulpit.png)
![Widok miesięczny](docs/screenshots/widok-miesieczny.png)
![Klienci](docs/screenshots/klienci.png)
![Faktury](docs/screenshots/faktury.png)
![Raporty](docs/screenshots/raporty.png)
![Zadania](docs/screenshots/zadania.png)
![Kalendarz](docs/screenshots/kalendarz.png)

### Panel administratora

| | |
|---|---|
| ![Użytkownicy](docs/screenshots/admin-uzytkownicy.png) | ![Role i uprawnienia](docs/screenshots/admin-role-i-uprawnienia.png) |
| ![Konta e-mail](docs/screenshots/admin-konta-e-mail.png) | ![Konto systemowe i kalendarz](docs/screenshots/admin-konto-systemowe-i-kalendarz.png) |
| ![Cennik księgowość](docs/screenshots/admin-cennik-ksiegowosc.png) | ![Cennik kadry](docs/screenshots/admin-cennik-kadry.png) |
| ![Usługi dodatkowe](docs/screenshots/admin-uslugi-dodatkowe.png) | ![Firmy](docs/screenshots/admin-firmy.png) |
| ![Faktury i KSeF](docs/screenshots/admin-faktury-i-ksef.png) | ![KSeF – dostęp i certyfikat](docs/screenshots/admin-ksef--dostep-i-certyfikat.png) |
| ![Import danych](docs/screenshots/admin-import-danych.png) | ![Baza danych i kopie](docs/screenshots/admin-baza-danych-i-kopie.png) |
| ![System](docs/screenshots/admin-system.png) | ![Audyt i importy](docs/screenshots/admin-audyt-i-importy.png) |

## Kopie zapasowe

Program czyta logi kopii (Cobian Backup, mail „Backup 11 (ENOVA)”) z pliku albo z najnowszego maila na koncie systemowym. Nazwy plików mają postać `firma_datautworzenia` (np. `Firma_20261021.BAK`), z której pobierana jest data.

**Ustawienia → Baza danych i kopie → „Sprawdź z ostatniego maila”** pobiera najnowszy mail, zapisuje datę ostatniej kopii w firmach i pokazuje tabelę *Nazwa firmy | Data | Błąd (TAK/NIE)*. Data jest widoczna w kolumnie „Ostatni backup” na liście klientów.

## Wymagania

- Python 3.11
- PostgreSQL
- Zależności: `pip install -r requirements.txt`

## Konfiguracja

Skopiuj `.env.example` jako `.env` (katalog projektu albo `%LOCALAPPDATA%\Biuro\` dla wersji zainstalowanej). Plik `.env` nie trafia do instalatora ani do repozytorium.

| Zmienna | Opis |
|---|---|
| `DB_URL` | Adres bazy PostgreSQL (wymagane) |
| `BIO_ENCRYPTION_KEY` | Klucz szyfrowania haseł kont e-mail; przy bazie na NAS ustaw ten sam na wszystkich komputerach |
| `CEIDG_BASE_URL`, `CEIDG_API_KEY` | Wyszukiwanie firm w CEIDG |
| `GUS_API_URL`, `GUS_API_KEY` | Wyszukiwarka REGON (domyślny adres jest wbudowany) |

## Uruchomienie

```powershell
pip install -r requirements.txt
python migrate_db.py   # tworzy/aktualizuje schemat bazy
python main.py
```

`migrate_db.py` kończy się sukcesem tylko wtedy, gdy schemat jest aktualny (`SCHEMA_VERSION` w `app\db\schema.py`). Zmiana struktury bazy wymaga podbicia tej wersji.

## Rozliczenia i KSeF

W **Fakturach wystawionych** przycisk **Rozlicz ING MT940** importuje wyciąg
do kolejki uzgodnień. Wybierz przelew, fakturę i kwotę, a następnie potwierdź
przypisanie. Można podzielić przelew na kilka faktur lub rozliczyć część salda.
Import nie zapisuje płatności automatycznie. Rozliczane są tylko wpływy w PLN;
storna i inne waluty pozostają poza listą wpłat do przypisania. Data zapłaty
pochodzi z daty księgowania, a przy jej braku z daty waluty.

**Zapłata gotówką** proponuje pozostałe saldo i pozwala wskazać rzeczywistą
datę otrzymania pieniędzy. Księgowy widok miesięczny pokazuje numer faktury
i datę pełnego rozliczenia przed „Kompletem”, dla faktur dotyczących wybranego
miesiąca. Szczegóły częściowych wpłat są dostępne w podpowiedzi komórki.

Pobieranie i wysyłka KSeF wymagają certyfikatu i klucza prywatnego w ustawieniach;
token nie zastępuje certyfikatu. **Wyślij XML do KSeF** wysyła przygotowany plik
FA(3) dla wybranej wystawionej faktury po sprawdzeniu jej danych i potwierdzeniu
środowiska. Program nie generuje tego pliku. **Status wysyłki KSeF** sprawdza
przetwarzanie i zapisuje numer dopiero po przyjęciu przez MF. Nieznany wynik
wysyłki należy wyjaśnić przed jej ponowieniem, aby uniknąć duplikatu.

**Pobierz z KSeF** pobiera sprzedażowe faktury VAT oraz korekty FA(3) z wybranego miesiąca.
Nabywca jest łączony z firmą po NIP-ie. Dokument o tym samym numerze KSeF lub
numerze faktury jest aktualizowany, a pojedynczy niezsynchronizowany dokument
tej firmy i miesiąca może zostać zastąpiony. Gdy dokumentu nie ma, program go
tworzy. Treść, daty, pozycje i kwoty pochodzą z KSeF; zapisane płatności
pozostają przy dotychczasowym ID faktury. Niepewny NIP lub kilka pasujących
faktur wymaga wyjaśnienia i nie powoduje automatycznego nadpisania.
Synchronizacja najpierw pobiera i deduplikuje listę sprzedaży. XML-e już
zapisanych numerów KSeF są pomijane, dlatego po przerwaniu kolejne uruchomienie
wznawia pobieranie brakujących dokumentów. Przy ponad 10 brakujących
dokumentach program korzysta z zalecanego przez MF eksportu paczkowego
`/invoices/exports`, pobiera i weryfikuje skróty zaszyfrowanych części, a
następnie odszyfrowuje paczkę lokalnie. Dla mniejszej liczby respektuje pełny
`Retry-After` KSeF. Synchronizacja działa w tle i pokazuje liczbę znalezionych
oraz przetworzonych faktur, więc nie blokuje okna programu.

W tabeli wystawionych faktur numer faktury i przycisk **Podgląd** otwierają
lokalny widok dokumentu z pełnymi danymi sprzedawcy i nabywcy, pozycjami,
kodem QR oraz opcją **Zapisz PDF**. Dane sprzedawcy i logo ustawia się w
**Ustawieniach faktur**. Techniczna kolumna ID jest ukryta. XSD FA(3)
służy do walidacji danych, a wygląd podglądu generuje program. Kliknięcie
numeru KSeF otwiera urzędowy adres weryfikacyjny z NIP-em sprzedawcy, datą
wystawienia i skrótem XML, jeżeli zostały zapisane podczas wysyłki lub pobrania.
Starszy dokument bez tych danych zostanie uzupełniony przy kolejnej
synchronizacji KSeF; do tego czasu otworzy się formularz wyszukiwania.

## Czynności dodatkowe i kolejne faktury

Zakładka **Czynności dodatkowe** w Zadaniach (dostępna także z Faktur) pokazuje
czynności według klienta, miesiąca i stanu. Można utworzyć czynność razem
z zadaniem albo powiązać ją z istniejącym zadaniem tej samej firmy. Wykonanie
wynika ze statusu zadania; planowane i anulowane czynności nie są fakturowane.

Zwykłe **Generuj faktury** po wystawieniu faktury podstawowej tworzy kolejną
fakturę tylko za nowe, jeszcze nieujęte czynności dodatkowe. Działa to również
po wystawieniu wcześniejszych faktur dodatkowych. Ponowne generowanie korzysta
z istniejącego szkicu i nie nalicza ponownie tych samych czynności.

Nowe pozycje kadrowe mają nazwy „Usługa kadrowo-płacowa – UZ”,
„Usługa kadrowo-płacowa – UoP”, „Usługa płacowa – UZ” lub
„Usługa płacowa – UoP”, zgodnie z wybranym zakresem cennika. Stała opłata
ma nazwę „Usługa kadrowo-płacowa”. Wystawione dokumenty zachowują swoje nazwy.

Korekty nie są eksportowane jako zwykłe faktury z ujemnymi pozycjami.
Przycisk **Korekta w Saldeo** pokazuje dane faktury pierwotnej i zmiany
do wprowadzenia w Saldeo. Po rzeczywistym wystawieniu korekty można zapisać
jej numer w programie, potwierdzając ten fakt. Korekty wystawione w KSeF są
importowane przez **Pobierz z KSeF** i, gdy numer faktury pierwotnej jest
jednoznaczny, zostają z nią powiązane. Ścieżka Saldeo nie importuje korekty
automatycznie i nie zmienia znaków wartości.

W imporcie ING pola opisu przelewu, nadawcy, referencji i rachunku są odrębne.
Przy niepoprawnych polskich znakach wybierz kodowanie pliku (UTF-8,
Windows-1250, ISO-8859-2 lub CP852); program pamięta wybór. Rozpoznany numer
faktury, NIP, rachunek lub jednoznaczna nazwa kontrahenta zawęża jego faktury,
również z wcześniejszych miesięcy. Opcja **Wszystkie faktury** pozwala wybrać
inny dokument ręcznie. Samo dopasowanie nie zapisuje płatności.
Pole rozpoznanego kontrahenta jest edytowalną listą: można wpisać fragment
nazwy, wybrać właściwego klienta i ograniczyć listę faktur do niego. Automatyczne
rozpoznanie pozostaje wyłącznie propozycją do ręcznego potwierdzenia.

Przegląd możliwości użycia bibliotek: [docs/library-review.md](docs/library-review.md).

## Testy

```powershell
python run_tests.py                      # wszystkie pliki testowe
python -m unittest tests.test_invoices   # pojedynczy plik
```

Każdy test działa na osobnym, tymczasowym schemacie PostgreSQL.

## Zrzuty ekranu do dokumentacji

```powershell
python scripts\make_screenshots.py
```

Skrypt tworzy tymczasowy schemat z fikcyjnymi firmami, zapisuje obrazy w `docs\screenshots` i usuwa schemat. Dane z prawdziwej bazy nie są odczytywane; zakładka „Logi” jest pomijana.

## Instalator

Skrypt Inno Setup: `installer\biuro.iss` (wersja 1.3.1). Specyfikacja PyInstallera: `packaging\biuro.spec` (opis w `packaging\README.md`).

## Struktura

```
app\            kod aplikacji (admin, companies, dashboard, db, email, invoices, kalendarz, monthly_view, reports, tasks, utils ...)
tests\          testy unittest
scripts\        narzędzia pomocnicze
installer\      instalator Inno Setup
docs\screenshots\  zrzuty ekranu do README
```
