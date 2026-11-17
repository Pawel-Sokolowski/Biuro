# Deklaracje za cały rok i miesięczne kadry

W **Sprawdzaniu** wybierz **Pobierz deklaracje za cały rok — Enova i Płatnik**,
rok oraz firmy. Zlecenie wykonuje worker na serwerze. Pobiera formularze ZUS
za styczeń–grudzień, IWA/ZSWA za wskazany rok oraz deklaracje podatkowe i JPK
z powiązanych baz Enovy. Poszczególne źródła mają osobne wyniki; błąd jednego
nie oznacza, że drugie zostało odczytane.

Import dodaje wykryte deklaracje do **Ustawienia miesięcznego** firmy. Nie
wyłącza pozostałych obowiązków na podstawie braku dokumentu i nie nadpisuje
ręcznie wskazanych ustawień. Pokazuje źródło wykrycia w ustawieniu miesięcznym.
Rozpoznany wariant VAT-7K/JPK V7K podpowiada rozliczenie kwartalne, z miesięczną
ewidencją JPK; ręcznie wybrany okres ma pierwszeństwo. Jest to uzupełnienie
zakresu deklaracji, bez importowania kwot, liczników dokumentów czy zmiany etapów.
Zapisane informacje nie są dowodem wysłania ani przyjęcia deklaracji.

W **Widoku miesięcznym → Kadry** ostatnia kolumna zawiera kody ubezpieczenia
z dokumentów Płatnika za wybrany miesiąc. Dymek podaje osobę, formularz, NIP
płatnika, dokument źródłowy i datę odczytu. Brak odczytu nie jest kodem zero;
kodów nie przenosi się automatycznie na kolejne miesiące.

Płatnik odczytuje kod z pól `ZUSDRA.XI_1_*` oraz `UBEZP_ZUSRCA`,
`UBEZP_ZUSRSA` i `UBEZP_ZUSRZA` (`III_B_1_*`), gdy metadane potwierdzają ich
obecność. Cztery cyfry tytułu nie są uzupełniane domyślnymi zerami przy braku
danych o emeryturze/rencie lub niepełnosprawności. W spółce cywilnej deklaracje
wspólników pod indywidualnymi NIP-ami trafiają do tej samej firmy, z zachowaniem
NIP-u źródłowego; nie nadpisują deklaracji spółki ani drugiego wspólnika.

