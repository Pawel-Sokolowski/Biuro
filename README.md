# Biuro – program dla biura rachunkowego

Aplikacja desktopowa (PyQt6 + PostgreSQL) do prowadzenia biura rachunkowego: klienci, faktury, e-mail, kalendarz, zadania, raporty i kontrola kopii zapasowych.

> Zrzuty ekranu pochodzą z tymczasowej bazy z danymi fikcyjnymi (`scripts\make_screenshots.py`).

## Moduły

| Moduł | Opis |
|---|---|
| **Pulpit** | Podsumowanie dnia, czas pracy, stan kopii zapasowych |
| **Widok miesięczny** | Status firm w wybranym miesiącu; „Cały rok” przełącza na zakończenie roku (deklaracje roczne) |
| **Klienci** | Lista firm, formularz firmy (m.in. deklaracja roczna), pełnomocnictwa (PEL/UPL), data ostatniej kopii bazy |
| **E-mail** | Wiele kont IMAP/SMTP, foldery, reguły, etykiety, przypisywanie wiadomości do firm |
| **Czat** | Wewnętrzna komunikacja pracowników |
| **Faktury** | Wystawianie z cennika, usługi dodatkowe, KSeF, eksport XLSX do Saldeo, JPK_V7M (Enova), rozliczenia i płatności |
| **Raporty** | Import dokumentów i czasu pracy, rentowność (domyślnie cały rok), czas według klienta i pracownika |
| **Zadania** | Zadania z czasomierzem |
| **Kalendarz** | Wydarzenia, import ICS, synchronizacja CalDAV |
| **Ustawienia** | Panel administratora (poniżej) |

## Zrzuty ekranu

![Pulpit](docs/screenshots/pulpit.png)
![Widok miesięczny](docs/screenshots/widok-miesieczny.png)
![Zakończenie roku (Widok miesięczny → Cały rok)](docs/screenshots/zakonczenie-roku.png)
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
| ![Audyt i importy](docs/screenshots/admin-audyt-i-importy.png) | ![Wygląd](docs/screenshots/admin-wyglad.png) |
| ![Synchronizacja poczty](docs/screenshots/admin-synchronizacja-poczty.png) | |

## Kopie zapasowe

Program czyta logi kopii (Cobian Backup, mail „Backup 11 (ENOVA)”) z pliku albo z najnowszego maila na koncie systemowym. Nazwy plików mają postać `firma_datautworzenia` (np. `Firma_20261021.BAK`), z której pobierana jest data.

**Ustawienia → Baza danych i kopie → „Sprawdź z ostatniego maila”** pobiera najnowszy mail, zapisuje datę ostatniej kopii w firmach i pokazuje tabelę *Nazwa firmy | Data | Błąd (TAK/NIE)*. Data jest widoczna w kolumnie „Ostatni backup” na liście klientów.

## Wymagania

