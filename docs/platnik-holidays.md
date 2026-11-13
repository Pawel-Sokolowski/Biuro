# Płatnik — roczne wakacje składkowe (1.4.6)

## Konfiguracja

W Biuro Worker otwórz **Płatnik** (lub w aplikacji Ustawienia → Płatnik — połączenie).
Dla podanej przez użytkownika instalacji serwer/instancja to
`platnik-srv\SQLEXPRESS`, baza `platnik`. Są to oddzielne pola, nie „podbaza”.
Wprowadź konto SQL z prawem SELECT, hasło oraz sterownik (automatycznie, 17 lub 18).
Jeśli serwer używa własnego certyfikatu, zaznacz odpowiednią opcję. Połączenie
pozostaje szyfrowane. Hasło jest przechowywane zaszyfrowane wspólnym kluczem Biuro.

Sprawdź połączenie i zapisz ustawienia, włącz odczyt Płatnika. Możesz pobrać
wskazany rok ręcznie w tym panelu. W Zadaniach workera włącz osobne zadanie
Płatnik; domyślnie co godzinę odświeża poprzedni i bieżący rok. Starszy rok
pobierz ręcznie. Nie ma zapisu do Płatnika ani wysyłania dokumentów do ZUS.
Nie jest wymagany własny plik SQL ani instalacja Płatnika na workerze.

Pole **Limit zapytania SQL** ma domyślnie 120 sekund i zakres 30–1200 sekund.
Dotyczy pojedynczego zapytania; logowanie ma osobny limit 15 sekund. Zapisane
ustawienie stosuje również automatyczny worker. Podczas ręcznego odczytu widoczny
jest wskaźnik pracy oraz etapy: łączenie, odczyt RWS, walidacja i zapis w Biuro.
Roczny raport pobierany jest zbiorczo, więc wskaźnik nie udaje licznika firm.

Test połączenia używa pól formularza; pobranie roku używa zapisanego połączenia.
Przy niezapisanej zmianie adresu, hasła, certyfikatu lub limitu pobieranie jest
wstrzymane z prośbą o **Zapisz połączenie Płatnika**, aby nie uruchomić importu
na starym adresie po udanym teście nowego.

## Diagnostyka połączenia

Microsoft ODBC Driver 17 i 18 obsługują SQL Server 2017, 2019 i 2022.
Numer sterownika nie musi odpowiadać numerowi wersji serwera. Sterownik musi być
zainstalowany na komputerze uruchamiającym worker, w architekturze aplikacji
(zwykle x64). Opcja Automatycznie wybiera zainstalowany sterownik 18 lub 17.

**Sprawdź połączenie SQL** sprawdza bieżące pola formularza; nie trzeba najpierw
włączać importu. Puste hasło wymaga wcześniej zapisanego hasła Płatnika.
**Pobierz wybrany rok** używa zapisanej konfiguracji, więc po zmianie pól zapisz ją.

Przy błędzie w statusie widoczne są etap operacji, SQLSTATE oraz wskazówka.
Kliknij **Pokaż szczegóły błędu SQL**, a w oknie rozwiń szczegóły, aby skopiować
komunikat sterownika. Hasło (również zapisane z escapowaniem ODBC) i zaszyfrowana
wartość hasła są usuwane z diagnostyki. Nie przesyłaj haseł ani klucza.

- `28000` / `18456`: login/hasło lub tryb uwierzytelniania SQL Server.
- `08001` lub przekroczenie czasu: nazwa hosta, instancja, dostęp TCP/IP, zapora.
  Dla instancji nazwanej potrzebny jest dostęp do SQL Browser albo adres
  `host,port` z rzeczywistym portem tej instancji (nie zakładaj portu 1433).
- Błąd SSL/certyfikatu: opcja zaufania własnemu certyfikatowi lub konfiguracja TLS.
- Poprawny test SQL i błąd odczytu: sprawdź szczegóły zapytania, tabele/kolumny
  oraz prawo SELECT konta w bazie `platnik`.

Jeżeli SSMS łączy się lokalnie przez **Shared Memory**, taki test nie sprawdza
TCP z maszyny workera. Port nasłuchu można odczytać bez zmian w SQL Server:

```sql
EXEC sys.xp_readerrorlog 0, 1, N'Server is listening on';
```

Po potwierdzeniu portu 1433 dla wskazanej instancji można wpisać
`PLATNIK-SRV,1433` zamiast `PLATNIK-SRV\SQLEXPRESS`, z bazą `platnik`.
Omija to rozpoznawanie nazwanej instancji przez SQL Browser. Gdy połączenie nadal
nie działa, sprawdź `Test-NetConnection PLATNIK-SRV -Port 1433` na komputerze workera.

## Widok

**Kadry → Sprawdzanie → Wakacje składkowe — Płatnik (rocznie)** ma własny wybór roku.
Miesiąc jest kolumną sprawy, nie filtrem widoku. W tym raporcie miesięczny
selektor i filtry dokumentów są ukryte. Tabela pokazuje firmę oraz zamówione kolumny:
imiona i nazwiska, kod ubezpieczenia, wysłane powiadomienie, składki opłacane,
miesiąc, oświadczenie, płatność, kto wysłał, decyzja. Osobna, nieedytowalna kolumna
pokazuje status RWS z Płatnika. „Decyzja” pozostaje notatką biura, np. „zawieszony?”
lub „przyznano zwolnienie”; nie jest automatycznym potwierdzeniem decyzji ZUS.

