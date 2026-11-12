# Automatyczne raporty e-ZLA z ZUS

Pobieranie działa jako osobne zadanie istniejącego workera. Wykorzystuje usługę
SOAP `PobierzRaporty` opisaną w [instrukcji ZUS dla producentów programów kadrowo-płacowych](https://bip.zus.pl/documents/493361/0/e-ZLA+Instrukcja+skorzystania+z++us%C5%82ugi+do+rapot%C3%B3w+dla+p%C5%82atnika.pdf/25964652-2ad6-af1b-71fc-a5f49e6d3875).
Nie wymaga otwierania PUE/eZUS ani podpisu na karcie/USB. Pobiera już wygenerowane
raporty; ich generowanie i zakres muszą być wcześniej ustawione w ZUS.

## Konfiguracja w Biuro

1. Administrator otwiera **Ustawienia → ZUS — e-ZLA**.
2. Wpisuje wspólny dedykowany login/e-mail usługi raportów i hasło usługi.
   Nie jest to hasło konta pocztowego ani zwykły login do PUE.
3. Wpisuje osobne hasło szyfrowania ZIP ustawione dla raportów w ZUS.
4. Zaznacza firmy, włącza automatyczne pobieranie i zapisuje ustawienia.
   Puste pola haseł przy kolejnej edycji zachowują zapisane wartości.
5. Zaznacza wiersz firmy i używa **Sprawdź usługę i ZIP**. Test działa z tego
   komputera, uwzględnia bieżące pola i nie zapisuje pobranych raportów.
   Jeżeli ZUS nie zwróci żadnego raportu, test potwierdza połączenie, ale nie hasło ZIP.
6. W **Worker w tle** włącza zadania i pozycję **Pobieranie raportów e-ZLA z ZUS**.
   Domyślny odstęp to godzina; można ustawić własny, w tym 86400 s dla raz na dobę.
   **Pobierz teraz** na stronie e-ZLA uruchamia zapisany zakres firm bez oczekiwania
   na worker. Nie używa niezapisanych pól formularza.
7. **Kadry → Sprawdzanie → Raport: e-ZLA → Odśwież** pokazuje raporty na każdym
   komputerze połączonym z tą samą bazą Biuro i właściwym kluczem szyfrowania.

Serwer workera i komputery korzystające z testu potrzebują połączenia HTTPS z
`pue.zus.pl` na porcie **8500**. TLS pozostaje weryfikowany. Usługa ma też publiczne
techniczne uwierzytelnienie HTTP wskazane przez ZUS; program obsługuje je sam,
nie wpisuje się go zamiast dedykowanego loginu płatnika.

## Daty, zapis i ponawianie

Pierwsze pobranie obejmuje raporty wygenerowane w ostatnich 30 dniach. Kolejne
zaczyna się dzień przed datą ostatniego poprawnego sprawdzenia, z ograniczeniem
do okna 30 dni. To daty generowania raportów, nie okresy nieobecności pracowników.
Przerwa dłuższa niż 30 dni może wymagać ręcznego odzyskania starszych raportów.

Raport ZIP jest dekodowany w pamięci, a jego `raport.xml` walidowany. Program
obsługuje szyfrowanie ZIP/AES, nie rozpakowuje ścieżek archiwum na dysk i nie
importuje pojedynczych załączników XML zamiast raportu. Oryginalne bajty XML są
zapisywane w PostgreSQL jako szyfrogram przy użyciu wspólnego `BIO_ENCRYPTION_KEY`.
Nie zapisujemy nazwisk, identyfikatorów pracowników ani treści zwolnień jawnie
w metadanych synchronizacji. Hasła usługi i ZIP również są szyfrowane.
Archiwum ZIP nie jest przechowywane; zachowany XML jest dokładną treścią raportu.
Kopie bazy zawierają zaszyfrowane raporty, a ich odczyt wymaga właściwego klucza.

Zapis jest atomowy dla firmy. NIP raportu musi zgadzać się z kartoteką firmy.
Identyczny XML jest rozpoznawany po skrócie i nie jest zapisywany drugi raz.
Korekty, anulowania i różne wersje raportów pozostają osobnymi danymi do kontroli.
Nie traktujemy nowszego raportu jako automatycznej zgody na zmianę nieobecności.

Błąd firmy nie zatrzymuje pozostałych. Nie przesuwa daty ostatniego sukcesu ani
nie usuwa wcześniejszych raportów. W zestawieniu widać błąd i ostatnie poprawne
sprawdzenie. Kod 101 dotyczy loginu/hasła usługi/NIP, a błąd odczytu ZIP może
wskazywać inne hasło szyfrowania. Brak raportów nie potwierdza braku zwolnień.

## Zakres

To automatyczne pobieranie i wspólne zestawienie. Nie zapisuje nieobecności do
Enovy, nie dopasowuje pracowników do jej kartoteki i nie wysyła dokumentów ZUS.
Podłączenie importera Enovy i sterowanie podpisywaniem w Płatniku są osobnymi
adapterami wymagającymi dopasowania faktycznych okien lub obsługiwanego interfejsu.

Nie wykonano rzeczywistego połączenia do ZUS z danymi biura podczas prac nad kodem.
Przewidziano test z poziomu ustawień aplikacji. Testy transportu, ZIP/AES, zapisu,
ponawiania i uprawnień korzystają z syntetycznych danych i atrap usługi w GitHub CI.

## Panel workera i przerwy

W **Ustawienia → Worker w tle** dostępny jest panel z podzakładkami **Status**,
**Logi**, **e-ZLA**. Można otworzyć go osobno przyciskiem na tej stronie albo
uruchomić zainstalowane `Biuro.exe --worker-panel` (dla źródeł: `python main.py --worker-panel`). Panel wymaga zalogowania administratora. Korzysta z tej samej
konfiguracji bazy co aplikacja. Nie uruchamia kolejnego procesu roboczego;
zamknięcie okna nie zatrzymuje zainstalowanego workera.

Status pokazuje sygnał życia, aktywne zadania, start/koniec i następne sprawdzenie.
Logi to ostatnie 200 zdarzeń rozpoczęcia/zakończenia/błędu zapisanych w bazie,
dostępne także z innego PC. Szczegółowe pliki logów są na komputerze workera;
przycisk folderu otwiera folder na bieżącym komputerze. Podzakładka e-ZLA pokazuje
firmy, ostatnie próby/sukcesy, liczbę nowych raportów i błędy. Widać aktualną firmę,
etap pobierania/zapisu oraz odliczanie do wznowienia po przerwie. Odczyt panelu
wykonuje się w tle, co 5 sekund; ukryty panel nie odpytuje bazy.

**ZUS — e-ZLA** ma regulowaną przerwę między firmami (domyślnie 2 s) oraz po błędzie
(domyślnie 30 s). Kolejka działa po jednej firmie, a blokada bazy zapobiega
nakładaniu się ręcznego pobrania i workera. Przerwę przerywa zamknięcie procesu
workera; nie blokuje ona innych zadań ani interfejsu. Każde zadanie workera czeka
pełny skonfigurowany interwał po zakończeniu poprzedniego przebiegu, również po
błędzie — długie przebiegi nie powodują serii natychmiastowych powtórzeń.
