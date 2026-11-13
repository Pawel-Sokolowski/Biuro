# Formularze ZUS z bazy programu Płatnik

Worker używa wbudowanego `queries/declarations.sql`, bez ręcznego wybierania
pliku. Korzysta z `DOKUMENT` oraz standardowych tabel formularzy i pozycji.
Przed odczytem automatycznie sprawdza widoczność wymaganych tabel i kolumn.
Konto SQL potrzebuje SELECT i widoczności ich metadanych; brak wymaganych pól
powoduje błąd, nie zapis pustego wyniku.

Podstawą mapowania są opublikowane przez ZUS
[Struktury danych osobowych programu Płatnik, wersja 1.9](https://pobierz.zus.pl/dystrybucja/a1_10_02_002/dokumentacja/Struktury_danych_osobowych_wersja_1.9.pdf),
rozdział 4.3. Dołączony katalog mapuje:

| Formularz | Tabele | Okres |
| --- | --- | --- |
| DRA | `ZUSDRA` | `I_2_2OKRESDEKLAR` — miesiąc rozliczenia |
| RCA/RSA/RZA/RPA | `ZUS…` i `UBEZP_ZUS…` | `I_1_2OKRROZL` — miesiąc rozliczenia |
| ZUA | `ZUSZUA` | `XV_1_DATAWYPEL` — data wypełnienia |
| ZZA | `ZUSZZA` | `XII_1_DATAWYPEL` — data wypełnienia |
| ZWUA/ZCNA | `ZUSZWUA` / `ZUSZCNA` | `VI_1_DATAWYPEL` — data wypełnienia |
| ZIUA | `ZUSZIUA` | `V_1_DATAWYPEL` — data wypełnienia |
| IWA | `ZUSIWA` | `I_1_2OKRINFOR` — rok informacji |
| ZSWA | `ZUSZSWA` i `UBEZP_ZUSZSWA` | `I_1_2OKRESZGL` — rok zgłoszenia |

Daty wypełnienia zgłoszeń nie są datami wysyłki ani datami rozpoczęcia lub
zakończenia ubezpieczenia. Dla ZCNA odczytywany jest nagłówek związany z
ubezpieczonym, bez danych członków rodziny. Nagłówki i pozycje ubezpieczonych
pozostają osobnymi rekordami. Identyfikatory raportów i dokumentów pozostają
widoczne, również dla korekt; najwyższy ID nie jest uznawany automatycznie za
aktualny stan rozliczenia.

## Uruchamianie

W połączeniu Płatnika włącz worker oraz odczyt formularzy ZUS. Przy dotychczasowym
włączonym połączeniu formularze są domyślnie włączone po aktualizacji.
Zadanie Płatnika odczytuje rozliczenia i zgłoszenia za bieżący i poprzedni miesiąc,
IWA/ZSWA za bieżący i poprzedni rok, niezależnie od rocznych wakacji składkowych.
Błąd odczytu miesięcznego nie blokuje próby odczytu rocznego i odwrotnie.
W Kadry → Sprawdzanie jest **Pobierz formularze ZUS z Płatnika**
dla wybranych firm i wybranego/poprzedniego miesiąca. Żądanie wykonuje worker,
z ponowną kontrolą dostępu do firm, nie komputer pracownika. Zgłoszenia nie są
obowiązkiem tworzenia dokumentu co miesiąc: pusty, udany odczyt nie sygnalizuje zaległości.

IWA i ZSWA mają osobne raporty roczne z wyborem roku oraz przyciskiem
**Pobierz IWA/ZSWA z Płatnika**. Lista firm wynika z rocznego setupu tego roku;
nie z ustawień miesięcznych i nie z tego, czy dokument już istnieje. Snapshot
jest zapisany pod rokiem informacji, bez sztucznego przypisania do grudnia lub
stycznia. Odczyt nie oznacza ręcznych pozycji Zakończenia roku jako wykonanych.

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

Opcjonalne pola zamiennika SQL dla rozliczeń/zgłoszeń oraz IWA/ZSWA przyjmują pełną ścieżkę pliku na
serwerze workera. Pozostawienie pustego pola przywraca adapter wbudowany.
Własny plik przechowuj poza folderem instalacyjnym. Przyjmuje dwa parametry
ODBC: początek pierwszego okresu włącznie i koniec ostatniego wyłącznie.
Zwraca kolumny `form`, `source_id`, `payer_id`, `nip`, `report_id`, `miesiac`
(`YYYY-MM`), `employee_id`, `employee_name`, `raw_status`, `valid_link` (1).
Zamiennik roczny ma ten sam kontrakt, ale zamiast `miesiac` zwraca `rok` (`YYYY`).
Domyślny SQL jest szablonem uzupełnianym przez worker wyłącznie dołączonym
katalogiem nazw, po weryfikacji metadanych. Nie uruchamiaj samego szablonu w SSMS.
Nagłówki nie mają pracownika, pozycja imienna wymaga ID i nazwy. Brak wiersza
oznacza brak dokumentu w tym odczycie, a nie status „niewysłany”.

Instalator zawiera SQL automatycznie. Wersja aplikacji i workera pozostaje 1.4.6.
