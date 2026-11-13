Najkrótsza instrukcja gotowej instalacji EXE: [worker-install.md](worker-install.md).

# Worker Biuro i wspólny klucz szyfrowania

Worker jest osobnym procesem działającym bez okna Biuro i bez zalogowanego
użytkownika Windows. Uruchom **jeden worker na biuro / bazę PostgreSQL**.
Wszystkie komputery nadal mogą otwierać program. Ustawienia usług, konta poczty,
reguły przypomnień, mapowania Enova i kolejka faktur są odczytywane ze wspólnej bazy.
Worker odświeża własne przełączniki i odstępy co kilka sekund.

## Co obsługuje

- Przyrostowe sprawdzanie nowych wiadomości IMAP i pobieranie ostatnich wiadomości.
  Używa istniejących reguł przetwarzania poczty, w tym Saldeo. Pełne pobieranie
  dwuletniej historii pozostaje oddzielną świadomą akcją w programie.
- Przypomnienia o dokumentach i płatnościach zgodnie z „Automatyczne e-maile”,
  z konta systemowego. Wyłączone reguły nie wysyłają wiadomości.
- Potwierdzona w programie kolejka faktur PDF: sprawdzanie przyjęcia w KSeF,
  pobranie XML, wygenerowanie PDF i wysyłka z wybranego konta do zatwierdzonych
  odbiorców. Uprawnienia zapisanego użytkownika są sprawdzane ponownie.
  Worker nie wystawia sam nowych faktur ani nie wysyła niezatwierdzonych roboczych
  faktur do KSeF. Wysyłki SMTP o niepewnym wyniku wymagają sprawdzenia przez człowieka.
- Enova raz dziennie po ustawionej godzinie (Europe/Warsaw); błędy są ponawiane
  według odstępu workera. Zachowana jest istniejąca reguła etapu „Wysłane wyniki”.
  Importer nie zmienia etapu ani ręcznych liczników. Suma automatyczna trafia do
  `Ilość dokumentów` w Widoku miesięcznym i jest odświeżana, dopóki nie zostanie
  ręcznie zmieniona. Ręczna edycja (także zero) chroni licznik. Starych ręcznych zer
  sprzed tej funkcji nie można odróżnić od domyślnych pustych liczników; wpisz takie
  zero ponownie w Widoku miesięcznym, aby jawnie chronić miesiąc.
- Opcjonalny import wystawionych faktur z KSeF dla bieżącego i poprzedniego miesiąca.
  Korzysta z istniejących ustawień KSeF.

Zadania działają niezależnie: wolny IMAP nie zatrzymuje przypomnień ani Enova.
Blokada PostgreSQL uniemożliwia uruchomienie dwóch workerów tej samej bazy/schematu.
Istniejące blokady IMAP i rezerwacje wysyłek zabezpieczają współpracę z otwartymi
stanowiskami. Ustawienia „Worker w tle” nie instalują procesu na zdalnym serwerze.

## Instalacja z okna aplikacji — wersja 1.5.0

Użyj jednej instalacji **Biuro-Setup**. Domyślnie zaznaczony jest tylko
**Program Biuro**. Na serwerze zaznacz **Worker**; możesz wybrać oba składniki
albo sam worker. Wspólne pliki EXE są potrzebne obu składnikom, ale skróty
stanowiska i workera instalują się według wyboru. Python ani `.venv` nie są wymagane.

1. Otwórz **Biuro – konfiguracja workera** z menu Start lub po instalacji.
2. Podaj adres PostgreSQL, port, bazę, użytkownika i hasło. Wybierz istniejący
   wspólny klucz z pliku albo wygeneruj klucz **tylko dla nowego biura**.
   Zapisz prywatną kopię nowego klucza i przekaż go wszystkim stanowiskom.
   Kreator sprawdza wersję bazy i odszyfrowanie zapisanych haseł przed zapisem.
   Nie aktualizuje automatycznie schematu i nie uruchamia wysyłek podczas testu.
3. Zaloguj się jako administrator Biuro. W zakładkach skonfiguruj i zapisz usługi:
   pocztę, konto systemowe, przypomnienia, e-ZLA, odczyt z Enovy i KSeF.
   Wszystkie korzystają z tych samych ustawień PostgreSQL co stanowiska pracowników.
   Dla Enovy przypisz bazy firm; ścieżki zapytań SQL muszą istnieć na serwerze.
