# Codzienna liczba dokumentów z Enova365

Importer jest przeznaczony dla Windows Server z dostępem do PostgreSQL aplikacji
oraz do SQL Server Enova w innej maszynie Hyper-V. Działa bez otwartej aplikacji.
Każda firma ma osobną bazę Enova; mapowanie do Biuro odbywa się po NIP.

## Stan integracji

Worker używa `queries/monthly-documents.sql` do licznika miesięcznego i do
raportu dokumentów w Sprawdzaniu. Starsze pliki SQL pozostają dostępne dla
konfiguracji wskazujących je jawnie.

- **KPiR** liczy numerowane zapisy `Dziennik.KPiRLp > 0` według `Dziennik.Data`,
  z definicjami powiązanych dokumentów. Dolicza zatwierdzoną DRA z Enovy. PIT-5,
  PIT-5L oraz deklaracja VAT są pomijane. Raport grupuje sprzedaż, zakupy i DW
  (dokumenty wewnętrzne oraz pozostałe zapisy, w tym DRA).
- **KH** liczy wpisy `DokEwidencja` według `DataEwidencji` oraz zatwierdzone
  deklaracje CIT-2, CIT-8, CIT-8E i VAT za dany okres. Deklaracja i jej korekty
  w tym samym okresie liczą się raz. JPK z częścią deklaracyjną wysłane na
  bramkę produkcyjną uzupełnia VAT. Warunkiem jest rozpoczęcie przetwarzania w MF.
- **Ryczałt** liczy sprzedaż u firm bez VAT oraz sprzedaż i zakupy u czynnych
  podatników VAT. Do rozpoznania rodzajów służą typy definicji Enovy i znane
  symbole, m.in. SPR oraz ZP. Przy nieustalonym statusie VAT importer zachowuje
  poprzedni wynik. Raport pokazuje Sprzedaż, Zakup, DW i Razem.

W ewidencji środków trwałych odpis AMO jest widoczny osobno, a OT i LT są
pomijane. Podtypy bierze się z `DokumentyST` (OT=1301, LT=1302, AMO=1303).
W KH dzielnik obejmuje pozycje WBEW i raportów kasowych, a w KPiR pozycje
kasowe. RKEW i RKVE powiązane z `RaportyESP` liczą ich pozycje `Zaplaty`.
Pojedynczy wpis RKVE bez powiązania z raportem wnosi jedną pozycję kasową.
Worker używa dotychczasowego dzielnika globalnego lub właściwego firmie
i zaokrągla raz po zsumowaniu miesiąca. Powielone powiązanie z raportem nie
powiela jego pozycji.

Zapytanie zwraca pełny zakres miesięcy, także zera. Worker weryfikuje NIP
z `HstDanychFirmy` i przerywa zapis w razie sprzecznego NIP-u, brakującego
okresu albo niespójnego powiązania raportu lub STE. „Razem” w Sprawdzaniu
pokazuje wynik reguł odczytu. Licznik Widoku miesięcznego nadal chroni ręczne
korekty i dane pochodzące z innych importów. Po aktualizacji workera odczytaj
ponownie wybrany okres, żeby zobaczyć nowe wyniki.

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

Używaj wspólnego workera: `scripts/install_background_worker.cmd`, uruchomionego
jako administrator na serwerze PostgreSQL. Godzinę Enova i pozostałe zadania
ustaw w **Ustawienia → Worker w tle**. Instrukcja instalacji gotowego EXE:
[worker-install.md](../worker-install.md).

Osobny instalator codziennego zadania Enova i jego wrapper PowerShell zostały
usunięte. Do ręcznej diagnostyki ze źródeł pozostaje bezpośrednie polecenie
`python -m app.integrations.enova.document_counts --saved-settings` (podgląd)
lub z `--apply` (zapis). Nie trzeba tworzyć drugiego zadania harmonogramu.
Jeżeli istnieje stare zadanie `Biuro-Enova-Daily`, wyłącz je po potwierdzeniu,
że nowy worker działa. Aktualizacja plików nie usuwa istniejących zadań Windows.

