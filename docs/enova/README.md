# Codzienna liczba dokumentów z Enova365

Importer jest przeznaczony dla Windows Server z dostępem do PostgreSQL aplikacji
oraz do SQL Server Enova w innej maszynie Hyper-V. Działa bez otwartej aplikacji.
Każda firma ma osobną bazę Enova; mapowanie do Biuro odbywa się po NIP.

## Stan integracji

Dostępne są gotowe zapytania odczytowe workera:

- `queries/kpir.sql`: miesięczna liczba wierszy `Dziennik` z nadanym `KPiRLp > 0`,
  według `Dziennik.Data`. Nie odrzuca wpisów na podstawie niezweryfikowanych
  kodów `Bufor`/`KPiRStan` — w otrzymanym wyniku licznik zawierał również `Bufor=1`.
- `queries/kh.sql`: wszystkie dokumenty `DokEwidencja` według `DataEwidencji`,
  z zastąpieniem nagłówków definicji `WBEW` pozycjami `Zaplaty`, których
  `RaportType='RaportyESP'` i `Raport` wskazuje ten sam wyciąg. Pozycje importu
  `OperacjeBankowe` nie są sumowane z płatnościami. DR, LPEW, RKVE i pozostałe
  definicje są wliczane normalnie. Bez dodatkowego filtrowania stanów.

Dzielenie pozycji bankowych przez wybrany dzielnik (domyślnie 3) i jednorazowe
zaokrąglenie sumy wykonuje worker. Przykład: 95 dokumentów, w tym 3 nagłówki WBEW i 190 pozycji płatności,
daje `round(92 + 190/3) = 155`. Nie dobiera się wykluczeń do wcześniejszej,
ręcznie obliczonej wartości.

Zapytania zwracają pełny zakres miesięcy, także zera, oraz NIP z
`OddzialyFirmy.DeklaracjeNIP`. Brak NIP, kilka różnych NIP-ów lub uszkodzone
powiązanie nagłówka WBEW powodują błąd walidacji zamiast zapisu zera.
Jeżeli inna baza używa innych definicji bankowych niż WBEW, trzeba je najpierw
zidentyfikować i dostosować plik zapytania dla tego klienta.

**Ryczałt: mechanizm przyjmuje własny plik SQL w tym samym formacie, ale gotowe
zapytanie do faktur wymaga jeszcze potwierdzenia źródła/definicji.** Nie jest
włączony w przykładowej konfiguracji. Nie dołączono zapytania zastępczego
zwracającego sztuczne zera.

Zapytania oparto na przekazanych metadanych i wynikach diagnostycznych. Nadal
wymagany jest podgląd uruchomiony na rzeczywistym serwerze, zwłaszcza sprawdzenie
źródła NIP. Integracja pozostaje wyłączona do czasu lokalnej konfiguracji.
Do sprawdzenia kolejnych schematów służy `inspect-schema.sql`; nie udostępniaj
haseł ani plików `.env`.

## Sprawdzenie liczników po otrzymaniu schematu

Po eksporcie metadanych użyj `profile-counts.sql`. Wybierz bazę klienta w SSMS,
ustaw `@AccountingType` na `KPIR` lub `KH` oraz `@From` na pierwszy dzień
miesiąca, którego liczbę możesz sprawdzić w Enova. Wykonaj skrypt osobno dla
każdego rodzaju księgowości. Wystarczą wyniki zagregowane — skrypt nie pobiera
kwot, treści dokumentów, kontrahentów ani transakcji pojedynczo.

Schemat potwierdza powiązania `Dziennik.Ewidencja → DokEwidencja.ID`,
`DokEwidencja.Definicja → DefDokumentow.ID` oraz połączenia operacji bankowych
z `RaportyESP` i `Zaplaty`. Same nazwy kolumn nie określają jednak znaczenia
liczbowych pól `Typ`, `Stan` i `KPiRStan`. Wyniki profilowania i oczekiwany
licznik z Enova są potrzebne, aby ustalić filtry bufora, anulowania oraz
wyłączenie nagłówków bankowych z liczby pozostałych dokumentów KH.

Jeśli zewnętrzny analizator SQL zgłasza błąd przy `THROW 50001, ...`, nie jest
to komunikat wykonania z SQL Server. Pełny skrypt używa składni T-SQL dla SSMS.
Dostępne są również prostsze warianty bez `DECLARE`, `IF` i `THROW`:
`profile-kpir-select.sql` (jeden zestaw wyników) oraz `profile-kh-select.sql`
(trzy zestawy wyników). Ustawione są na czerwiec 2026. Wybierz właściwą bazę
klienta w SSMS i wykonaj wariant odpowiadający jej rodzajowi księgowości.
Sprawdzenie składni online nie uruchamia zapytania na serwerze Enova.

Jeśli w zestawieniu widoczna jest wyłącznie liczba WBEW, jest to liczba nagłówków
wyciągów, nie ich pozycji. `profile-kh-statement-positions.sql` pokazuje w jednym
zestawie: wszystkie dokumenty, nagłówki, dokumenty pozostałe, pozycje bankowe,
udział pozycji / 3 oraz przykładową sumę po zaokrągleniu. Pozycje są przypisane
do tych samych wyciągów co dokumenty danego miesiąca, a nie wybierane niezależnie
według daty operacji. Ujawniane są również nagłówki bez powiązanego raportu.