4. W **Zadania** włącz worker i potrzebne zadania, ustaw odstępy i zapisz.
5. W **Instalacja** wybierz **Zainstaluj autostart i uruchom worker**. UAC musi
   użyć tego samego konta Windows co kreator. W oknie Biuro podaj hasło konta
   Windows (nie PIN ani hasło Biuro) i kliknij instalację. Nie otwierasz skryptów.
6. Sprawdź świeży sygnał w **Panel i logi**. Skrót **Biuro Worker** otwiera panel
   i konfigurację po zalogowaniu administratora. Możesz zamknąć wszystkie okna;
   zadanie `Biuro-Background-Worker` działa po restarcie i bez zalogowania Windows.

Okno autostartu ma również przyciski restartu i zatrzymania. Istniejące zadanie
tej samej instalacji/konta można uruchomić ponownie; obce zadanie nie jest
nadpisywane. Po zmianie lokalnego połączenia lub klucza uruchom worker ponownie.
Przed zmianą bazy zatrzymaj stary worker, żeby nie pozostawić go na starej bazie.
Przy aktualizacji plików działającego workera zatrzymaj go i po instalacji uruchom ponownie.

Lokalna konfiguracja workera jest oddzielna od desktopowej `.env`:
`%LOCALAPPDATA%\Biuro\worker.env`. Ma pierwszeństwo nad odziedziczonymi
`DB_URL` i `BIO_ENCRYPTION_KEY` przy uruchomieniu workera/panelu. Plik jest
ograniczony uprawnieniami NTFS do konta i SYSTEM. Hasło Windows jest przekazywane
Harmonogramowi przez anonimowy stdin ukrytego procesu PowerShell; nie trafia do
argumentów procesu ani do pliku. Rejestracja/żądanie startu nie oznaczają jeszcze
poprawnego działania — potwierdzeniem jest aktualny sygnał i wyniki zadań.

**Zakres:** pobieranie e-ZLA z usługi ZUS do Biuro, poczta, zatwierdzone wysyłki,
przypomnienia, odczyt Enovy i skonfigurowany KSeF. Import **e-ZLA do Enovy**
pozostaje niepodłączony. Deklaracje/UPO/zdarzenia kadrowe wymagają zapytań
pasujących do Waszej bazy Enovy. Konektor SQL Płatnika i automatyzacja podpisu
nie są częścią instalatora. ZUS wymaga dostępu HTTPS do portu 8500; poczta,
PostgreSQL, Enova SQL i KSeF muszą być dostępne z serwera.

Poniższe instrukcje źródłowe i skryptowe są alternatywą dla administratorów.

## Uruchomienie na Windows Server ze źródeł

1. Skopiuj aktualne źródła do np. `C:\BiuroWorker`. Zainstaluj Python 3.14+
   i Microsoft ODBC Driver 18 for SQL Server.
2. Utwórz środowisko i zainstaluj zależności (worker PDF potrzebuje bibliotek Qt,
   ale nie wyświetla okien):

   ```powershell
   py -3.14 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock -r requirements-enova.lock
   ```

3. W lokalnym zabezpieczonym `.env` skonfiguruj `DB_URL` tej samej bazy Biuro
   i **ten sam** `BIO_ENCRYPTION_KEY` co na działających stanowiskach. Klucz musi
   być dostępny kontu Windows zadania. Zaktualizuj schemat przez program / migracje.
4. W programie otwórz **Ustawienia → System → Worker w tle**. Włącz worker oraz
   wybrane zadania, ustaw odstępy i godzinę Enova. Konta e-mail, konto systemowe,
   reguły przypomnień, KSeF i Enova konfiguruj w ich dotychczasowych ustawieniach.
5. Sprawdź uruchomienie na serwerze:

   ```powershell
   .\.venv\Scripts\python.exe -m app.worker --once
   ```

   To wykonuje prawdziwy przebieg włączonych zadań, w tym zatwierdzone wysyłki.
   Nie włączaj wysyłek tylko w celu sprawdzenia instalacji. Test połączenia Enova
   w ustawieniach sprawdza samą bazę, bez importu.
6. Zarejestruj worker uruchamiany przy starcie serwera i po awarii:

   ```powershell
   .\scripts\install_background_worker.cmd
   ```

   Skrypt pyta lokalnie o konto Windows, uruchamia zadanie natychmiast,
   konfiguruje brak limitu czasu i ponowienie po awarii. Istniejącego zadania
   nie nadpisuje. Zadanie działa także bez interaktywnego logowania.
   Alternatywnie uruchom stale `python -m app.worker` przez własny menedżer usług.
   Zaktualizowany plik EXE obsługuje również `Biuro.exe --worker` oraz
   `Biuro.exe --worker --once`; dla EXE skonfiguruj zadanie uruchamiające go
   z argumentem `--worker`, z właściwym `.env` konta zadania.