Jeden wiersz odpowiada firmie w wybranym roku, także gdy nie ma jeszcze RWS.
Imiona, bazowe kody ubezpieczenia i miesiące są zbierane z dostępnych wpisów RWS.
Przy kilku osobach/kodach/miesiącach widoczne są wszystkie unikalne wartości;
przycisk **Wpisy źródłowe** pokazuje poszczególne rekordy wraz z rokiem, miesiącem,
kodem bazowym, kodem ulgi i A/N. To również miejsce na sprawdzenie nieprzypisanych
wpisów przez administratora. Nie zastępujemy wpisu N automatycznie innym A.

Dwuklik w jednej z dziewięciu kolumn otwiera edycję. Wpisz np. „Wysłano 09/11/2026”,
„tylko zdrowotna”, „z chorobową”, „bez chorobowej”, „luty”, „sam/sama” lub „KC”.
„Kto wysłał” oznacza inicjały pracownika biura; **Wstaw moje inicjały** uzupełnia
tekst na podstawie imienia i nazwiska zalogowanego pracownika. Samo uzupełnienie
komórki nie wysyła wiadomości. Można wpisać inicjały innej osoby.

Wpisy biura są wyróżnione; szczegóły komórki pokazują autora i czas edycji.
Pozostają zapisane w PostgreSQL oddzielnie dla firmy i roku, także po odczycie
nowego lub pustego RWS. Import nigdy ich nie nadpisuje. Pusty tekst jest świadomym
wyczyszczeniem komórki; **Przywróć dane źródłowe / usuń wpis biura** oddaje pierwszeństwo
danym źródłowym. Edycje różnych komórek z kilku komputerów są łączone. Równoczesna
zmiana tej samej komórki wymaga odświeżenia, zamiast nadpisania cudzej zmiany.

Brak wpisu RWS w odczycie nie oznacza odmowy ani braku uprawnienia. Takie firmy
pozostają widoczne. Bez poprawnego importu wyświetlamy „Nie odczytano roku”.
Najnowsza nieudana próba nie usuwa poprzedniego snapshotu; widok zaznacza nieaktualność.
Przełączenie roku czyści poprzednie wiersze podczas pobierania zapisu, aby nie
pokazywać starego roku pod nowym nagłówkiem. Importy jednego roku mają blokadę
PostgreSQL zapobiegającą równoczesnej pracy kilku wywołań.

## Zakres potwierdzony eksportem

Użytkownik przekazał schemat SQL Server 14.0.1000.169 (SQL 2017). Adapter czyta
`dbo.RWS_ZWOLNIENIE`, powiązania `ID_PLATNIK` z `PLATN_IDENT` oraz dane osoby
z `UBEZP_IDENT` po lokalnym ID lub identyfikatorach ZUS. Historyczne, różniące się
NIP-y lub nazwy nie są rozstrzygane wyborem przypadkowego rekordu; wiersz wymaga
sprawdzenia. Osoby bez jednoznacznych danych pozostają w raporcie z ostrzeżeniem.
Nie odczytujemy PESEL-i ani haseł użytkowników Płatnika.

Znaczenie A/N przyjęto zgodnie z informacją użytkownika:
- A: aktualne zwolnienie;
- N: nieaktualny wpis, nie decyzja odmowna.

Zachowujemy kod oraz wszystkie wpisy, w tym N. Opcjonalny filtr pokazuje firmy z wpisem A.
Nieznane kody nie są mapowane na zgodę/odmowę. Sam wpis RWS nie jest dowodem
pobrania formalnej decyzji/UPO. Kwoty składek RWS są w szczegółach komórki,
wyraźnie jako kwoty źródłowe, nie potwierdzenie zapłaty.

**Obecny adapter nie odczytuje jeszcze powiadomień do klientów, oświadczeń,
faktycznych płatności, nadawcy ani formalnej decyzji/UPO z innych tabel.**
Odpowiednie kolumny są uzupełniane przez biuro; bez wpisu pokazują „Brak danych”.
„Składki opłacane” to opis do wpisania przez biuro, nie wniosek wyciągany z kwot
zwolnienia RWS ani dowód faktycznej zapłaty. Kod bazowy, np. 0510/0590, jest
oddzielony od kodu ulgi, np. 0514/0594. Mimo że
baza ma tabele ZESTAW/ZESTAW_DOKUM i potwierdzenia, ich powiązanie ze zdarzeniami
RWS wymaga osobnego adaptera i weryfikacji znaczenia dokumentów. Nie wywodzimy
nadawcy RWS z użytkownika, który jedynie utworzył kartotekę płatnika.

Kompletne, zweryfikowane dane roczne są atomowo zapisywane jako zaszyfrowany
snapshot w PostgreSQL Biuro. Wpisy biura i informacje o ich autorach również są
szyfrowane wspólnym kluczem. Pracownicy potrzebują dostępu do Sprawdzania oraz
firmy według istniejących reguł. Nieprzypisane NIP-y widzi wyłącznie administrator.
Instalacja nie wymaga zmiany schematu PostgreSQL — używa istniejącego app_settings.

Weryfikację testów i budowy EXE wykonuje GitHub CI. Po wdrożeniu sprawdź test
połączenia, wykonaj odczyt 2026 i porównaj liczbę wpisów źródłowych z podanym
wynikiem (9 × A) w szczegółach źródłowych, bez wyciągania wniosków z liczby firm.