Skrypt porównuje `OperacjeBankowe` (wiersze importu bankowego) i `Zaplaty`
(pozycje płatności). Nie wolno ich dodawać: mogą reprezentować te same operacje,
a płatności wpisane ręcznie mogą nie występować w tabeli importu. `Zaplaty.Raport`
jest odwołaniem polimorficznym; wyniki są rozdzielone przez `RaportType`, aby
nie uznać pasującego numeru innego rodzaju raportu za pozycję bankową.
Źródło i stany trzeba potwierdzić przed przygotowaniem zapytania produkcyjnego.

Profilowanie nie jest zapytaniem produkcyjnym workera i nie włącza importu.
Nie zawiera zmian w tabelach Enova ani przypisania rzeczywistych nazw baz
klientów do repozytorium.

## Reguły

- **KPIR**: liczba wpisów KPIR w miesiącu.
- **RYCZALT**: liczba faktur w miesiącu, bez podwójnego liczenia faktury obecnej
  w kilku ewidencjach.
- **KH**: `inne dokumenty + liczba pojedynczych pozycji bankowych / dzielnik`,
  zaokrąglone do najbliższej liczby całkowitej. Inne dokumenty nie obejmują banku.
- Okres powinien odpowiadać miesiącowi księgowemu w Enova, a nie dacie importu.
  Trzeba ustalić z księgowym, które statusy (np. bufor/anulowanie) wchodzą do
  jego licznika, oraz zweryfikować wynik z ekranem Enova przed włączeniem zapisów.

### Globalny dzielnik WBEW i wyjątki dla firm

Administrator ustawia regułę w **Ustawienia → System → Enova — liczba dokumentów**.
Globalnie obowiązuje dzielnik **3**. W tabeli firm „Globalny” oznacza dziedziczenie;
dla wybranej firmy można wskazać np. **10**. Dzielnik 1 liczy pozycje w całości.
Zaokrąglenie następuje raz, po obliczeniu miesięcznej sumy (połówki w górę).
Przykład: 92 inne dokumenty i 190 pozycji dają 155 przy /3 lub 111 przy /10.
Ustawienia są przechowywane we wspólnej bazie PostgreSQL, więc worker serwerowy
odczytuje te same reguły co aplikacja. Nie trzeba przepisywać dzielnika do JSON.
Reguła dotyczy KH; KPIR i ryczałt nie otrzymują udziału pozycji bankowych.

### Tylko księgowość z etapem „Wysłane wyniki”

Worker najpierw sprawdza `statusy_miesieczne.etap` dla każdej firmy i miesiąca.
**Tylko „Wysłane wyniki” kwalifikuje miesiąc do odczytu Enova.** `etap_kadry`
nie kwalifikuje miesiąca. Brak statusu lub inny etap oznacza pominięcie bez
zapytania do SQL Server i bez zmiany liczby dokumentów. Przy nieciągłych miesiącach
zapytania wykonywane są osobno, aby nie odczytywać niezakwalifikowanych przerw.
Etap jest ponownie sprawdzany po odczycie Enova, przed zapisem; cofnięcie etapu
w trakcie operacji chroni dotychczasową liczbę.

Udany zapis oznacza miesiąc jako sprawdzony tego dnia w strefie Europe/Warsaw.
Kolejne uruchomienie tego samego dnia nie odpytuje ponownie tego źródła/miesiąca.
Zmiana dzielnika lub bazy źródłowej pozwala na ponowne przeliczenie tego dnia.
Błędy nie zapisują znacznika sukcesu, więc można ponowić import po ich naprawieniu.
Podgląd respektuje etap, ale może ponownie odczytać zakończony miesiąc i nie
zużywa dziennego sprawdzenia. Reguła działa w skonfigurowanym zakresie `months_back`.

Domyślnie aktualizowany jest miesiąc bieżący i dwa poprzednie, aby uwzględnić
późniejsze księgowania. `months_back` można zmienić w zakresie 1–24. Miesiące
obliczane są według daty w strefie Europe/Warsaw.

Codzienne uruchomienie **zastępuje** pole `Ilość dokumentów` pełną liczbą za
miesiąc; nigdy nie dodaje jej do wczorajszej wartości. Pozostałe statusy, czas,
notatki i komplet dokumentów są zachowane. W tym zakresie Enova staje się
źródłem licznika: ręczne zmiany licznika w aplikacji zostaną zastąpione.
Wpis pochodzenia danych nadaje Enova pierwszeństwo w raportach, także dla zera.
Błąd jednej bazy zachowuje jej dotychczasowe wartości; inne firmy są importowane.
Nakładające się zadania zapisujące są blokowane w PostgreSQL.

## Kontrakt zapytań SQL

Każdy plik `count_query` wykonuje wyłącznie odczyt i przyjmuje dwa parametry
ODBC `?`: początek zakresu włącznie, koniec wyłącznie. Ma zwrócić dokładnie jeden
wiersz dla każdego żądanego miesiąca, także gdy prawdziwy wynik wynosi zero:

