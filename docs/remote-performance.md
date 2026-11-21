# Przycięcia po połączeniu z serwerem

Istniejący `service_scope` uruchamia cache na czas operacji. Cache nie jest
współdzielony między operacjami ani użytkownikami; nie zmieniamy jego zakresu.
Połączenia TCP serwera używają TCP_NODELAY, aby małe odpowiedzi HTTP/TLS nie
czekały na buforowanie Nagle'a przy osobnym wysyłaniu nagłówków i treści.
Weryfikacja certyfikatu i sesji pozostaje aktywna.

Wywołania trwające co najmniej 250 ms zapisują w standardowym `biuro.log`:

- klient: `office_request_slow`, trasę, nazwę operacji, czas i `main_thread`;
- serwer: `office_operation_slow`, nazwę operacji i czas wykonania.

Logi są w katalogu danych Biura, zwykle `%LOCALAPPDATA%\Biuro\logs`.
Wpisy nie zawierają argumentów, tokenów, adresów serwera ani danych firm.
`main_thread=True` wskazuje oczekiwanie w wątku interfejsu. Duży czas po
stronie klienta przy krótkiej operacji serwera sugeruje komunikację lub
kolejkę serwera; długi czas na serwerze wymaga sprawdzenia konkretnej
operacji i bazy. Brak wolnych wpisów nie wyklucza wielu krótkich wywołań
wykonywanych kolejno ani kosztu renderowania interfejsu.

Te zmiany usuwają rozpoznane źródła narzutu. Bez logów z instalacji nie
potwierdzają głównej przyczyny zgłoszonych przycięć ani skali poprawy.

`office_navigation_slow` opisuje całe przełączenie zakładki: indeks widoku,
czas, liczbę żądań sieciowych wykonanych synchronicznie, ich łączny czas i pięć
najdroższych nazw operacji. Uwzględnia także żądania krótsze niż 250 ms,
które mogą sumować się do długiego oczekiwania. Pomiar nie obejmuje odczytów
w tle rozpoczętych przez widok. Dużo czasu sieciowego oznacza oczekiwanie
na API; długi czas przejścia przy małym czasie sieciowym wskazuje na lokalne
budowanie/renderowanie widoku. Potrzebne są logi z komputera pracownika i
serwera z tej samej próby przełączenia.

## Interfejs po logowaniu

Odpowiedzi logowania i wznowienia sesji zawierają `ui_authorization`: role,
uprawnienia i zakładki bieżącego pracownika. Program przechowuje ten zestaw
wyłącznie w pamięci sesji i używa go do rysowania interfejsu. Zmianę widoczności
przycisków i zakładek po edycji roli pokaże ponowne logowanie. Serwer nadal
sprawdza aktualną sesję i uprawnienia przy operacjach na danych; zestaw
przechowywany przez klienta nie służy do autoryzacji po stronie serwera.
Starszy serwer bez tego pola zachowuje dotychczasowy sposób działania.

Formatowanie czasu i filtrowanie już pobranych deklaracji nie wykonuje RPC.
Status kopii zapasowej, liczniki przypisanej poczty i lista rozmów przy
przełączaniu zakładki pobierają się w tle. Ukryty czat sprawdza aktualizacje
najwyżej co 30 s; niezależne powiadomienia czatu zwiększają odstęp z 3 do
30 s przy braku nowych wiadomości. Bez powiadomień serwera nadal potrzebne
jest okresowe odpytywanie, żeby wykrywać zmiany wprowadzone przez inne osoby.

Klient dopuszcza najwyżej dwa równoległe wywołania operacji w tle i trzy
łącznie, pozostawiając miejsce dla akcji użytkownika w limicie serwera.
Operacje nie są ponawiane automatycznie: zapisy pozostają pojedyncze.

## Poczta i kalendarz

`mail_sidebar_snapshot` pobiera konta pracownika, foldery wszystkich tych kont,
zbiorcze liczniki nieprzeczytanych wiadomości i etykiety pracowników jednym
żądaniem. Foldery i liczniki są czytane zapytaniami z ANY(lista kont), zamiast
pętli żądań na każde konto. Panel boczny odświeża się w tle, zachowując bieżący
widok podczas oczekiwania. Ładowanie treści wiadomości pozostaje osobnym odczytem.

`calendar_view_snapshot` pobiera wydarzenia oraz źródła dla wspólnego zakresu
widoku i miniatury miesiąca. Informacja o udostępnieniu jest częścią zapytania
listy wydarzeń. Rysowanie nie odpytuje dostępu każdego wydarzenia. Wyszukiwanie,
wybór źródeł i oznaczenia dni korzystają z pobranego zestawu; zmiana zakresu lub
ponowne wejście/odświeżenie pobiera aktualne dane w tle. Nie jest to trwały cache.
Sprawdzanie konfiguracji synchronizacji Google/CalDAV również odbywa się w tle.

