# Formularze ZUS z bazy programu Płatnik

Worker używa wbudowanego `queries/declarations.sql`, bez ręcznego wybierania
pliku. Korzysta z `DOKUMENT` oraz standardowych tabel formularzy i pozycji.
Przed odczytem automatycznie sprawdza widoczność wymaganych tabel i kolumn.
Konto SQL potrzebuje SELECT i widoczności ich metadanych; brak wymaganych pól
powoduje błąd, nie zapis pustego wyniku.

Mapowanie zweryfikowano z rozdziałem 4.3 dokumentacji ZUS dla Płatnika
od wersji 10.02.002. [Struktury danych osobowych, wersja 1.6](https://pobierz.zus.pl/dystrybucja/a1_10_02_002/dokumentacja/Struktury_danych_osobowych_wersja_1.6.pdf)

| Formularz | Tabele | Okres |
| --- | --- | --- |
| DRA | `ZUSDRA` | `I_2_2OKRESDEKLAR` — miesiąc rozliczenia |
| RCA/RSA/RZA | `ZUS…` i `UBEZP_ZUS…` | `I_1_2OKKROZL` lub `I_1_2OKRROZL` — miesiąc rozliczenia |
| RPA | `ZUSRPA` i `UBEZP_ZUSRPA` | `I_1_2OKRROZL` — miesiąc rozliczenia |
| ZUA | `ZUSZUA` | `XV_1_DATAWYPEL` — data wypełnienia |
| ZZA | `ZUSZZA` | `XII_1_DATAWYPEL` — data wypełnienia |
| ZWUA/ZCNA | `ZUSZWUA` / `ZUSZCNA` | `VI_1_DATAWYPEL` — data wypełnienia |
| ZIUA | `ZUSZIUA` | `V_1_DATAWYPEL` — data wypełnienia |
| ZCZA | `ZUSZCZA` | `VIII_1_DATAWYPEL` — data wypełnienia |
| ZAA/ZBA | `ZUSZAA` / `ZUSZBA` | `IX_1_DATAWYPEL` — data wypełnienia |
| ZFA/ZPA | `ZUSZFA` / `ZUSZPA` | `XI_3_DATAWYPEL` / `IX_3_DATAWYPEL` — data wypełnienia |
| ZIPA/ZWPA | `ZUSZIPA` / `ZUSZWPA` | `IV_1_DATAWYPEL` — data wypełnienia |
| RIA/OSW | `ZUSRIA` / `ZUSOSW` | `XII_1_DATAWYPEL` / `III_1_DATAWYPEL` — data wypełnienia |
| IWA | `ZUSIWA` | `I_1_2OKRINFOR` — rok informacji |
| ZSWA | `ZUSZSWA` i `UBEZP_ZUSZSWA` | `I_1_2OKRESZGL` — rok zgłoszenia |

ZCZA, ZAA, ZBA, ZFA, ZPA, ZIPA, ZWPA, RIA i OSW są odczytywane po potwierdzeniu
widoczności ich tabel i wymaganych kolumn. Są to dokumenty dostępne do przeglądu
w okresie wypełnienia. ZIPA jest dopasowywany po aktualnym NIP z bloku III,
z użyciem bloku II przy pustym NIP. [Instrukcja formularza ZUS ZIPA](https://www.zus.pl/documents/10182/167567/ZUS_ZIPA.pdf/b6e51425-c990-464b-9abf-9276a0117591)

Dla ZCNA i ZCZA odczytywany jest nagłówek związany z ubezpieczonym. Nagłówki i pozycje ubezpieczonych
pozostają osobnymi rekordami. Identyfikatory raportów i dokumentów pozostają
widoczne, również dla korekt. W pozycjach RPA kod tytułu ubezpieczenia
pochodzi z bloku III.A.5, w RCA, RSA i RZA z III.B.1, w DRA z XI.1,
w ZUA i ZZA z V.1, a w ZWUA z IV.1, o ile odpowiednie pola są widoczne.

Osobne obszary bez odczytu przez adapter deklaracji obejmują historyczne RGA
i inne dawne raporty oraz wspólną tabelę DRA/RCA cz. II. Komplety pobrane z ZUS
z rozdziału 4.4 i archiwalne bazy Płatnika stanowią odrębne źródła.
Kartoteki, dane członków rodziny i rachunki bankowe pozostają w Płatniku.

## Uruchamianie

W połączeniu Płatnika włącz worker oraz odczyt formularzy ZUS. Przy dotychczasowym
włączonym połączeniu formularze są domyślnie włączone po aktualizacji.
Zadanie Płatnika odczytuje rozliczenia i zgłoszenia za bieżący i poprzedni miesiąc,
IWA/ZSWA za bieżący i poprzedni rok, niezależnie od rocznych wakacji składkowych.
Błąd odczytu miesięcznego nie blokuje próby odczytu rocznego i odwrotnie.
W Kadry → Sprawdzanie jest **Pobierz formularze ZUS z Płatnika**
dla wybranych firm i wybranego/poprzedniego miesiąca. Żądanie wykonuje worker,
z ponowną kontrolą dostępu do firm. Wbudowany adapter zawęża odczyt na SQL Server
do przypisanych NIP-ów jednym parametrem. Worker zapisuje wyniki w PostgreSQL partiami
w jednej transakcji odczytu. Formularze zdarzeniowe pojawiają się w miesiącu wypełnienia.

IWA i ZSWA mają osobne raporty roczne z wyborem roku oraz przyciskiem
**Pobierz IWA/ZSWA z Płatnika**. Lista firm wynika z rocznego setupu tego roku;
nie z ustawień miesięcznych i nie z tego, czy dokument już istnieje. Snapshot
jest zapisany pod rokiem informacji, bez sztucznego przypisania do grudnia lub
stycznia. Odczyt nie oznacza ręcznych pozycji Zakończenia roku jako wykonanych.

NIP dokumentu musi jednoznacznie odpowiadać firmie Biuro. Powtórne kartoteki
z tym samym NIP-em lub uszkodzone powiązania nie są łączone po nazwie.
Pozycje imienne muszą mieć także własny rekord `DOKUMENT` wskazujący tego samego
płatnika i ten sam dokument nadrzędny (`ID_DOK_NAD`). Brak rekordu lub sprzeczne
powiązanie kończy odczyt błędem i zachowuje poprzedni snapshot. Surowy STATUSPT
pozycji pochodzi z jej własnego rekordu DOKUMENT; nie jest kopiowany z nagłówka.
Snapshoty pracowników są szyfrowane wspólnym kluczem aplikacji i workera.
Nie zmieniają danych Enovy, miesięcznych liczników ani odczytów innych firm.
Nieudany odczyt zachowuje wcześniejsze snapshoty.

## Składki ZUS (miesięcznie)

Do sprawdzania statusów zestawów z interfejsu Płatnika jest dostępny
[osobny agent Windows](gui-agent.md).

W **Kadry → Sprawdzanie → Raport → Składki ZUS (miesięcznie)** wybierz
miesiąc i zakres. **Cała firma (DRA)** odczytuje deklarację pod NIP-em
firmy. **Właściciel lub wspólnik** odczytuje RCA dopasowane do osoby w
rejestrze Płatnika. Raport może użyć DRA z jednym ubezpieczonym i jego
tytułem działalności, gdy płatnik rozlicza wyłącznie siebie.
Wspólnicy spółek cywilnych są widoczni w osobnych wierszach według NIP-u.

Raport pokazuje składki społeczne z podziałem na emerytalne, rentowe,
chorobowe i wypadkowe, składkę zdrowotną obciążającą płatnika lub
ubezpieczonego oraz FP/FS, FGŚP i FEP wykazane na DRA. Sumy społeczne
obejmują wszystkie źródła finansowania, w tym budżet państwa podczas
wakacji składkowych. „—” wskazuje brak kwoty w odczycie lub na formularzu.
Raport korzysta z ostatniego identyfikatora w seriach 01–39 i 40–49 oraz
rozpatruje korekty RCA według tytułu ubezpieczenia. Status wysyłki jest
widoczny obok numeru formularza. Kwoty pochodzą z deklaracji.

Starsze miesiące uzupełnij przez **Pobierz formularze ZUS z Płatnika**.
Własne zapytanie miesięczne może zwracać opcjonalne kolumny
`contribution_health`, `contribution_pension`, `contribution_disability`,
`contribution_sickness`, `contribution_accident`, `contribution_labour_fund`,
`contribution_guaranteed_benefits`, `contribution_bridging_pensions` i
`contribution_insured_count` (liczba ubezpieczonych na DRA). Zapytanie może
też zwracać `filing_status_raw`, `sent_at`, `upo` i `upo_at`; bez tych kolumn
status i potwierdzenie pochodzą z wbudowanego połączenia z zestawem Płatnika.

## Statusy i potwierdzenia

Adapter łączy dokumenty z `ZESTAW_DOKUM` i `ZESTAW`, jeśli widoczne są
wymagane kolumny. Pobiera z zestawu źródłowy kod `STATUSPT`, daty `DATA_WYS`
i `DATA_POTW` oraz identyfikator potwierdzenia `ID_POTW`. Gdy dokument należy
do kilku zestawów, używany jest najnowszy według daty wysyłki. UPO i daty
pochodzą z zapisanych pól Płatnika, nie z czasu odczytu ani daty wypełnienia.

Kod zestawu jest pokazywany dosłownie; nie mapujemy go na „przyjęty” lub
„odrzucony” bez zweryfikowanego słownika. `ID_POTW` jest numerem UPO;
jeśli Płatnik przechowuje ten numer, Biuro oznacza zestaw jako
`Potwierdzony (UPO)` niezależnie od nieznanego kodu `STATUSPT`. `DATA_POTW`
jest datą z UPO. Sam `DOKUMENT.STATUSPT` nie jest dowodem przyjęcia. Widok UPO
w Płatniku może zawierać dodatkowe dane poza numerem i datą.

## Powiązania z Płatnikiem

Po zapisaniu połączenia otwórz **Płatnik → Powiązania z Płatnikiem** jako
administrator. Lista pokazuje przypadki wymagające sprawdzenia i zapisane
zatwierdzenia. Możesz też wyświetlić dopasowania automatyczne i wyszukać firmę
po nazwie lub NIP-ie.

1. Wybierz firmę i NIP płatnika. Dla spółki cywilnej osobne pozycje obejmują
   również NIP-y wspólników.
2. Wybierz kartotekę płatnika. Biuro użyje jej przy pobieraniu deklaracji
   miesięcznych, zgłoszeń oraz IWA i ZSWA.
3. Gdy u wybranego płatnika są dwie kartoteki właściciela lub wspólnika,
   wybierz właściwą według identyfikatora i dat zgłoszeń. Ta decyzja dotyczy
   rejestru kodów i okresów ubezpieczenia oraz kwot imiennych RCA.
4. Zatwierdź powiązanie. Decyzja obowiązuje przy kolejnym odczycie. Możesz ją
   zmienić albo cofnąć w tym oknie.

Kandydaci pochodzą z zapisanej bazy Płatnika i odpowiadają NIP-owi wybranej
firmy albo wspólnika. Jeśli ten NIP figuruje przy kilku firmach w Biuro, sprawdź
ich kartoteki przed zatwierdzeniem. Gdy baza lub wybrana kartoteka się zmieni,
Biuro zgłosi powiązanie do ponownej weryfikacji i zachowa poprzednie dane tej
firmy. Historia decyzji jest zapisywana w Biuro. Wybór kartoteki jest operacją
odczytową po stronie Płatnika.

## Kody i terminy ulg

Miesięczny odczyt uzupełnia kody z dokumentów okresami właścicieli z rejestru
`UBEZP_PODLEGANIE`. Płatnik i ubezpieczony są łączeni wewnątrz SQL Server
po swojej kartotece i identyfikatorze. PESEL służy do dopasowania w Płatniku.
Do Biuro trafia zaszyfrowany zestaw NIP-ów, kodów i dat obowiązywania.
Źródłowa data wyrejestrowania jest pierwszym dniem poza okresem.
Przy niejednoznacznej kartotece lub błędnym okresie Biuro zachowuje poprzedni
rejestr tej firmy, zapisuje poprawne firmy i pokazuje wynik częściowy.

W widoku miesięcznym **Kadry** kolumna **Ulga ZUS do** pokazuje źródłową datę
lub prognozę, z oznaczeniem jej pochodzenia. Dla ulgi na start i składek
preferencyjnych można wykorzystać zapisaną datę z kartoteki. Prognoza obejmuje
6 lub 24 pełne miesiące według zapisanych okresów. Przy możliwych przerwach
w tych tytułach potrzebna jest data z kartoteki. [Składki preferencyjne – ZUS](https://www.zus.pl/pl/firmy/rozliczenia-z-zus/skladki-preferencyjne)

Mały ZUS Plus 0590/0592 od 2026 r. ma prognozę w standardowym limicie 36
miesięcy w okresie 60 miesięcy prowadzenia działalności. Biuro wyznacza ją
na podstawie zapisanych okresów ubezpieczenia, licząc miesiąc z choć jednym
dniem i pomijając wykorzystanie sprzed 2026 r. Uprawnienie wymaga osobnej
weryfikacji warunków rocznych, kompletności historii i ewentualnego dodatkowego
limitu 12 miesięcy. [Mały ZUS Plus od 2026 r. – ZUS](https://www.zus.pl/pl/-/%E2%80%9Ema%C5%82y-zus-plus-nowe-zasady-od-2026-r.)

Jeśli kartoteka zawiera datę końca Małego ZUS Plus dla tego płatnika, kolumna
pokazuje datę z kartoteki. Różniąca się prognoza jest widoczna w szczegółach.

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

Instalator zawiera adapter SQL.


Workflow **Import SQL Server** wykonuje również wygenerowane zapytania Płatnika
na SQL Server 2022 przez konto SELECT-only. Syntetyczne fixture obejmują wszystkie
miesięczne formularze i zgłoszenia, granice dat, IWA/ZSWA, pozycje imienne,
brakujące i sprzeczne powiązania, niepoprawne okresy, własne kody pozycji oraz
warianty klucza ID/ID_DOKUMENT, brak opcjonalnego STATUSPT oraz wybór NIP-ów
bezpośrednio przez SQL Server. Testy używają syntetycznych danych.
