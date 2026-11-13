# RCA/DRA z bazy programu Płatnik

Worker używa wbudowanego `queries/declarations.sql`, bez ręcznego wybierania
pliku. Odczytuje standardowe `DOKUMENT`, `ZUSDRA`, `ZUSRCA` i `UBEZP_ZUSRCA`.
Przed odczytem automatycznie sprawdza widoczność wymaganych tabel i kolumn.
Konto SQL potrzebuje SELECT i widoczności ich metadanych; brak wymaganych pól
powoduje błąd, nie zapis pustego wyniku.

Podstawą mapowania są opublikowane przez ZUS
[Struktury danych osobowych programu Płatnik, wersja 1.9](https://pobierz.zus.pl/dystrybucja/a1_10_02_002/dokumentacja/Struktury_danych_osobowych_wersja_1.9.pdf),
rozdział 4.3. Okres odczytu pochodzi z okresu rozliczeniowego dokumentu,
nie z daty jego utworzenia. DRA, nagłówki RCA i pozycje RCA ubezpieczonych
pozostają osobnymi rekordami. Identyfikatory raportów i dokumentów pozostają
widoczne, również dla korekt; najwyższy ID nie jest uznawany automatycznie za
aktualny stan rozliczenia.

## Uruchamianie

W połączeniu Płatnika włącz worker oraz odczyt RCA/DRA. Przy dotychczasowym
włączonym połączeniu RCA/DRA jest domyślnie włączone po aktualizacji.
Zadanie Płatnika odczytuje RCA/DRA za bieżący i poprzedni miesiąc, niezależnie
od rocznego odczytu wakacji składkowych. Błąd jednego adaptera nie blokuje próby
wykonania drugiego. W Kadry → Sprawdzanie jest osobne **Pobierz RCA/DRA z Płatnika**
dla wybranych firm i wybranego/poprzedniego miesiąca. Żądanie wykonuje worker,
z ponowną kontrolą dostępu do firm, nie komputer pracownika.

NIP dokumentu musi jednoznacznie odpowiadać firmie Biuro. Powtórne kartoteki
z tym samym NIP-em lub uszkodzone powiązania nie są łączone po nazwie.
Snapshoty pracowników są szyfrowane wspólnym kluczem aplikacji i workera.
Nie zmieniają danych Enovy, miesięcznych liczników ani odczytów innych firm.
Nieudany odczyt zachowuje wcześniejsze snapshoty.

## Statusy i potwierdzenia

Jeżeli baza udostępnia `DOKUMENT.STATUSPT`, kod źródłowy jest pokazywany przy
dokumencie. Bez zweryfikowanego słownika kodów status wysyłki pozostaje
nieustalony. Dokumentacja tabel nie daje kompletnego słownika tych kodów ani
technicznych powiązań z przesyłkami/potwierdzeniami. To ograniczenie obecnego
adaptera, nie twierdzenie, że baza jest niestandardowa.

Odczyt dokumentu nie potwierdza wysyłki ani przyjęcia przez ZUS. Daty wysyłki,
daty potwierdzenia i UPO nie są wyprowadzane z czasu odczytu, daty wypełnienia
ani litery statusu. Potwierdzenie w Płatniku jest osobnym obiektem; opisuje je
[dokumentacja użytkownika ZUS](https://pobierz.zus.pl/dystrybucja/a1_10_02_002/dokumentacja/Dok_Uzytk_A1_10.02.002_wersja_7.8.pdf),
rozdziały 5.6.8–5.6.9. Pełny odczyt statusu wysyłki i potwierdzenia pozostaje
do uzupełnienia po potwierdzeniu mapowania pól i kodów.

## Własny zamiennik

Opcjonalne pole **RCA/DRA — zamiennik SQL** przyjmuje pełną ścieżkę pliku na
serwerze workera. Pozostawienie pustego pola przywraca adapter wbudowany.
Własny plik przechowuj poza folderem instalacyjnym. Przyjmuje dwa parametry
ODBC: początek pierwszego miesiąca włącznie i koniec ostatniego wyłącznie.
Zwraca kolumny `form`, `source_id`, `payer_id`, `nip`, `report_id`, `miesiac`
(`YYYY-MM`), `employee_id`, `employee_name`, `raw_status`, `valid_link` (1).
Nagłówki nie mają pracownika, pozycja imienna wymaga ID i nazwy. Brak wiersza
oznacza brak dokumentu w tym odczycie, a nie status „niewysłany”.

Instalator zawiera SQL automatycznie. Wersja aplikacji i workera pozostaje 1.4.6.