| Kolumna | Wartość |
| --- | --- |
| `nip` | NIP firmy odczytany z bazy Enova, zgodny z mapowaniem |
| `miesiac` | Tekst `YYYY-MM` |
| `dokumenty` | Nieujemna liczba całkowita: KPIR / faktury / pozostałe dokumenty KH |
| `pozycje_bankowe` | Nieujemna liczba pojedynczych pozycji bankowych KH; 0 dla innych typów |

Brak miesiąca, NULL, duplikat miesiąca lub inny NIP to błąd, a nie zero.
Zapytania muszą zwracać spójne dane, bez `NOLOCK` i brudnych odczytów. Konto SQL
musi mieć tylko prawa SELECT do wymaganych tabel/widoków, bez DDL i zapisu.
ODBC dodatkowo otrzymuje żądanie trybu tylko do odczytu; nie zastępuje ono praw SQL.

## Instalacja na Windows Server

1. Zainstaluj Python 3.11+ i Microsoft ODBC Driver 18 for SQL Server. Skopiuj
   źródła projektu do np. `C:\BiuroSync`; ustaw środowisko `.venv` i zainstaluj
   `requirements-enova.lock` (sam worker nie wymaga Qt). Można też użyć
   istniejącego środowiska serwerowego aplikacji i doinstalować `requirements-enova.txt`. Worker nie jest funkcją samego pliku EXE.
2. Skonfiguruj `DB_URL` do istniejącej bazy Biuro oraz `sql_user` i `sql_password`
   w środowisku konta zadania lub lokalnym `.env` projektu. Obsługiwane są też
   `SQL_USER` / `SQL_PASSWORD` oraz `ENOVA_SQL_USER` / `ENOVA_SQL_PASSWORD`.
   Pełna para `ENOVA_SQL_*` ma pierwszeństwo; niepełne pary nie są ze sobą mieszane.
   Ogranicz dostęp do pliku i konta zadania. Nie dodawaj haseł do JSON ani argumentów.
3. Zapewnij połączenie TCP z VM SQL Server i poprawny zaufany certyfikat TLS.
   Worker używa `Encrypt=yes;TrustServerCertificate=no`. W `server` podaj nazwę
   zgodną z certyfikatem i port, np. `enova-sql.firma.local,1433`.
4. Zaktualizuj bazę Biuro poleceniem `python migrate_db.py`, jeśli nie zrobiła
   tego aplikacja po aktualizacji. Skopiuj `config.example.json` poza repozytorium. Wpisz rzeczywiste bazy, NIP-y,
   typy księgowości i pliki zapytań SQL. Skopiuj katalog `queries` obok JSON
   lub wpisz bezwzględne ścieżki do plików. Ścieżki względne są liczone względem JSON.
   NIP musi wskazywać dokładnie jedną firmę w Biuro. Pozostaw `enabled=false`.
5. Uruchom podgląd z katalogu projektu:

   ```powershell
   .\.venv\Scripts\python.exe -m app.integrations.enova.document_counts --config C:\BiuroConfig\enova.json
   ```

6. Porównaj liczby z Enova (przynajmniej po jednym kliencie KPIR, ryczałt i KH).
   Ustaw `enabled=true`, a następnie wykonaj zapis przez dodanie `--apply`.
   Bez tego argumentu komenda nigdy nie zapisuje wartości.

## Harmonogram raz dziennie

W Harmonogramie zadań utwórz zadanie pod kontem mającym dostęp do konfiguracji,
PostgreSQL i loginu SQL. Zaznacz **Uruchom niezależnie od tego, czy użytkownik
jest zalogowany**. Wybierz godzinę codziennego uruchomienia w lokalnym czasie
serwera, np. 02:00 po zakończeniu księgowania. Włącz uruchomienie po pominiętym
terminie; dla istniejącej instancji wybierz **Nie uruchamiaj nowego wystąpienia**.

Program: `powershell.exe`

Argumenty:

```text
-NoProfile -File "C:\BiuroSync\scripts\run_enova_import.ps1" -ConfigFile "C:\BiuroConfig\enova.json" -Apply
```

Katalog startowy: `C:\BiuroSync`. Stosuj obowiązującą na serwerze politykę
uruchamiania/skryptów podpisanych. Skrypt nie zmienia ExecutionPolicy.
Najpierw sprawdź ręczne **Uruchom** pod docelowym kontem zadania.

Logi trafiają do `logs\enova`. Kod zakończenia 0 oznacza sukces wszystkich
źródeł, 1 — błąd konfiguracji lub co najmniej jednej firmy. Historia zapisanych
liczb (poprzednia/nowa, miesiąc, źródłowe składniki) oraz błędów jest dostępna
w tabeli `import_history` jako `enova_document_counts`, w ustawieniach audytu
aplikacji. Podgląd nie zapisuje historii ani liczników. Harmonogram i połączenie
z rzeczywistą Enova trzeba skonfigurować na serwerze — nie są instalowane
przez samo dodanie tych plików do repozytorium.