- Python 3.11
- PostgreSQL
- Zależności: `pip install --require-hashes -r requirements.lock`

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
pip install --require-hashes -r requirements.lock
python migrate_db.py   # tworzy/aktualizuje schemat bazy
python main.py
```

`migrate_db.py` uruchamia wersjonowane migracje Alembic i sprawdza aktualność schematu.
Pierwszy przebieg zachowuje dane i aktualizuje starszą bazę przez zamrożony bootstrap;
kolejne uruchomienie nie wykonuje go ponownie. Nowe zmiany struktury dodawaj jako
rewizje w `app/db/migrations/versions`, wraz z aktualizacją kontroli wersji schematu.
Po instalacji tej wersji uruchom migrację przed logowaniem: dodaje ona identyfikator
generacji folderów IMAP, aby ponownie użyty UID nie wskazywał treści starej wiadomości.

Lista poczty wczytuje po 500 wiadomości; przycisk **Wczytaj starsze wiadomości**
udostępnia kolejne strony. Wyszukiwanie obejmuje również starsze wiadomości,
w tym pole odbiorcy, i nie wykonuje zapytania po każdym pojedynczym znaku.

## Poczta i powiadomienia

Pełne pobieranie uruchamia się **wyłącznie ręcznie** w **Ustawienia → Moje
ustawienia → Synchronizacja poczty**. Wybierz jedno konto albo **Wszystkie
dostępne konta** i użyj **Pobierz pełne wiadomości z 2 lat**. Każdy użytkownik
może pobierać swoje przypisane konta; administrator ma dostęp do wszystkich
kont. Opcja jest także dostępna dla wybranego konta w administracyjnych
ustawieniach poczty.

Pobieranie działa w tle i zapisuje pełne wiadomości wraz z załącznikami z
ostatnich 730 dni ze wszystkich wybieralnych folderów. Można pracować w innych
modułach lub zminimalizować okno. Po przerwaniu uruchom opcję w ustawieniach
ponownie — zapisane już wiadomości zostaną pominięte. Otwarcie aplikacji ani
nieukończona poprzednia synchronizacja nie uruchamiają pełnego pobierania.
Postęp pokazuje folder, liczbę sprawdzonych wiadomości i czas działania.

Później program sprawdza nowe wiadomości co dwie minuty we wszystkich folderach.
Różne konta synchronizują się niezależnie; operacje jednego konta są kolejkowane,
a nie uruchamiane równocześnie. Nieudane odświeżenie folderów nie usuwa lokalnej
poczty. Obok pełnego nazwiska pracownika jest liczba przypisanych mu
nieprzeczytanych wiadomości na wybranym koncie. Liczniki przypisań są odświeżane
co 15 sekund oraz po odczytaniu/przypisaniu wiadomości.

Przycisk **Powiadomienia** w nagłówku pokazuje przypisane e-maile i otwarte
zadania. Kliknięcie przenosi do wiadomości lub zaznacza zadanie na liście.
Stan odczytania powiadomień jest zapisywany dla użytkownika na tym komputerze;
oznaczenie powiadomienia jako odczytane nie kończy zadania ani nie oznacza
wiadomości e-mail jako przeczytanej.

## Rozliczenia i KSeF

### Wygląd i katalog usług

Każdy użytkownik ma dostęp do **Ustawienia → Moje ustawienia → Wygląd**.
Można wybrać jasny lub ciemny motyw oraz kolor akcentu. Zmiany działają od razu
i są zapisywane osobno dla każdego konta na danym komputerze. Ustawienia
administracyjne nadal wymagają odpowiednich uprawnień.

Podczas wystawiania faktury przycisk **Dodaj z katalogu usług** otwiera
wyszukiwalną listę usług dodatkowych. W zakładce roboczych faktur pozycje
edytuje się bezpośrednio w dolnej tabeli: nazwa, ilość, jednostka, cena netto,
VAT i kategoria. W tym samym panelu można zapisać daty wystawienia, sprzedaży,
termin płatności, przyczynę korekty i podstawę zwolnienia z VAT.
Zmiany pozycji zapisują się automatycznie, a wartości netto i brutto
są przeliczane. Obok tabeli można wybrać usługę z katalogu i jej ilość,
dodać nową pozycję lub usunąć wybraną.
Wybierz usługę i ilość; nazwa, cena netto i VAT są pobierane z katalogu.
Po dodaniu można edytować pozycję. Katalog jest zarządzany przez administratora
w **Ustawienia → Usługi dodatkowe**. Dodanie pozycji z katalogu nie tworzy
wpisu w ewidencji wykonanych czynności.

W formularzu nowego kontrahenta przycisk **Pobierz dane po NIP (GUS / CEIDG)**
uzupełnia dane firmy z rejestrów. Pobieranie działa w tle, wymaga konfiguracji
źródeł danych i nie zapisuje kontrahenta automatycznie. Dane można sprawdzić
i poprawić przed zapisaniem.

Pole **Zdrowotna wliczona do ceny** oznacza, że opłata jest już zawarta w cenie
księgowości: nie zwiększa ceny i nie tworzy osobnej pozycji zdrowotnej na fakturze.
Gdy pole jest odznaczone, zdrowotna jest naliczana osobno według konfiguracji.

### Uzgadnianie płatności

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

Pod pozycjami faktury oraz w PDF znajduje się podsumowanie według stawek VAT:
netto, podatek i brutto, z osobnym wierszem sumy.

Pobieranie i wysyłka KSeF wymagają certyfikatu i klucza prywatnego w ustawieniach;
token nie zastępuje certyfikatu. Zaznacz dokumenty i użyj **Wyślij zaznaczone do
KSeF** (wystawione) lub **Wystaw i wyślij do KSeF** (robocze). Numer faktury
można wcześniej uzupełnić i zapisać nad tabelą pozycji. Program generuje FA(3)
z danych faktury i sprawdza go lokalnie z dołączonym schematem XSD.
Po potwierdzeniu środowiska wysyłka i sprawdzanie przyjęcia działają w tle.
Po przyjęciu numer pojawia się w tabeli oraz podglądzie/PDF; przyjęty XML jest
pobierany do katalogu danych aplikacji `ksef/<środowisko>/<NIP>/<numer>.xml`.
Ponowne sprawdzanie i pobieranie nie wysyła faktury drugi raz. Stan niepewnej
wysyłki jest zachowywany również po zamknięciu programu. **Status wysyłki KSeF**
pozwala wznowić sprawdzanie, gdy automatyczne próby się zakończą.
Generator obsługuje krajowe faktury PLN oraz korekty, ze stawkami 23/22, 8/7,
5 i 0%; zwolnienie wymaga podania podstawy prawnej. Nieobsługiwane procedury
lub brakujące dane zatrzymują wysyłkę z komunikatem.

### Wysyłka PDF do klienta

W formularzu firmy sekcja **Faktury i ceny → Adres e-mail do wysyłki faktur**
pozwala wskazać adres rozliczeniowy, np. `invoices@firma.com`. Ma on pierwszeństwo
przed ogólnym adresem kontaktowym. Formularz firmy jest podzielony na zakładki,
a przyciski zapisu pozostają widoczne poza przewijaną treścią.

Zaznacz **Po przyjęciu w KSeF wyślij PDF do klienta** przed wysyłką dokumentów.
Program pokaże nadawcę i odbiorców do zatwierdzenia. Kolejność jest następująca:
przyjęcie w KSeF, nadanie numeru, pobranie i sprawdzenie XML, wygenerowanie PDF
z numerem i urzędowym QR, wysyłka z zatwierdzonego konta pocztowego. PDF powstaje
na podstawie przyjętego XML, a nie późniejszych zmian w kartotece. Adresaci są
utrwalani w kolejce. Przycisk **Wyślij PDF e-mailem** umożliwia wysyłkę również
faktur mających już numer KSeF. Stan wysyłki jest widoczny w tabeli faktur.
Niepewny wynik SMTP wymaga sprawdzenia; program nie ponawia automatycznie
wiadomości, która mogła już dotrzeć. Nowa wersja wymaga jednokrotnego uruchomienia
**Biuro – aktualizacja bazy** po instalacji.

**Pobierz z KSeF** pobiera sprzedażowe faktury VAT oraz korekty FA(3) z wybranego miesiąca.
Nabywca jest łączony z firmą po NIP-ie. Dokument o tym samym numerze KSeF lub
numerze faktury jest aktualizowany, a pojedynczy niezsynchronizowany dokument
tej firmy i miesiąca może zostać zastąpiony. Gdy dokumentu nie ma, program go
tworzy. Treść, daty, pozycje i kwoty pochodzą z KSeF; zapisane płatności
pozostają przy dotychczasowym ID faktury. Niepewny NIP lub kilka pasujących
faktur wymaga wyjaśnienia i nie powoduje automatycznego nadpisania. Po
synchronizacji program pokazuje takie dokumenty w osobnej tabeli. Firmę można
wyszukać po nazwie lub NIP-ie i przypisać ręcznie bez ponownego pobierania XML-a.
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
**Ustawieniach faktur**. Podgląd wypełnia szerokość okna, a zapis PDF korzysta
z pionowej strony A4. Techniczna kolumna ID jest ukryta. XSD FA(3)
służy do walidacji danych, a wygląd podglądu generuje program. Kliknięcie
numeru KSeF otwiera urzędowy adres weryfikacyjny z NIP-em sprzedawcy, datą
wystawienia i skrótem XML, jeżeli zostały zapisane podczas wysyłki lub pobrania.
Starszy dokument bez tych danych zostanie uzupełniony przy kolejnej
synchronizacji KSeF; do tego czasu otworzy się formularz wyszukiwania.
Miesiąc dokumentu KSeF zawsze wynika z daty wystawienia; migracja naprawia też
starsze wpisy zapisane w innym miesiącu. W Fakturach i Widoku miesięcznym miesiąc
wybiera się tym samym małym kalendarzem z przyciskami miesięcy i zmianą roku.

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

## Zakończenie roku, JPK_V7M i raporty

**Zakończenie roku podatkowego.** W **Widoku miesięcznym** wybór miesiąca ma
przycisk **Cały rok**. Po jego wybraniu tabele Księgowość/Kadry zastępuje lista
firm z rocznymi deklaracjami (np. PIT-28, PIT-36, PIT-36L, CIT-8, CIT-8E)
i ich stanem: złożona lub do złożenia, data złożenia, kwota, nr UPO i uwagi.
Wiersz można oznaczyć jako złożony albo edytować (dwuklik); dostępne są filtry
„Tylko niezłożone” i wyszukiwanie klienta. Wybór miesiąca przywraca zwykły widok.

Deklaracja roczna firmy jest dobierana automatycznie z pola **Forma
opodatkowania** w karcie firmy: ryczałt → PIT-28, skala → PIT-36, liniowy →
PIT-36L, księgi rachunkowe → CIT-8, CIT estoński → CIT-8E. Pole **Deklaracja
roczna** w karcie firmy pozwala to nadpisać, a **Dodatkowe deklaracje roczne**
(np. `PIT-4R, PIT-11`) dopisuje kolejne. Firmy bez rozpoznanej deklaracji są
zliczone nad tabelą. Stan zapisuje tabela `zakonczenie_roku`; programu nie
łączy z e-Deklaracjami, więc nr UPO wpisuje się ręcznie.

**JPK_V7M (Enova).** W wystawionych fakturach przycisk **JPK_V7M (Enova)**
generuje ewidencję sprzedaży z numerami KSeF (`NrKSeF`) dla zaznaczonych
faktur. Program pyta o kod urzędu i e-mail (zapamiętuje je w ustawieniach)
i zapisuje `JPK_V7M_<miesiąc>.xml`. To plik ewidencji VAT bez pozycji
i płatności, więc Enova zaimportuje go jako wpisy rejestru VAT, a nie pełne
faktury. Struktura odpowiada schemie JPK_V7M (3).

**Faktury i KSeF.** Lista rozliczeń i pobieranie z KSeF działają dla wszystkich
firm (metoda kasowa); bez wybranej firmy pobranie pyta o numer faktury lub KSeF.
Miesiąc w Fakturach można ustawić na **Cały rok**. Metoda kasowa trafia do XML
jako `P_16`. Daty w fakturach, wpłatach i widoku miesięcznym mają format
`dd-MM-rrrr` (np. `29-10-2026`), by nie mylić ich z numerem `FV/10/10/2026`.

**Raport rentowności.** Domyślnie obejmuje cały bieżący rok (od 01 do 12);
zakres „Od/Do” można skrócić (wybór jest miesięczny).

**Czynności dodatkowe.** Kolumna **Faktura** stoi tuż za miesiącem i pokazuje
numer faktury, do której dopisano czynność (albo „faktura robocza”).

## Testy

```powershell
pip install --require-hashes -r requirements.lock -r requirements-test.lock
python run_tests.py                      # wszystkie pliki testowe
python -m unittest tests.test_invoices   # pojedynczy plik
```

Testy bazodanowe działają na osobnych, tymczasowych schematach PostgreSQL.
Testy generowane używają Hypothesis (zależność wyłącznie testowa).
Opis kolejki synchronizacji i pomiarów wydajności: [docs/sync-workflows.md](docs/sync-workflows.md).

## Zrzuty ekranu do dokumentacji

```powershell
python scripts\make_screenshots.py
```

Skrypt tworzy tymczasowy schemat z fikcyjnymi firmami, zapisuje obrazy w `docs\screenshots` i usuwa schemat. Dane z prawdziwej bazy nie są odczytywane; zakładka „Logi” jest pomijana.

## Instalator

Skrypt Inno Setup: `installer\biuro.iss` (wersja 1.3.9). Specyfikacja PyInstallera: `packaging\biuro.spec` (opis w `packaging\README.md`).

## Struktura

```
app\            kod aplikacji (admin, companies, dashboard, db, email, invoices, kalendarz, monthly_view, reports, tasks, utils, year_end ...)
tests\          testy unittest
scripts\        narzędzia pomocnicze
installer\      instalator Inno Setup
docs\screenshots\  zrzuty ekranu do README
```

### Wspólna stopka poczty i dane pracownika

W **Ustawienia → Konta e-mail → Globalny wzór stopki…** administrator edytuje
wspólny wzór. Pola `{imie}`, `{nazwisko}` i `{telefon}` są zastępowane danymi
pracownika piszącego wiadomość, niezależnie od konta nadawcy. Wzór można
uzupełnić zdjęciem lub logo przez wklejenie obrazu albo **Dodaj zdjęcie / logo…**.
Obrazy są zapisywane w szablonie, a przy wysyłce dołączane do HTML jako obrazy MIME/CID.

Stopkę widać od razu w treści nowej wiadomości, odpowiedzi i przekazania.
Przycisk **Moja stopka…** umożliwia zapisanie własnego telefonu i pokazuje podgląd;
zmiana telefonu aktualizuje stopkę w otwartym edytorze. Administrator może zmienić
telefon pracownika przez **Użytkownicy → Stopka**. Imię i nazwisko pochodzą z konta
pracownika. Zmiana globalnego wzoru obowiązuje w nowo otwieranych wiadomościach.
Dawne indywidualne treści stopek pozostają w bazie, ale nowe wiadomości używają
wspólnego wzoru. Wymagana jest aktualizacja schematu do `2026-11-02.1`.

## Wystawianie i historia dokumentów

W **Fakturach roboczych** zaznacz dokumenty i kliknij **Wystaw faktury**.
Program nadaje numery, zapisuje wystawienie i otwiera zakładkę **Faktury wystawione**;
nie pyta o plik Excel. Eksport XLSX do Saldeo jest osobną opcją dla wystawionych dokumentów.
Wysyłka do KSeF pozostaje oddzielną czynnością.

Wystawionych faktur nie usuwa się ani nie zmienia ich numerów. **Anuluj fakturę**
wymaga przyczyny i zachowuje cały dokument, numer, autora i czas operacji.
Dotyczy wyłącznie dokumentów niewprowadzonych do obrotu, bez historii płatności
ani trwającej/przyjętej wysyłki KSeF; pozostałe dokumenty wymagają korekty.
Anulowane dokumenty mają oznaczenie w tabeli i PDF, nie są rozliczane ani eksportowane.
Raz zarezerwowany numer nie wraca do puli po usunięciu szkicu.
Obliczenia pozycji korzystają z Decimal i zaokrąglania HALF_UP do groszy;
nowe zapisy sum faktur są przechowywane jako NUMERIC(18,2).

## Roczne rozliczenie według roku i wspólnika

W karcie firmy, w sekcji **Roczne rozliczenie**, wybierz **Rok** i **Rozliczenie dla**
(firma lub wspólnik), a następnie zaznacz odpowiednie deklaracje. Wspólników dodaje się
w zakładce **Wspólnicy**. Przykład: dla 2025 r. Anna może mieć PIT-36, a Jan PIT-36L.
Można zaznaczyć kilka deklaracji. Selekcje różnych lat i osób są niezależne;
przełączanie zachowuje niezapisane zmiany, a przycisk zapisu firmy utrwala je razem.
W zakończeniu roku deklaracja każdego wspólnika ma osobny stan złożenia, datę i UPO,
także gdy oboje składają ten sam formularz.

Istniejące ustawienia firmy są punktem wyjścia dla nieskonfigurowanych lat.
Pierwsze otwarcie listy roku utrwala dostępne dane; program nie odtwarza nieznanej
historii sprzed tej zmiany. Zapisane lata nie zmieniają się po zmianie danych lub statusu
firmy. Zapisane złożenia pozostają widoczne również po odznaczeniu obowiązku.

## Bezpieczny reset hasła

**Nie pamiętam hasła** wysyła jednorazowy kod ważny 15 minut; dotychczasowe hasło
pozostaje aktywne. W oknie logowania wybierz **Mam kod resetowania**, wklej kod i podaj
nowe hasło dwukrotnie. Dopiero wykorzystanie kodu zmienia hasło i unieważnia zapisane
sesje. W bazie przechowywany jest wyłącznie skrót kodu.

Po instalacji tej wersji uruchom **Biuro – aktualizacja bazy** (`python migrate_db.py`).

Katalog **Rocznego rozliczenia** zawiera także obowiązki ZUS (DRA/RCA — roczne zdrowotne,
IWA, ZSWA, informacja roczna dla ubezpieczonego) oraz PFRON (DEK-R, INF-2).
Pełna lista i sprawdzone źródła urzędowe: [docs/annual-declarations.md](docs/annual-declarations.md).


### Wzór numeracji faktur i wspólne importy bankowe

W **Ustawienia → Faktury** ustaw wzór numeracji, np.
`{prefix}/{number:04d}/{month:02d}/{year}` → `FV/0001/11/2026`.
`prefix` wynosi FV, KFV, FZ albo PRO zależnie od rodzaju dokumentu.
Bez pola `month` numeracja jest roczna. Numer nadawany jest przy wystawianiu;
zmiana wzoru nie zmienia numerów już nadanych i nie zwalnia numerów historycznych.
W podglądzie wysyłki roboczy dokument nie jest oznaczany numerem technicznym `#ID`.

