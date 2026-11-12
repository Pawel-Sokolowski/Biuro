# Płatnik i e-ZLA — zakres do dopasowania na stanowisku Windows

## Ustalony proces biura

- Płatnik ma bazę Microsoft SQL Server.
- Podpisy CenCert i Certum na USB/kartach; podpisywanie odbywa się na stanowisku użytkownika.
- Poprzedni Płatnik Manager obsługiwał okna programu, a nie tylko bazę SQL.
- e-ZLA: raporty XML generowane na PUE/eZUS, ręcznie pobierane i przekazywane do opcji importu e-ZLA w Enovie.
- Docelowo Sprawdzanie → Kadry pokazuje firmę, pracownika, stan wysyłki/potwierdzenia oraz wynik importu zwolnień.

## Stan implementacji

Aktualna implementacja Sprawdzania obsługuje odczyt statusów i UPO z
konfigurowalnego, zweryfikowanego zapytania. Pokazuje pracowników osobno i ma
filtry deklaracji do wysłania / wysłanych / przyjętych. Nie zawiera jeszcze
sterowania Płatnikiem, automatycznego pobierania z eZUS ani importu XML do Enovy.
Etykieta źródła `platnik` nie uruchamia osobnego połączenia z bazą Płatnika.

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
3. Przykładowy raport e-ZLA XML z fikcyjnymi danymi oraz widok opcji importu
   w używanej wersji Enovy. Żadnych haseł, PIN-ów ani rzeczywistych danych zdrowotnych.
4. Przebieg pobierania wygenerowanych raportów z eZUS i sposób logowania na
   stanowisku. Uwierzytelnienia nie można wywnioskować z posiadania podpisu USB.

Dokument zapisuje wymagania; nie potwierdza dostępności API ani ukończenia adapterów.
