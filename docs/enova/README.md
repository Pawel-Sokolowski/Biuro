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

`queries/ryczalt.sql` liczy nagłówki ZR, SPU i RKVE (faktury metodą kasową)
według `DataEwidencji`. Potwierdzony przykład: czerwiec 2026, 18 + 2 + 1 = 21.
Nie dolicza DR, LPEW ani STE. Worker wybiera ten plik automatycznie dla firmy
z formą opodatkowania Ryczałt. Przy innych definicjach faktur trzeba dostosować zapytanie.

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
- **RYCZALT**: liczba nagłówków faktur ZR, SPU i RKVE w miesiącu; nie łączy
  kilku ewidencji ani pozycji faktur.
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

Codzienny import uzupełnia `Ilość dokumentów` w **Widoku miesięcznym** obliczoną
sumą Enova. Etap „Wysłane wyniki” jest tylko warunkiem odczytu i nigdy nie jest
zmieniany przez importer. Istniejące ręczne liczby (także pochodzące z innych
importów) pozostają bez zmian. Wartość Enova może być odświeżana, dopóki użytkownik
nie edytuje licznika. Ręczna edycja, także wpisanie zera, trwale chroni miesiąc
przed kolejnymi zapisami Enova. Ponowne sprawdzenie i blokada wiersza przed zapisem
chronią także zmianę dokonaną w trakcie pobierania danych z SQL Server.

Dla starych danych bez informacji o pochodzeniu niezerowa wartość jest traktowana
jako ręczna; domyślne zero jest traktowane jako nieuzupełnione. Zer wpisanych
ręcznie przed wprowadzeniem tej funkcji nie można rozpoznać wstecznie.
Podział według rodzajów i wynik Enova nadal odświeżają się osobno od chronionego
licznika. Pozostałe statusy, czas i notatki są zachowane. Błąd jednej bazy zachowuje
jej dotychczasowe wartości; inne firmy są importowane. Nakładające się zadania
zapisujące są blokowane w PostgreSQL.

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

1. Zainstaluj Python 3.14+ i Microsoft ODBC Driver 18 for SQL Server. Skopiuj
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

### Automatyczny wybór rodzaju księgowości

Skopiuj `config.example.json`; nie trzeba tworzyć konfiguracji od zera. Uzupełnij
adres `server` i listę `sources`, podając dla każdej firmy tylko `database` oraz
`nip`. Rodzaj księgowości jest pobierany z pola „Forma opodatkowania” firmy w Biuro.
Dla KPiR, ryczałtu i ksiąg rachunkowych importer sam wybiera dołączone zapytanie SQL.
Zmiana rodzaju w Biuro pozwala na ponowne przeliczenie tego samego dnia.

Stare konfiguracje nadal działają. Gdy zawierają własny `count_query` i jawny
`accounting_type`, niezgodność z rodzajem zapisanym w Biuro zatrzymuje import firmy;
usuń oba pola, aby używać automatycznego wyboru. Ryczałt liczy definicje ZR (zakupy), SPU (sprzedaż usług) i RKVE (faktury metodą kasową),
według DataEwidencji. Nie dodaje deklaracji, list płac ani dokumentów środków trwałych.
Potwierdzony przykład czerwca 2026: 18 ZR + 2 SPU + 1 RKVE = 21.

## Konfiguracja w aplikacji i zakładka Księgowość

Administrator otwiera **Ustawienia → System → Enova — połączenie**. Pole serwera
przyjmuje `IP\INSTANCJA` lub `host,port`. Nazwa serwera musi odpowiadać
certyfikatowi SQL Server. Login SQL powinien mieć wyłącznie prawa SELECT.
W tabeli firm wpisz nazwę bazy Enova; puste pole pomija firmę. NIP jest sprawdzany
przy każdym imporcie. Hasło jest przechowywane zaszyfrowane przez Fernet;
aplikacja i konto zadania wymagają tego samego `BIO_ENCRYPTION_KEY`.
Puste hasło przy kolejnym zapisie zachowuje dotychczasowe. Nie wpisuj klucza w chat.

Po zapisaniu ustawień wykonaj podgląd na serwerze:

```powershell
.\.venv\Scripts\python.exe -m app.integrations.enova.document_counts --saved-settings
```

Po porównaniu wyników włącz import w ustawieniach. Codzienne zadanie można
zarejestrować lokalnie na Windows Server:

```powershell
.\scripts\install_enova_daily_task.ps1 -Time '02:00'
```

Skrypt pyta lokalnie o konto Windows zadania i nie nadpisuje istniejącego zadania.
Czas oznacza strefę lokalną serwera. Wymaga odpowiednich uprawnień Windows;
repozytorium samo nie instaluje zadania. Konfiguracja JSON nadal jest obsługiwana.
Zadanie korzysta ze wspólnych ustawień PostgreSQL i działa bez otwartej aplikacji.
Pozostaje reguła odczytu tylko dla miesięcy „Wysłane wyniki”.

**Księgowość** pokazuje miesięczne liczniki według firmy, podział według symbolu
definicji dokumentu, osobno pozycje bankowe KH oraz datę ostatniego importu.
Podział KPiR dotyczy wpisów dziennika powiązanych z definicją dokumentu;
brak powiązania jest oznaczony „Bez definicji”. Podział i licznik są zapisywane
w tej samej transakcji. Zapytania z podziałem wymagają SQL Server 2017 lub nowszego
(`STRING_AGG`, `STRING_ESCAPE`). Starsze własne zapytania z czterema kolumnami
nadal działają, ale nie dostarczają podziału. Faktury korzystają nadal wyłącznie
z obliczonej sumy miesięcznej; nie otrzymują pozycji według typów.