7. Sprawdź stan i ostatni sygnał w „Worker w tle”, a na serwerze log `logs/biuro.log`.
   Szczegóły importów są w audycie, a szczegóły kolejki e-mail przy fakturach.
   Stan błędu jednego zadania nie zatrzymuje pozostałych.

Serwer musi pozostawać włączony i mieć dostęp do PostgreSQL, SQL Server Enova,
IMAP, SMTP oraz wybranego środowiska KSeF. Nie pozostawiaj drugiego codziennego
zadania Enova po przełączeniu na wspólnego workera; wyłącz je w Harmonogramie.
Aktualizacja programu/serwera wymaga aktualizacji plików workera i jego restartu.

## Naprawa haseł poczty na różnych komputerach

Poprzednia wersja tworzyła osobny klucz przy pierwszym uruchomieniu każdego PC.
Hasło zapisane w PostgreSQL było wtedy widoczne na drugim PC, lecz nie dawało się
odszyfrować. Błąd odszyfrowania był ukrywany jako pusty tekst. Nowa wersja nie
wymyśla klucza automatycznie i zwraca jasny błąd niezgodnego klucza.

1. Na komputerze, na którym hasło działa, administrator otwiera
   **Ustawienia → System → Klucz szyfrowania → Eksportuj obecny klucz do pliku**.
2. Przenieś plik klucza bezpiecznym kanałem na drugi komputer i serwer workera.
   Nie publikuj go, nie wpisuj w czacie ani nie zapisuj we wspólnej bazie z hasłami.
3. Na drugim komputerze użyj **Importuj klucz z pliku**, a następnie uruchom
   ponownie program. Odcisk klucza widoczny w ustawieniach powinien być taki sam
   na wszystkich stanowiskach. Worker wymaga tego samego klucza w swoim `.env`
   lub środowisku konta zadania; po zmianie uruchom go ponownie.
4. Jeżeli hasła były zapisywane na kilku PC z różnymi kluczami, część wpisów może
   nadal wymagać ponownego podania hasła po ujednoliceniu kluczy. Zmiana klucza
   nie odzyskuje automatycznie danych zaszyfrowanych innym kluczem. Zachowaj kopie
   starych kluczy do czasu sprawdzenia wszystkich kont i pozostałych integracji.

Dla zupełnie nowego biura można jawnie utworzyć jeden nowy klucz w tej zakładce,
a następnie przekazać go wszystkim stanowiskom. Nie twórz nowego klucza w celu
naprawienia już zaszyfrowanych danych. Klucz chroni również zapisane poświadczenia
Enova, KSeF i kalendarzy; nie należy go zmieniać przy zwykłej zmianie hasła poczty.
Pliki klucza i `.env` ogranicz uprawnieniami NTFS do właściwych kont Windows.

Jeśli `BIO_ENCRYPTION_KEY` jest również ustawiony jako zmienna środowiskowa Windows,
ma pierwszeństwo nad `.env`. Ujednolić trzeba także tę zmienną dla konta zadania
i pozostałych stanowisk, a następnie uruchomić ich procesy ponownie.

## Instalacja workera z gotowego EXE i widoczne błędy skryptu

Na serwerze można używać gotowej instalacji Biuro — Python i `.venv` nie są wtedy
potrzebne. Instalator Biuro umieszcza pomocnicze pliki w podfolderze `scripts`
oraz dodaje skrót „Biuro - instalacja workera”.

1. Zaloguj się na konto Windows, na którym skonfigurowano Biuro, wspólną bazę
   i klucz. Wybierz to samo konto w późniejszym oknie poświadczeń zadania, chyba
   że inne konto ma już osobno przygotowaną konfigurację i dostęp do plików.
2. Uruchom `scripts\install_background_worker.cmd` przez „Uruchom jako administrator”.
   Okno pozostaje otwarte również przy błędzie zasad wykonywania PowerShell
   lub błędzie składni. Skrypt nie zmienia zasad wykonywania PowerShell.
3. Można też otworzyć PowerShell jako administrator i wykonać:

   ```powershell
   & "C:\sciezka\scripts\install_background_worker.ps1" -ExecutablePath "C:\sciezka\Biuro.exe"
   ```

Skrypt wykrywa EXE w folderze instalacji, środowisko źródłowe `.venv`, wynik
PyInstaller `dist\Biuro`, a następnie domyślną instalację użytkownika. Jawne
`-ExecutablePath` ma pierwszeństwo. Brak środowiska nie jest już opisywany jako
obowiązek instalacji Pythona przy korzystaniu z gotowego EXE.