Mapowanie pól opisuje publiczna dokumentacja ZUS:
[Struktury danych osobowych Płatnika 1.9](https://ec06-waw1.cache.orange.pl/zus/pobierz/dystrybucja/a1_10_02_002/dokumentacja/Struktury_danych_osobowych_wersja_1.9.pdf),
rozdział 4.3. Prywatne eksporty schematów nie są publikowane.

# Klienci i poczta

Usunięto dawne pole `typ_rozliczenia` z danych firm, list i eksportów oraz filtrów
mailingu. Domyślne „Ryczałt” w tym polu opisywało opłatę biura, a nie podatek
klienta. Migracja usuwa samo stare pole. Cenę nadal wylicza stała opłata z cennika
oraz dopłata za dokumenty ponad limit. Rzeczywista forma opodatkowania klienta,
w tym podatkowy ryczałt, pozostaje odrębną informacją.

Przy odczycie listy folderów program wiąże istniejący folder z klientem, jeśli
pasuje jednoznacznie jego nazwa, nazwa skrócona, NIP albo dane wspólnika.
Uwzględnia kodowanie IMAP i polskie znaki. Tworzy reguły dla głównego adresu,
kontaktów i wspólników. Nie tworzy nowych folderów, nie zastępuje ręcznych
reguł i pomija niejednoznaczne nazwy, wiele pasujących folderów lub adres
wspólny dla różnych firm. Powiązanie powstaje również przy zapisie kontaktów
w kartotece klienta. Reguły służą poczcie przychodzącej i wyborowi kopii
wychodzących; sam zapis powiązania nie przenosi starszych wiadomości.

Mail masowy wysyła odbiorców w kopercie SMTP BCC. Nagłówek BCC nie trafia do
odbiorców. Program zapisuje kopię partii w serwerowym folderze **Wysłane**
oraz osobne kopie w powiązanych folderach klientów; każda kopia klienta zawiera
wyłącznie jego adresy BCC. Adres odrzucony przez SMTP nie jest oznaczany w kopii
jako przyjęty do wysyłki. Po błędzie IMAP raport wskazuje, że wysyłka SMTP się
powiodła, lecz kopia nie została potwierdzona. Nie wolno wtedy ponawiać mailingu
tylko w celu odtworzenia kopii. Odpowiedź SMTP nie dowodzi doręczenia do skrzynki
odbiorczej; produkcyjne doręczenie potwierdzają logi serwera poczty i zwrotki.

## Globalny import bieżącego roku

Opcja **Pobierz wszystkie deklaracje za rok — Enova i Płatnik** jest dostępna:

- w konfiguracji workera, zakładka **Deklaracje — import roczny**;
- w konfiguracji Płatnika i połączenia Enovy;
- w Sprawdzaniu, przycisk pobrania deklaracji za cały rok.

Lista obejmuje wszystkie firmy, niezależnie od aktualnego raportu/filtra w
Sprawdzaniu. Domyślnie zaznaczone są wszystkie; można zmienić rok i zaznaczenie.
Worker czyta Płatnika dla całego wyboru i 12 miesięcy zbiorczo, a Enovę osobno
z każdej przypisanej bazy, z rocznym zakresem. Wymagane są zapisane ustawienia
źródeł i włączone zadanie ręcznych pobrań. Wynik rozróżnia powodzenie obu źródeł.
Dodaje wykryte deklaracje do miesięcznych ustawień firm, zachowując ręczne
wybory. Nie pobiera kwot ani nie zmienia etapów pracy. Brak dokumentu nie
wyłącza obowiązku. Przycisk Płatnika dotyczący RWS jest teraz nazwany wprost
**Pobierz wakacje składkowe (RWS)**, aby nie mylić go z importem deklaracji.

## Automatyczny odczyt Enovy

Automatyczny odczyt następuje po zmianie `etap` lub `etap_kadry` firmy w Biuro.
Zmiana zapisuje żądanie w tej samej transakcji co etap. Ponowny zapis tego
samego etapu lub edycja samej liczby dokumentów nie tworzy żądania.
Worker sprawdza lokalną kolejkę co 30 sekund; bez zmian nie łączy się z SQL
Enovy. Kilka zmian tej samej firmy/miesiąca łączy się w jedno żądanie; sąsiednie
miesiące trafiają do wspólnych odczytów. Odczyt nie obejmuje innych baz firm.
Zmiana podczas odczytu zostaje do następnego przebiegu; błędy nie kasują kolejki.
Włączenie zadania Enovy i połączenia nadal jest wymagane. Bez zmiany etapu
status deklaracji zmieniony później w Enovie wymaga ręcznego odświeżenia.

## e-ZLA raz w tygodniu

Automatyczne pobieranie działa w poniedziałki od ustawionej godziny
(domyślnie 02:00, Europe/Warsaw). Udany przebieg zapisuje poniedziałek tygodnia
w bazie, więc restart workera nie powoduje drugiego pobrania. Błędy i zajęte
blokady pozwalają na ponowienie w poniedziałek z dotychczasowym odstępem zadania.
Gdy worker jest wyłączony przez cały poniedziałek, następny automatyczny termin
to kolejny poniedziałek; pobranie ręczne pozostaje dostępne. Poczta zachowuje
obecny harmonogram i sposób działania.

## Pobierz teraz — bez kolejki

Przyciski pobierania w Sprawdzaniu uruchamiają operację od razu na serwerze
przez API Biura. Nie zależą od głównego przełącznika automatycznych zadań
ani od obsługi wcześniejszych zleceń kolejki. Interfejs pozostaje aktywny,
a serwer wykonuje odczyt z zapisanymi danymi dostępu.

W ustawieniach Płatnika przycisk **Pobierz wszystkie deklaracje za rok z Płatnika**
obejmuje wszystkie zaznaczone firmy, 12 miesięcy DRA/RCA/RSA/RPA/RZA i formularzy
zgłoszeniowych oraz IWA/ZSWA. Nie próbuje łączyć się z Enovą. Odpowiedni przycisk
Enovy pobiera wyłącznie Enovę. Globalny przycisk w konfiguracji workera i
Sprawdzaniu nadal obejmuje oba źródła. Domyślnie wybrany jest bieżący rok i
wszystkie firmy; ręczne ustawienia obowiązków nie są wyłączane ani nadpisywane.
Wynik pokazuje liczbę dokumentów miesięcznych i rocznych Płatnika oraz błędy.

Wyłączenie automatycznego odczytu formularzy nie blokuje jawnie uruchomionego
pobrania dla wybranych firm. Zajęte źródło lub inny trwający import daje
konkretny błąd do ponowienia, bez pozostawiania nowego żądania w kolejce.
Starsze zlecenia kolejki zachowują dotychczasową obsługę. Nowy klient sprawdza
przy logowaniu możliwość `checking_run_now`; starszy serwer wyświetla potrzebę
aktualizacji workera/API zamiast bezskutecznie przyjmować nowy import.

W tabelach Sprawdzania pierwsza kolumna jest przypięta. Nazwa firmy i zaznaczenie
wiersza pozostają widoczne podczas przewijania poziomego; dane i widok przypięty
korzystają z tego samego modelu, bez osobnej kopii wyników.