Status JPK pochodzi z **Enova**, osobno dla firmy i miesiąca. Domyślne
„Nie sprawdzono” nie sugeruje wysyłki ani UPO. Wskazanie „Przyjęty (UPO)” wymaga
jawnego statusu `accepted` i niepustego potwierdzenia UPO. Odczyt odbywa się
raz dziennie również dla miesięcy bez etapu „Wysłane wyniki”; podgląd nie zapisuje.
Błąd źródła zachowuje dotychczasowe dane. Data odczytu i referencja UPO są
widoczne w podpowiedzi statusu.

Repozytorium nie zawiera potwierdzonego schematu JPK danej instalacji Enova.
Uruchom `inspect-jpk-schema.sql` w SSMS. Po potwierdzeniu znaczenia pól przygotuj
zapytanie SELECT wybierające ostateczny/najnowszy status danego okresu
(z uwzględnieniem korekt). Nie zgaduj statusu na podstawie numeru kodu.
Ścieżkę do pliku SQL na serwerze importu wpisz w polu „Zapytanie statusu JPK”.
Dla konfiguracji JSON obsługiwane jest `jpk_query` w każdym wpisie `sources`.
Bez skonfigurowanego zapytania dokumenty są importowane normalnie, JPK pozostaje
niesprawdzony.

Zapytanie JPK ma dwa parametry ODBC `?`: początek zakresu włącznie i koniec
wyłącznie. Zwraca dokładnie jeden wiersz na miesiąc z kolumnami `nip`, `miesiac`
(`YYYY-MM`), `status` oraz `upo` (referencja potwierdzenia, NULL dopuszczalny poza
`accepted`). Dozwolone statusy: `not_sent`, `sent`, `accepted`, `rejected`,
`not_applicable`. Brak wiersza/NULL status/niezgodny NIP/duplikat to błąd;
nie są zamieniane na „niewysłany”. Bez prawdziwego źródła nie można potwierdzić
połączenia z JPK ani przetestować go w tej sesji.

Dostęp do nowej zakładki można nadać w ustawieniach roli (role z domyślnymi
zakładkami otrzymują ją automatycznie).

## Test połączenia i praca niezależna

Przycisk **Sprawdź połączenie z bazą Enova** w ustawieniach korzysta z aktualnie
wpisanego serwera, loginu, hasła i nazw baz, bez konieczności zapisywania ustawień.
Sprawdza logowanie i dostęp do każdej wskazanej bazy (`SELECT DB_NAME()`), nie
uruchamia importu, nie zmienia liczników ani statusów. Puste hasło wykorzystuje
zapisane hasło. Test połączenia z komputera użytkownika nie zastępuje testu na
serwerze, gdzie uruchamiany jest importer.

Na Windows Server z dostępem do PostgreSQL Biuro i SQL Server Enova zainstaluj
**worker ze źródeł**, Python 3.14+, zależności `requirements-enova.lock` i Microsoft
ODBC Driver 18. Nie trzeba instalować ani pozostawiać otwartego okna Biuro.
Skonfiguruj `DB_URL` i wspólny `BIO_ENCRYPTION_KEY` w środowisku konta zadania
lub zabezpieczonym lokalnym `.env`. Ustawienia zapisane w aplikacji są we wspólnej
bazie PostgreSQL. Na serwerze uruchom podgląd `--saved-settings`, a po weryfikacji
zarejestruj `scripts/install_enova_daily_task.ps1 -Time '02:00'`.
Zadanie uruchamia worker raz dziennie, także bez zalogowanego użytkownika,
a proces kończy się po imporcie. Serwer musi być włączony w czasie pracy zadania;
ustawienie StartWhenAvailable nadrabia pominięty termin. W Harmonogramie zadań
sprawdź ręcznie pierwsze uruchomienie pod docelowym kontem, wynik i logi `logs/enova`.

### Test z komputera użytkownika i sterownik

Przycisk „Sprawdź połączenie z bazą Enova” łączy się bezpośrednio **z komputera,
na którym działa Biuro**. Worker nie jest potrzebny do testu. Wystarczą serwer,
login i hasło; bez mapowań firm sprawdzane jest logowanie do domyślnej bazy konta.
Po wpisaniu baz firm test sprawdza również dostęp do każdej z nich.

Windowsowy instalator Biuro zawiera moduł Python `pyodbc`. Sterownik systemowy
Microsoft ODBC Driver for SQL Server jest oddzielnym składnikiem Windows.
Opcja „Automatycznie” wybiera z zainstalowanych lokalnie sterowników najpierw 18,
a następnie 17. Można też jawnie wskazać wersję. Każdy PC i worker wykrywają
sterownik we własnym środowisku. Wymagana jest zgodna architektura (zwykle x64).
Działająca Enova nie gwarantuje dostępności tego samego sterownika: może używać
własnego dostawcy .NET zamiast ODBC. Brak modułu w starym EXE wymaga aktualizacji
Biuro, a brak systemowego sterownika wymaga instalacji Microsoft ODBC Driver
na komputerze wykonującym połączenie. TLS i sprawdzanie certyfikatu pozostają
włączone dla obu wersji.