Błędy są zapisywane do `%LOCALAPPDATA%\Biuro\logs\worker-install.log` konta
uruchamiającego instalator. Interaktywne uruchomienie zatrzymuje okno do naciśnięcia
Enter. Hasło konta zadania jest pytane lokalnie i nie trafia do logu. Istniejące
zadanie nie jest nadpisywane. `-NoStart` rejestruje zadanie bez natychmiastowego
uruchomienia. `-CheckOnly -NonInteractive` sprawdza wyłącznie ścieżki: nie rejestruje
zadania, nie prosi o hasło i nie uruchamia wysyłek ani importów.

Po rejestracji sprawdź sygnał w „Worker w tle”. Samo zarejestrowanie zadania i
żądanie uruchomienia nie potwierdzają poprawnego działania procesu. Jeśli brak
sygnału, odczytaj wynik zadania w Harmonogramie oraz `Biuro\logs\biuro.log`
w profilu **konta zadania**. Nowa wersja zapisuje tam także błędy startu, np. brak
wspólnego klucza szyfrowania. Dla źródeł log i `.env` znajdują się w projekcie;
dla EXE konfiguracja konta zadania jest w `%LOCALAPPDATA%\Biuro\.env`.

## PostgreSQL działa, ale klucz nie odszyfrowuje haseł

Ten komunikat pojawia się po nawiązaniu połączenia z bazą; nie oznacza złego hasła
PostgreSQL. Identyczny klucz na PC i serwerze nie naprawia danych zapisanych wcześniej
innym kluczem. Zapisanie/import klucza nie obraca szyfrowania istniejących sekretów.

Kreator pokazuje liczbę nieczytelnych rekordów, nazwy kont/usług i odcisk sprawdzanego
klucza (nigdy klucz ani zaszyfrowane hasła). Porównaj odcisk z działającym PC. Nie
usuwaj plików klucza i nie generuj kolejnego, żeby naprawić stare hasła.

Po poprawnym połączeniu i nieudanym odczycie sekretów można jawnie potwierdzić zapis
konfiguracji lokalnej, zalogować się jako administrator Biuro i ponownie wpisać hasła
wskazanych kont/usług. Zapis nowego hasła zaszyfruje je aktualnym wspólnym kluczem.
Sekretów w bazie nie usuwamy ani nie nadpisujemy automatycznie. Certyfikaty/tokeny
mogą wymagać ponownego importu/autoryzacji. Do tego czasu instalacja/start przez
kreator nadal wymaga pomyślnego sprawdzenia sekretów.

Edytor klucza w trybie workera zapisuje do istniejącego `worker.env`, a nie do
nieużywanej w tym trybie `.env`. Jawny import w trybie desktopowym zapisuje znacznik
`BIO_LOCAL_KEY_OVERRIDE=1`; wybrany w aplikacji klucz ma wtedy pierwszeństwo nad
odziedziczoną zmienną klucza po restarcie. Inne zmienne, w tym DB_URL, zachowują
wcześniejsze reguły pierwszeństwa. Bez jawnego wyboru znacznika nadal obowiązuje
pierwszeństwo zmiennych środowiskowych opisane wyżej.


## Bieżące logi (1.5.0)

W panelu workera otwórz **Logi**. Zdarzenia odświeżają się co dwie sekundy,
również z innego komputera niż serwer workera. Wybierz zadanie (np. e-ZLA) i
pozostaw włączone **Śledź nowe zdarzenia**, aby widok przewijał się na koniec.
Wyłączenie śledzenia pozwala przeczytać starsze wpisy bez przewijania przy odświeżaniu.
Dla e-ZLA widać firmę, rozpoczęcie pobierania, zapis, wynik z liczbą raportów,
błąd lub przerwę; Enova i formularze Płatnika zgłaszają odczyt per firma.
Zakończenie pobierania e-ZLA oznacza zapis w Biuro, nie import do Enovy.

Historia zachowuje ostatnie 500 zdarzeń postępu. Zdarzenia zapisują się niezależnie
od transakcji importu, więc pozostają widoczne podczas długiego pobierania i po
błędzie. Pełniejsze logi rotacyjne są w katalogu danych workera, w `logs/biuro.log`.
Brak aktualnego sygnału w panelu oznacza ostatni zapisany stan, nie aktywny postęp.
Zadania wyłączone, oczekujące na godzinę lub sprawdzone dziś mają stan
„Bez pobierania”, zamiast udawać zakończone pobranie.