Wymagana jest aktualizacja workera/API oraz klientów: nowe zbiorcze operacje
muszą być dostępne na serwerze. Pomiary z instalacji są potrzebne do oceny
pozostałego czasu ładowania i ewentualnego kosztu zapytań bazy danych.

## Zgodność klienta ze starszym workerem

Logowanie i wznowienie sesji przekazują obsługiwane `ui_features`. Klient używa
zbiorczych odczytów poczty/kalendarza dopiero, gdy serwer je zgłosi. Brak pola
w starszym serwerze uruchamia dotychczasowe autoryzowane odczyty, wykonywane
w zadaniu tła. Nie obejmuje to ponawiania zapisów ani obchodzenia odmowy dostępu:
po odmowie uprawnień przez obsługiwaną operację nie ma próby inną ścieżką.
W logu klienta jednorazowy `office_legacy_view_transport` wskazuje, że aktualizacja
workera/API jest potrzebna dla pełnego przyspieszenia.

Błędy odczytu są widoczne w statusie i logach jako `mail_sidebar_failed` lub
`calendar_snapshot_failed` z typem i bezpiecznym komunikatem błędu. Nie wymagają
odczytywania tokenów, kluczy ani danych logowania. W razie problemu potrzebne
są wpisy z tej samej próby otwarcia widoku po stronie klienta i serwera.

## Firmy i masowa obsługa faktur

Lista firm wyświetla rekordy przez model Qt. Zmiana filtra aktualizuje wiersze
według identyfikatorów firm i zachowuje zaznaczenie, jeśli firma nadal pasuje.
Karta firmy pobiera historię zapisów czasu w tle po otwarciu zakładki Czasomierz.

W trybie HTTPS dane podstawowe karty firmy są pobierane jednym żądaniem w tle.
Obejmują firmę, słowniki, pracowników, pełnomocnictwa i cenniki. Kontakty,
wspólnicy, deklaracje, reguły pocztowe i notatki są odczytywane przy otwieraniu
odpowiednich zakładek. Okno ponownie wykorzystuje już pobrane informacje o
kontaktach i wspólnikach. Pierwszy odczyt poczty zawiera nadawców i foldery
wybranego konta. Przy zmianie konta nadawcy i foldery pobierają się razem.

W teście 1000 syntetycznych firm na lokalnym HTTPS i PostgreSQL 17 otwarcie
wszystkich kart trwało 241,9 s przed zmianą i 21,9–25,1 s po zmianie. Liczba
żądań na otwarcie spadła z 28 do 1. W próbce 30 kart przejście przez wszystkie
zakładki zajęło 8,15 s przed zmianą i 1,93 s po zmianie, z 6 żądaniami na kartę.
Środowisko Linux miało limit 2 vCPU i 8 GiB pamięci. Pomiar obejmował tworzenie
interfejsu i dostarczenie danych. Opóźnienia między VM należy zmierzyć osobno.

Worker z funkcją `invoice_selection` udostępnia zbiorczy odczyt wybranych
faktur przed wystawieniem, wysyłką do KSeF, wysyłką PDF i eksportem. Starszy
worker obsługuje te akcje przez dotychczasowe odczyty pojedynczych faktur.
Eksport Saldeo pobiera dane firm jednym zapytaniem PostgreSQL.

## Dalsze pomiary PostgreSQL i Enovy

W logu workera wpis `enova_source_read` podaje osobno czas otwarcia połączenia
ODBC i czas odczytu z SQL Server. Wpis dla operacji dłuższych niż 250 ms ma
poziom INFO. Krótsze odczyty są dostępne przy poziomie DEBUG.

W PostgreSQL `pg_stat_statements` pozwala ustalić liczbę wywołań i łączny czas
zapytań. Analiza planu najdroższych odczytów za pomocą `EXPLAIN (ANALYZE, BUFFERS)`
na kopii danych pomaga dobrać indeksy do faktycznych filtrów. W dalszym rozwoju
warto rozważyć wyodrębnienie historii i kolejki zapisanych dziś pod kluczami
tekstowymi w `app_settings`. Kolumny firmy, miesiąca, źródła i stanu umożliwią
indeksowanie zakresów oraz statusów bez przeglądania dokumentów JSON.

## Import i lista rozliczeń MT940

W trybie HTTPS klient odczytuje wskazany plik i wysyła go na serwer workera.
Worker rozpoznaje transakcje, a PostgreSQL zapisuje je zbiorczo. Otwieranie
i odświeżanie listy rozliczeń pobiera dane w tle i buduje podpowiedzi na stanowisku.

W logu klienta `mt940_import_client` podaje łączny czas od rozpoczęcia importu
pliku do odpowiedzi. W logu workera `mt940_import_postgres` podaje liczbę operacji,
czas odczytu i parsowania pliku (`read_parse_s`) oraz czas połączenia i zapisu
w PostgreSQL (`postgres_s`). Zestaw wpisy z tej samej próby według czasu zapisu.
