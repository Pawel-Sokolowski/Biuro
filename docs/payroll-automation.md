# Płatnik i e-ZLA — zakres do dopasowania na stanowisku Windows

## Ustalony proces biura

- Płatnik ma bazę Microsoft SQL Server.
- Podpisy CenCert i Certum na USB/kartach; podpisywanie odbywa się na stanowisku użytkownika.
- Poprzedni Płatnik Manager obsługiwał okna programu, a nie tylko bazę SQL.
- e-ZLA: raporty XML generowane na PUE/eZUS, ręcznie pobierane i przekazywane do opcji importu e-ZLA w Enovie.
- Docelowo Kadry → Sprawdzanie pokazuje firmę, pracownika, stan wysyłki/potwierdzenia oraz wynik importu zwolnień.

## Stan implementacji

Aktualna implementacja Sprawdzania obsługuje odczyt statusów i UPO z
konfigurowalnego, zweryfikowanego zapytania. Pokazuje pracowników osobno i ma
filtry deklaracji do wysłania / wysłanych / przyjętych. Nie zawiera jeszcze
sterowania Płatnikiem, automatycznego pobierania z eZUS ani importu XML do Enovy.
Etykieta źródła `platnik` nie uruchamia osobnego połączenia z bazą Płatnika.
Dodano lokalny podgląd raportu e-ZLA XML opisany poniżej.

## Docelowy przebieg

**Wysyłka:** użytkownik wybiera firmę i konkretny zestaw, sprawdza podsumowanie,
a lokalny moduł Windows otwiera ten sam zestaw w Płatniku i uruchamia istniejący
wybór certyfikatu. Obsługę karty i PIN pozostawia oprogramowaniu podpisu.
Automatyzacja musi sprawdzić tożsamość firmy i zestawu przed wysyłką, wykrywać
nieoczekiwane okna i zatrzymywać niepewne operacje. Po niejednoznacznym wyniku
najpierw odczytuje stan zestawu, zanim dopuści ponowną próbę. „Kliknięto Wyślij”
nie jest równoznaczne z przyjęciem przez ZUS. Worker odświeża potwierdzenia.
Lokalna obsługa okien wymaga aktywnej sesji Windows; nie jest zadaniem usługi
serwerowej bez interaktywnego pulpitu.

**e-ZLA:** pobranie gotowego raportu XML z konta eZUS z właściwymi uprawnieniami,
rozpoznanie firmy i pracownika, wskazanie przypisanej bazy Enovy i uruchomienie
istniejącego importera Enovy. Dziennik powinien rozróżniać pobranie, kolejkę,
ukończony import oraz brak dopasowania/błąd. Ponowne pobranie nie może tworzyć
duplikatów. Sposób traktowania korekt i anulowań musi odpowiadać formatowi raportu
i zachowaniu importera. Samo uruchomienie importu nie potwierdza dodania nieobecności.
Nie należy zastępować importera bezpośrednim zapisem do tabel Enovy.

## Potrzebne do działającego adaptera

1. Wersja Płatnika i przebieg okien wyboru zestawu, certyfikatu, wysyłki i wyniku.
2. Weryfikacja tabel/statusów Płatnika dla odczytu potwierdzeń.
3. Widok opcji importu e-ZLA w używanej wersji Enovy oraz wynik próbnego importu
   raportu z fikcyjnymi danymi. Struktura raportu została już przekazana.
   Żadnych haseł, PIN-ów ani rzeczywistych danych zdrowotnych.
4. Przebieg pobierania wygenerowanych raportów z eZUS i sposób logowania na
   stanowisku. Uwierzytelnienia nie można wywnioskować z posiadania podpisu USB.

Dokument zapisuje wymagania; nie potwierdza dostępności API ani ukończenia adapterów.

## Podgląd e-ZLA XML w Biuro

W **Kadry → Sprawdzanie** wybierz **Raport: e-ZLA**, następnie
**Wczytaj raporty e-ZLA XML z ZUS**. Można wskazać raporty wielu firm jednocześnie.
Wybierz oryginalny raport, z korzeniem `raportEzla`, nagłówkiem `daneRaportu`
i powtarzanymi elementami `dokumentyEzla`, zgodnie z przekazanym przykładem.
Nie wklejaj treści skopiowanej z instrukcji PDF: cyfry stron, wielokropki,
powtórzone pola lub niedomknięte znaczniki nie są naprawiane automatycznie.

NIP z `daneRaportu/platnik/nip` musi jednoznacznie wskazywać dostępną firmę Biuro.
Nazwa bazy pochodzi z dotychczasowego mapowania Enovy, nigdy z nazwy pliku.
Brak mapowania jest widoczny w podsumowaniu i nie uruchamia innej bazy.
Lista pokazuje pracownika **z raportu**, zamaskowany identyfikator, serię/numer,
status, datę wystawienia oraz okres zwolnienia. Nie potwierdza dopasowania
pracownika w Enovie. Brak PESEL nie prowadzi do dopasowania po nazwisku.

Anulowane, skorygowane, nieznane statusy, sprzeczne oznaczenie anulowania,
nieprawidłowe okresy i powtórzone identyfikatory są oznaczone do sprawdzenia.
Nie wnioskujemy powiązania korekty z nowym zwolnieniem wyłącznie z dat lub nazwiska.
Powtórzone pozycje w jednym raporcie pozostają widoczne, zamiast cicho znikać.
To nie jest jeszcze rejestr importów ani deduplikacja między kolejnymi raportami.

Podgląd nie zapisuje zawartości raportu w bazie Biuro, nie przesyła go do Enovy,
nie pobiera raportów z eZUS i nie oznacza dokumentów jako zaimportowane.
**Wyczyść wczytane raporty** usuwa lokalne odczyty; lista firm pozostaje
z oznaczeniem braku raportu. Raporty można doładowywać partiami (do 20 plików);
ponowne wczytanie tych samych danych nie powiela pozycji w podglądzie.
Zestawienie stosuje miesiąc i filtry firm ze Sprawdzania. Raport bez pozycji
w wybranym miesiącu nie jest potwierdzeniem kompletności danych ZUS. Limity: 10 MB i 20 000 dokumentów;
liczba dokumentów musi odpowiadać nagłówkowi. Parser nie ładuje DTD ani encji
zewnętrznych i odrzuca niepoprawny XML. Do działania używa istniejącej zależności
lxml, bez nowego pakietu instalacyjnego.

Rozpoznanie struktury raportu jest gotowe na podstawie przykładu. Do następnego
etapu pozostaje dopasowanie pobierania z eZUS oraz rzeczywistej opcji importu
w używanej Enovie i sprawdzenie jej raportu wynikowego (w tym anulowań/korekt).