**Faktury → Rozlicz MT940** pokazuje wspólne importy wszystkim pracownikom
z uprawnieniami do finansów. Pole „Pokaż wszystkie zaimportowane operacje” obejmuje
również rozliczone operacje, wypływy i inne waluty. Ich przypisywanie jako wpłat PLN
jest zablokowane. Przycisk „Wczytaj kolejne 1000 operacji” udostępnia starsze wpisy.
Pod listą widnieje źródło danych i liczba zapisanych operacji, przydatne przy
porównywaniu widoczności na dwóch stanowiskach.


### Ręczne oznaczanie zapłaty

W **Faktury → Wystawione** przycisk **Zapłacono** zapisuje wpłatę bez importowania
wyciągu MT940. Kwota domyślna to pełne pozostałe saldo (dla niezapłaconej faktury:
kwota brutto); można ją zmienić. Data zapłaty jest wybierana z kalendarza z nawigacją
między miesiącami i może być wcześniejsza niż data wystawienia faktury.
Można wskazać przelew/wpłatę ręczną albo gotówkę. Wpłata trafia do wspólnej ewidencji
rozliczeń i można ją cofnąć w zakładce Rozliczenia.

Filtr **Płatność** rozróżnia: wszystkie, zapłacone, częściowo zapłacone i niezapłacone.
Niezapłacone obejmują wszystkie faktury z dodatnim pozostałym saldem, również częściowe
wpłaty. Filtr uwzględnia łącznie płatności ręczne, gotówkę i rozliczenia MT940.