Błędy są w audycie importów, a log wspólnego workera w `logs/biuro.log` konta
zadania. Sprawdź sygnał działania w „Worker w tle”.

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
przyjmuje `IP\INSTANCJA`, `host,port` lub `tcp:host,port` dla zdalnego serwera.
Adres musi być osiągalny z serwera Biuro i hosta workera, a nazwa serwera powinna
odpowiadać certyfikatowi SQL Server. Login SQL powinien mieć wyłącznie prawa SELECT.
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
.\scripts\install_background_worker.cmd
```

Launcher pyta lokalnie o konto Windows zadania i nie nadpisuje istniejącego zadania.
Godzinę importu ustaw w „Worker w tle” (Europe/Warsaw). Wymaga odpowiednich uprawnień Windows;
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

Domyślny odczyt `queries/jpk.sql` jest dołączony do workera i wybierany
automatycznie, bez wskazywania pliku. Obejmuje JPK VAT/V7M/V7K z `JednolitePK`;
szczegóły odczytu i ograniczenia opisano poniżej. Pole „Zapytanie statusu JPK”
pozwala go zastąpić własnym SQL. JSON obsługuje `jpk_query` globalnie i w `sources`.

Zapytanie JPK ma dwa parametry ODBC `?`: początek zakresu włącznie i koniec
wyłącznie. Zwraca dokładnie jeden wiersz na miesiąc z kolumnami `nip`, `miesiac`
(`YYYY-MM`), `status` oraz `upo` (referencja potwierdzenia, NULL dopuszczalny poza
`accepted`). Dozwolone statusy: `not_sent`, `sent`, `accepted`, `rejected`,
`not_applicable`, `prepared`, `processing`, `unknown`, `failed`, `cancelled`. Opcjonalne `raw_status`
zachowuje kod źródła. Opcjonalne `sent_at` i `upo_at` przyjmują wartości ISO lub
NULL. Brak wiersza/NULL status/niezgodny NIP/duplikat to błąd;
nie są zamieniane na „niewysłany”. Bez prawdziwego źródła nie można potwierdzić
połączenia z JPK ani przetestować go w tej sesji.

Dostęp do nowej zakładki można nadać w ustawieniach roli (role z domyślnymi
zakładkami otrzymują ją automatycznie).

## Test połączenia i praca niezależna

Przycisk **Sprawdź połączenie z bazą Enova** w ustawieniach korzysta z aktualnie
wpisanego serwera, loginu, hasła i nazw baz, bez konieczności zapisywania ustawień.
Sprawdza logowanie i dostęp do każdej wskazanej bazy (`SELECT DB_NAME()`), nie
uruchamia importu, nie zmienia liczników ani statusów. Puste hasło wykorzystuje
zapisane hasło. Test połączenia wykonywany z aplikacji trafia do serwera Biuro.
Zaplanowany import łączy się z hosta workera. Jeśli są to różne maszyny, obie
muszą mieć dostęp sieciowy do SQL Server Enova.

Na Windows Server z dostępem do PostgreSQL Biuro i SQL Server Enova zainstaluj
**worker ze źródeł**, Python 3.14+, zależności `requirements-enova.lock` i Microsoft
ODBC Driver 18. Nie trzeba instalować ani pozostawiać otwartego okna Biuro.
Skonfiguruj `DB_URL` i wspólny `BIO_ENCRYPTION_KEY` w środowisku konta zadania
lub zabezpieczonym lokalnym `.env`. Ustawienia zapisane w aplikacji są we wspólnej
bazie PostgreSQL. Na serwerze uruchom podgląd `--saved-settings`, a po weryfikacji
zarejestruj wspólny worker przez `scripts/install_background_worker.cmd`.
Zadanie uruchamia worker raz dziennie, także bez zalogowanego użytkownika,
a proces kończy się po imporcie. Serwer musi być włączony w czasie pracy zadania;
ustawienie StartWhenAvailable nadrabia pominięty termin. W Harmonogramie zadań
sprawdź ręcznie pierwsze uruchomienie pod docelowym kontem, wynik i logi `logs/enova`.

### Test połączenia i sterownik

Przycisk „Sprawdź połączenie z bazą Enova” wykonuje test na **serwerze Biuro**
przez API, a nie na komputerze użytkownika. Import cykliczny działa na hoście
workera. Każdy host wykonujący połączenie musi mieć zgodny sterownik ODBC oraz
dostęp sieciowy do SQL Server Enova.

Gdy Enova SQL Server działa na osobnej maszynie, wpisz nazwę DNS lub IP widoczne
z serwera Biuro i workera. Dla statycznego portu TCP użyj na przykład
`tcp:enova.firma.local,1433` (albo `tcp:192.168.1.20,1433`). Włącz TCP/IP w
konfiguracji SQL Server i zezwól na ten port w zaporze. Nazwa z certyfikatu TLS
powinna odpowiadać użytej nazwie DNS; opcja zaufania własnemu certyfikatowi
wyłącza jego weryfikację, ale nie szyfrowanie.

Wystarczą serwer, login i hasło; bez mapowań firm test sprawdza domyślną bazę
konta. Po wpisaniu baz firm sprawdza również dostęp do każdej z nich.

Windowsowy instalator Biuro zawiera moduł Python `pyodbc`. Sterownik systemowy
Microsoft ODBC Driver for SQL Server jest oddzielnym składnikiem Windows.
Opcja „Automatycznie” wybiera zainstalowane na hoście wykonującym operację
sterowniki — najpierw 18, a następnie 17. Można też jawnie wskazać wersję.
Serwer Biuro i worker wykrywają sterownik we własnym środowisku; instalacja na
komputerze użytkownika nie zastępuje instalacji na tych hostach. Wymagana jest
zgodna architektura (zwykle x64).
Działająca Enova nie gwarantuje dostępności tego samego sterownika: może używać
własnego dostawcy .NET zamiast ODBC. Brak modułu w starym EXE wymaga aktualizacji
Biuro, a brak systemowego sterownika wymaga instalacji Microsoft ODBC Driver
na hoście wykonującym połączenie. TLS i sprawdzanie certyfikatu pozostają
włączone dla obu wersji.

## Księgowość i Kadry — Widok miesięczny oraz Sprawdzanie

Menu ma osobne działy **Księgowość** i **Kadry**. Każdy zawiera podzakładki
**Widok miesięczny** i **Sprawdzanie**. Dotychczasowe uprawnienia modułów są
zachowane: dostęp do Widoku miesięcznego nie przyznaje dostępu do Sprawdzania
ani odwrotnie. Stary zapis roli „Księgowość” nadal oznacza dawny moduł sprawdzania.
W Sprawdzaniu wybiera się raport dla danego działu i miesiąc. Dostępne są
wyszukiwarka firmy/NIP oraz filtry KPiR, Ryczałt i Księgi rachunkowe.

Każdy rodzaj dokumentu jest osobną kolumną. Dla księgowości SPU pokazuje się jako
„Sprzedaż”, ZR jako „Zakup”; DW, AMO i pozostałe symbole pozostają osobno. Nie
łączy się różnych definicji w jedną kolumnę. Kolumny są uzupełniane na podstawie
aktualnych danych i historii widocznych firm. Zero jest czerwone wyłącznie,
gdy ten rodzaj wystąpił w **tej samej firmie w poprzednim miesiącu księgowym**.
Kadry nie mają porównań ani czerwonych zer wynikających z historii. Brak odczytu oznacza „—”, a nie zero. Nie ocenia się, czy brak
AMO/DW jest błędem księgowania — kolor jest sygnałem do sprawdzenia.

Odczyt Sprawdzania jest niezależny od rozliczenia faktury i etapu „Wysłane wyniki”.
Dołączone `queries/checking.sql` liczy wszystkie definicje `DokEwidencja` według
`DataEwidencji`, w tym DW/AMO oraz pozostałe definicje wyłączane przez reguły
cennika ryczałtu. Nie przelicza pozycji WBEW na ekwiwalent dokumentów; tutaj
pokazywane są nagłówki danego rodzaju. Worker zapisuje skonfigurowane miesiące
oraz 12 miesięcy historii (maksymalnie 36 miesięcy). Nie zmienia etapów ani ręcznych
liczników w Widoku miesięcznym. Do faktury nadal trafia dotychczasowa suma według
reguł księgowych i ochrony ręcznych korekt. Stare konfiguracje JSON mogą włączyć
odczyt przez `checking_enabled=true`; ustawienia zapisane w aplikacji mają ten
odczyt włączony razem z integracją Enova.

### Własny certyfikat SQL Server

W **Enova — połączenie** zaznacz **Ufaj certyfikatowi serwera (własny certyfikat)**,
aby używać `Encrypt=yes;TrustServerCertificate=yes`. Test korzysta z bieżącego
stanu pola; po zapisaniu ustawienia użyje go także worker. Domyślnie opcja jest
wyłączona. Szyfrowanie pozostaje włączone, ale zaznaczenie pomija sprawdzenie
łańcucha certyfikatu i tożsamości serwera. JSON obsługuje logiczne pole
`trust_server_certificate` (nie tekst „true”/„false”).

### Kadry i wszystkie deklaracje elektroniczne z UPO

Kadry przewidują osobne rodzaje dokumentów kadrowo-płacowych i formularze PIT/ZUS.
Oba działy pokazują formularze osobno: status, **data wysyłki**, obecność UPO i
**data UPO**. Wyświetlane są JPK_VAT/CIT-8 oraz PIT-11/PIT-4R/ZUS DRA jako punkty
startowe, a każdy dodatkowy rodzaj zwrócony przez źródło dostaje własne kolumny.
Brak danych nie oznacza „Niewysłany” ani „Nie dotyczy”. Przyjęcie wymaga jawnego
statusu i potwierdzenia UPO; data pobrania nigdy nie zastępuje daty wysyłki/UPO.
Starszy odczyt JPK bez dat nadal jest obsługiwany, z pustymi datami.

**Listy płac i wypłaty mają wbudowany adapter `queries/payroll.sql`.** Jest
dołączony do aplikacji i workera przez instalator oraz automatycznie wybierany
przy odczycie zapisanych ustawień i konfiguracji JSON. Nie trzeba wskazywać pliku.
Istniejące ustawienia również otrzymują ten adapter po aktualizacji workera.
Liczy nagłówki `ListyPlac` i osobne dokumenty `Wyplaty` według ich pola `Data`,
grupując je według symbolu definicji. Bufor wypłat jest pokazywany jako osobny
rodzaj, a nie odrzucany. Lista płac i wypłaty to osobne rodzaje dokumentów,
nie liczba osób; ich suma nie oznacza liczby pracowników. Okres nie jest
wyznaczany przez datę zapłaty, miesiąc ZUS ani okres naliczenia.
Pusty miesiąc ma jawne zero. Brak lub wiele NIP-ów właściciela powoduje błąd.
Schemat tabel i połączeń jest wspólny dla dostarczonych wariantów księgowości;
wynik należy porównać z Enovą dla wybranego miesiąca.

Własny plik `payroll_query` jest opcjonalnym zamiennikiem. Pole w ustawieniach
może pozostać puste — wtedy działa adapter wbudowany. Zamiennik konkretnej firmy
w `sources` ma pierwszeństwo przed zamiennikiem globalnym. Względne ścieżki
zamienników są liczone od konfiguracji JSON (albo `docs/enova` dla ustawień
zapisanych w aplikacji). Wbudowany plik jest wyszukiwany w instalacji, niezależnie
od położenia JSON. Własny SQL przechowuj poza katalogiem instalacyjnym, aby
aktualizacja go nie nadpisała. Księgowość nadal dobiera wbudowane zapytanie do formy
opodatkowania.

**Deklaracje podatkowe i JPK VAT mają teraz wbudowane zapytania.**
`queries/declarations.sql` czyta VAT-7/7K/7D (wspólny raport VAT), VAT-UE/UEK,
VAT-8, VAT-9M, VAT-12, VAT-27, zaliczki PIT/CIT i CIT-8 z `Deklaracje`,
`EDeklaracje` i `ETransmisje`. Kod formularza XML ma pierwszeństwo przed symbolem
w definicji. Formularze kwartalne/roczne należą do miesiąca końca okresu.
Najnowszy numer deklaracji i ID wygrywają w obrębie formularza/miesiąca,
także gdy starsza deklaracja miała UPO, a nowa korekta jeszcze go nie ma.

Znaczenie relacji i pól transmisji potwierdza opublikowana przez producenta
[definicja bazy Soneta](https://github.com/soneta/EnovaDB/blob/master/EnovaDB/Generator/Deklaracje.business.xml).
Odczyt używa kodów odpowiedzi MF: 200 z pobraną referencją UPO oznacza przyjęcie,
301–304 przetwarzanie, 400–499 odrzucenie. Kody wewnętrzne zmapowano na podstawie opublikowanych stałych enumów Sonety;
[pełna tabela i źródła](status-codes.md). Surowe wartości są nadal zachowywane. [Opis kodów MF](https://podatki.gov.pl/e-deklaracje-zloz-deklaracje-podatkowa-online/informacje-podstawowe).
Brak elektronicznej wersji deklaracji oznacza przygotowanie; kody Enovy rozdzielają
przygotowanie, oczekiwanie na wysłanie i na UPO, błędy oraz anulowanie. Nieznane
kody pozostają nieustalone. Anulowana deklaracja nie staje się przyjęta ze starego UPO. Data UPO pochodzi wyłącznie z elementu
`DataWytworzeniaPotwierdzenia`; data wysyłki pozostaje pusta, ponieważ data próby
transmisji nie potwierdza złożenia. `Stamp` to wersja wiersza, nie data.

`queries/jpk.sql` odczytuje JPK VAT/V7M/V7K z `JednolitePK` (nazwy i typy kolumn
potwierdzone we wszystkich czterech lokalnych eksportach). Wybiera najwyższy numer
korekty, następnie ID. Z kolumny UPO pobiera referencję i daty bez eksportowania XML
ani kwot. W kolumnie „Data wysyłki” pokazuje `DataWplyniecia`, czyli udokumentowany
czas wpłynięcia pliku do MF. „Data UPO” pochodzi z czasu wytworzenia potwierdzenia
albo z `SigningTime` podpisu XAdES na UPO. Gdy brakuje odpowiedniego pola w źródle,
data pozostaje pusta. Przyjęcie wymaga statusu Finished=100, oficjalnej bramki=3 i referencji UPO.
Testowe UPO nie potwierdza złożenia produkcyjnego. Statusy przygotowania,
wysyłania, przetwarzania i błędu mapujemy według potwierdzonych enumów;
nieznane kody, brak pliku i zakończenie bez UPO pozostają nieustalone.
Nie obejmuje JPK CIT/KR/FA/MAG ani plików zewnętrznych nieobecnych w tej bazie.

SQL jest instalowany razem z workerem i działa także dla zapisanych wcześniej
konfiguracji. Puste pola deklaracji/JPK przywracają domyślne odczyty. Własny plik
SQL można podać globalnie lub dla źródła; zastępuje odpowiedni odczyt wbudowany.
Dla firm ze zmienionymi symbolami, niestandardowym XML UPO lub inną kolejnością
korekt służy `profile-declarations.sql` i opcjonalny zamiennik SQL. Schematy JSON
pozostają poza repozytorium. Zapytania nie były uruchamiane na bazach klientów.
Indywidualne deklaracje pracowników mają osobny kontrakt odczytu. Wbudowane
zapytanie terminów kadrowych korzysta z rejestrów modułu Kadry Płace.

Kontrakty opcjonalnych zamienników SQL:

- `payroll_query` / „Dokumenty kadrowe”: dwa parametry dat (początek włącznie,
  koniec wyłącznie), kolumny `nip`, `miesiac` (`YYYY-MM`), `rodzaj`, `liczba`.
  Jeden wiersz na rodzaj/miesiąc. Miesiąc bez dokumentów musi zwrócić jawny wiersz
  `rodzaj=NULL, liczba=0`; brak miesiąca jest błędem i zachowuje stare dane.
- `declarations_query` / „Deklaracje i UPO”: te same dwa parametry dat, kolumny
  `nip`, `miesiac`, `department` (`accounting` albo `payroll`), `form` (np. JPK_VAT,
  CIT-8, PIT-11, ZUS DRA), `status`, `sent_at`, `upo` (referencja potwierdzenia),
  `upo_at`. Statusy: `prepared`, `processing`, `not_sent`, `sent`, `accepted`,
  `rejected`, `not_applicable`, `unknown`, `failed`, `cancelled`. Opcjonalne `raw_status` zachowuje kod źródła. Dla `accepted` UPO nie może być puste. Daty mogą
  być NULL, datą ISO lub znacznikiem czasu ISO; nie są dopowiadane przez importer.
  Zapytanie musi wybierać aktualny stan formularza, uwzględniając korekty; duplikat
  formularza/działu/miesiąca/pracownika jest błędem. Przypisanie formularzy rocznych lub
  kwartalnych do okresu miesięcznego musi być jawne w zapytaniu, zgodne z zasadami
  biura. Brak wiersza pozostawia „Brak danych”, nie tworzy fałszywego statusu.

Zamienniki zapytań mogą być globalne w ustawieniach albo osobne przy `sources` w JSON.
Wszystkie wyniki muszą zawierać NIP zgodny z mapowaniem firmy. Błąd walidacji
zachowuje dotychczasowy odczyt firmy; pozostałe firmy są przetwarzane dalej.
Odczyt księgowości i kadr do Sprawdzania ma własną transakcję i blokadę, niezależną
od importu licznika rozliczeniowego.

### Sprawdzanie — poprzedni miesiąc i pracownicy

Księgowość ma zestawienie bieżący/poprzedni miesiąc, różnicę i zmianę procentową
osobno dla każdego rodzaju oraz sumy. Spadek jest wyróżniony, a zero po dodatniej
wartości w poprzednim miesiącu — czerwone. Brak odczytu pozostaje „—”; po zerowej
podstawie pojawia się „Nowe”, bez dzielenia przez zero. Nie ma porównania rok do
roku ani średnich. Kadry nie mają żadnego porównania miesięcy. Zachowana historia
służy także do przeglądania wcześniejszych okresów.

Dział wybiera się w menu głównym: **Księgowość** albo **Kadry**. Obie podzakładki
pozostają w wybranym dziale; nie ma potrzeby ponownie wybierać działu wewnątrz
Widoku miesięcznego lub Sprawdzania. Widok miesięczny korzysta z jednego edytora
i jednej instancji logiki stoperów, zachowując odrębnie wybrane miesiące działów.
Samodzielne użycie starego widoku nadal dobiera dział według roli. Nie zmienia to
uprawnień do modułów.

Tabela pokazuje firmę i przypisaną księgową/kadrową biura. Szczegóły deklaracji
pokazują osobno pracowników firmy, status, datę wysyłki, referencję UPO i datę UPO.
Filtry: do wysłania, wysłane, oczekujące na UPO, przyjęte z UPO, odrzucone.
Opcjonalne kolumny `declarations_query`: `employee_id`, `employee_name`
(obie podane albo obie puste) i `source_system` (`enova`, domyślnie, albo
`platnik`). Każdy pracownik ma osobny aktualny wiersz. Puste dane pracownika
oznaczają formularz całej firmy/zestawu. Zestawienie wielu formularzy pokazuje
liczbę przyjętych i liczbę UPO; nie nadaje wszystkim statusu jednego pracownika.
`source_system` to pochodzenie danych z zapytania, **nie gotowy osobny konektor
Płatnika**. Mapowanie tabel i statusów Płatnika nadal wymaga weryfikacji.

### Pracownicy, umowy, badania i BHP

W **Kadry → Sprawdzanie** odczyt Enovy udostępnia listę pracowników z aktywną
umową o pracę lub umową cywilnoprawną, końce umów, ważność ostatnich badań danego
rodzaju i terminy szkoleń BHP wyznaczone w Enovie. Adapter czyta aktualną historię
etatów z `PracHistorie`, umowy z `Umowy` i pozycje z `BadaniaLekarskie`
oraz `SzkoleniaBHP`. Widok BHP korzysta z terminu zaplanowanego szkolenia.
[Moduł Kadry Płace enova365](https://enova.pl/moduly-systemu/moduly/kadry-place/)

Puste pole **Umowy, badania i BHP** uruchamia wbudowane zapytanie `hr_events.sql`.
Pole `hr_events_query` przyjmuje własny plik SQL. Wymagane kolumny:
`nip`, `event_id`, `employee_id`, `employee_name`, `event_type`, `due_date`, `status`.
Opcjonalne kolumny to `document_number` i `detail`. Obsługiwane rodzaje to
`employee`, `contract_end`, `medical_exam` i `ohs_training`.
Statusy to `active`, `completed` i `cancelled`. Identyfikator jest
unikatowy w ramach firmy i rodzaju; jeden pracownik może mieć wiele umów.
Daty: ISO YYYY-MM-DD albo SQL DATE; NULL dla umowy bezterminowej. Brak terminu
badań/BHP jest sygnałem do sprawdzenia, nie umową bezterminową.

Własne zapytanie otrzymuje początek miesiąca oraz koniec okna obejmującego
najbliższe 91 dni, zaokrąglony do końca miesiąca. Wbudowany adapter otrzymuje
dzień odczytu jako pierwszy parametr. Zapytanie ma zwracać
również nadal aktywne zaległe terminy i umowy bezterminowe — dolna granica
**nie służy do odfiltrowania zaległości**. Zamknięte/anulowane zdarzenia są
pomijane w widoku. Pusta poprawna lista oznacza brak zdarzeń, brak konfiguracji
pozostaje „brak odczytu”. Terminy odnoszą się do dzisiaj w Europe/Warsaw,
nie do wybranego miesiąca dokumentów. Dostępne okna 7/30/60/90 dni i wszystkie
pobrane. Błąd odczytu kadrowego zachowuje ostatni wynik i pojawia się w informacji
nad raportem. Odczyt deklaracji i liczników może zakończyć się niezależnie.

### Wybór raportu i wszystkie firmy

W **Księgowość → Sprawdzanie** lub **Kadry → Sprawdzanie** pole **Raport**
przełącza zestawienie. Dostępne są dokumenty, deklaracje/UPO, poszczególne
formularze i — wyłącznie w księgowości — poprzedni miesiąc. Kadry mają m.in.
RCA, DRA, RSA, RPA, PIT-11, PIT-4R, umowy, badania, BHP oraz e-ZLA. Dodatkowe
formularze otrzymane ze źródła pojawiają się automatycznie; wybór przetrwa zmianę
filtrów i miesiąca. RCA oraz ZUS RCA oznaczają ten sam raport.

Raport konkretnej deklaracji pokazuje wszystkie dostępne firmy według filtrów,
a przy braku danych jawnie „Nie sprawdzono” lub „Brak danych”. Nie pokazuje
w tym miejscu innych formularzy firmy. Filtr statusu celowo zawęża tę listę.
„Tylko wymagające uwagi” dotyczy wybranego formularza, nie np. niewysłanego PIT-u
przy poprawnym RCA. Umowy/badania/BHP również pokazują firmy bez odczytu lub
bez terminów w zadanym zakresie. Brak odczytu nie oznacza niewysłanej deklaracji
ani braku pracowników.

RCA i inne deklaracje nadal wymagają skonfigurowanego źródła danych; sam wybór
raportu nie uruchamia nowego konektora Płatnika. e-ZLA zestawia raporty pobrane
z oficjalnej usługi ZUS przez worker i lokalnie wczytane XML. Dane pobrane przez
worker są zaszyfrowane w PostgreSQL, dostępne także na innych komputerach.
Konfiguracja w [ZUS — e-ZLA](../ezla-service.md). Import do Enovy nie jest wykonywany.

### Główny przycisk stopera

Gdy działa stoper firmy (księgowość lub kadry), główny przycisk ma etykietę
**Stop stopera firmy**. Kliknięcie zapisuje i zatrzymuje stopery firm, bez
uruchamiania ogólnego i bez otwierania wyboru zadania. Dopiero następne kliknięcie,
przy braku aktywnego stopera, pozwala uruchomić ogólny. Działający stoper ogólny
jest zatrzymywany tak jak dotychczas. Błąd zapisu nie uruchamia kolejnego stopera.

## Automatyczne przypisanie baz po NIP

W wersji 1.4.6 wyszukiwanie ma domyślnie **120 sekund na pojedyncze zapytanie**,
z możliwością ustawienia 30–1200 sekund w polu **Limit zapytania — wyszukiwanie baz**.
Ustawienie jest dostępne w aplikacji i panelu workera; działa także przed zapisaniem
formularza. Nie jest to limit całej operacji: odczyt 122 baz może potrwać dłużej.
Logowanie ma osobny limit 15 sekund. Postęp pokazuje liczbę sprawdzonych baz,
np. 35/122, oraz aktualnie odczytywaną bazę. Zapytania działają w tle i po kolei.

Lista baz jest odczytywana z metadanych bez wywoływania `HAS_DBACCESS` dla każdej
bazy w jednym zapytaniu. Dostęp jest weryfikowany przy osobnym połączeniu do
danej bazy. Błąd dostępu lub zapytania nadal wstrzymuje automatyczne przypisanie,
aby częściowy odczyt nie rozstrzygał niejednoznacznego NIP-u.

`HYT00` z `SQLExecDirectW` oznacza limit wykonania zapytania po połączeniu,
a nie limit logowania. Wskazówka błędu kieruje wtedy do czasu zapytania,
obciążenia SQL i blokad. Samo zwiększenie czasu nie usuwa blokad na serwerze.

W formularzu połączenia aplikacji i workera wpisz oddzielnie:

- **Serwer SQL / instancja:** `ENOVA\SQLEXPRESS`;
- **Główna baza Enovy:** `Biuro_rachunkowe`;
- login i hasło SQL oraz opcję zaufania własnemu certyfikatowi, jeśli jest potrzebna.

Główna baza jest bazą startową połączenia. Można ją sprawdzić przed uzupełnieniem
tabeli firm. Wyszukiwanie baz rozpoczyna się w niej i odczytuje dostępny katalog
`sys.databases`; nie wymusza połączenia do `master`, jeśli wskazano inną bazę.
Puste pole zachowuje dotychczasowy sposób działania. Przy wskazanej bazie program
nie próbuje potajemnie innej, jeśli logowanie lub dostęp do tej bazy zawiedzie.

Główna baza nie przypisuje się automatycznie do każdej firmy. Nazwy baz klientów
pozostają w tabeli firm i są używane przez importery. Test połączenia sprawdza
bazę główną oraz wpisane bazy klientów. Zapamiętanie ustawienia działa również
w panelu workera.

W ustawieniach połączenia (także w panelu workera) przycisk **Znajdź bazy Enovy
po NIP** odczytuje listę dostępnych baz SQL Server i pole
`dbo.OddzialyFirmy.DeklaracjeNIP`, którego używają istniejące zapytania importera.
Nie wyszukuje NIP w kontrahentach, bo każda baza może zawierać wielu kontrahentów.
Nie zapisuje danych do Enovy i nie uruchamia importu.

Przycisk uzupełnia tylko puste komórki przy jednoznacznym NIP w Biuro i jednej
rozpoznanej bazie. Ręczne przypisania są zachowane. Kopie/archiwalne bazy o tym
samym NIP są pokazane w szczegółach, bez wybierania pierwszej. Przy błędach odczytu
baz automatyczne uzupełnianie jest wstrzymane. Zmiany trzeba zapisać przyciskiem
**Zapisz połączenie Enova**. Wynik obejmuje tylko bazy widoczne dla konta SQL;
brak dostępu do metadanych lub inna struktura Enovy wymaga ręcznego mapowania.

Przy błędzie połączenia z `master` lub odczytu listy baz okno pokazuje SQLSTATE,
wskazówkę i rozwijane szczegóły sterownika. Błędy pojedynczych baz są dostępne
w szczegółach wyniku, a automatyczne przypisania nadal pozostają wstrzymane przy
niepełnym odczycie. Diagnostyka usuwa hasło i jego postać zakodowaną dla ODBC.
Połączenie w SSMS może używać innego dostawcy SQL lub ustawień TLS niż ODBC;
sam ogólny komunikat nie rozstrzyga, czy chodzi o logowanie, sieć czy metadane.

Sterowniki Microsoft ODBC 17 i 18 obsługują SQL Server 2017, 2019 i 2022.
Nie dobieraj numeru sterownika do rocznika SQL Server ani wersji SSMS.
Jeden zainstalowany ODBC Driver 18 i wybór Automatycznie może obsłużyć oba
połączenia workera — Enovę i Płatnika.


Dołączone ścieżki SQL są rozwiązywane w katalogu instalacji komputera wykonującego
odczyt. Zapis ustawień nie utrwala ścieżek domyślnych z komputera administratora
jako zamienników firmy. Wartość dziedziczona z ustawienia globalnego nadal
podlega późniejszym zmianom globalnego SQL. Rzeczywisty zamiennik firmy zachowuje
pierwszeństwo. Rewizja adapterów unieważnia dzienne znaczniki po aktualizacji SQL.

## Ręczne powiązanie przy braku NIP-u historii firmy

Dla wbudowanych adapterów worker sprawdza identyfikatory historii firmy przed
odczytem i przekazuje NIP zapisanego powiązania jako parametr sesji SQL Server
`biuro_company_nip` przez `sys.sp_set_session_context`. Dotyczy to wyłącznie
tej sesji; nie zmienia rekordów Enovy i zachowuje konto tylko do odczytu.
Zapytania nadal mają dwa parametry dat. Fallback działa tylko wtedy, gdy
kolumna `NIP` historii firmy nie dostarcza żadnego dziesięciocyfrowego NIP-u.
Gdy istnieje inny NIP, worker odrzuca import przed odczytem dokumentów.

Ręczne przypisanie bazy w Biuro jest więc używane również do odczytu danych,
a nie tylko do wyświetlenia bazy w konfiguracji. Nie trzeba zmieniać danych
właściciela w Enovie, żeby obejść puste pole historii firmy. Puste wyniki liczników
nadal są błędem, a nie automatycznymi zerami. Zamienniki SQL użytkownika nie
są przepisywane i zachowują własny kontrakt.


Dane oddziałów nie są tożsamością całej firmy. Import i wyszukiwanie baz
korzystają z `HstDanychFirmy.NIP` dla najnowszego `Termin` niepóźniejszego
niż bieżąca data serwera SQL. Nie wybierają NIP-ów z `OddzialyFirmy` ani
`HstDanychOddzial`. Historia może być pusta; wtedy działa zapisane mapowanie.
Bieżąca pieczątka w API Sonety jest dostępna przez
`session.GetCore().Config.Firma.Pieczątka.NIP` (tak korzysta z niej dodatek e-ZLA).
